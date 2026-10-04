package claude

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"reflect"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func fastBody(e *env, session string) []byte {
	b := e.body(msg{Session: session})
	return append(bytes.TrimSuffix(b, []byte("}")), []byte(`,"speed":"fast"}`)...)
}

func TestFastModeUpstream401IsNeverAnsweredWith401(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Status: 401})

	r := e.send(msg{Session: sessionID(1), Body: fastBody(e, sessionID(1))})
	next := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusServiceUnavailable || !strings.Contains(r.ErrMsg, "claude-router login") {
		t.Fatalf("fast-mode 401 reached the client as %d %q, want 503 naming the login", r.Status, r.ErrMsg)
	}
	if next.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 2 {
		t.Fatalf("next request got %d after %d attempts, want 503 with no attempt on the account that needs login", next.Status, len(e.upstream.inference()))
	}
}

func TestFastModeOverloadIsRetried(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Status: 529})

	r := e.send(msg{Session: sessionID(1), Body: fastBody(e, sessionID(1))})

	if r.Status != 200 || len(e.upstream.inference()) != 2 {
		t.Fatalf("fast-mode 529 got %d after %d attempts, want 200 after a retry", r.Status, len(e.upstream.inference()))
	}
}

func TestFastModeConnectionFailureIsRetried(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Drop: true})

	r := e.send(msg{Session: sessionID(1), Body: fastBody(e, sessionID(1))})

	if r.Status != 200 || len(e.upstream.inference()) != 2 {
		t.Fatalf("fast-mode connection failure got %d after %d attempts, want 200 after a retry", r.Status, len(e.upstream.inference()))
	}
}

func TestRequestScopedErrorAfterASuccessfulAttemptIsNotSentAgain(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{BodyCut: true})
	b := e.body(msg{Session: sessionID(1), NonStream: true})
	fast := append(bytes.TrimSuffix(b, []byte("}")), []byte(`,"speed":"fast"}`)...)

	r := e.send(msg{Session: sessionID(1), Body: fast})
	attempts := len(e.upstream.inference()) - 1
	next := e.send(msg{Session: sessionID(1)})

	if attempts != 1 {
		t.Fatalf("the fast request made %d upstream attempts, want 1: an answered attempt is not sent again", attempts)
	}
	if r.Status == http.StatusOK || r.Header.Get("X-Should-Retry") != "false" {
		t.Fatalf("got %d with x-should-retry %q, want a failure the client does not retry", r.Status, r.Header.Get("X-Should-Retry"))
	}
	var classes []router.Class
	for _, ev := range e.events() {
		if ev.Kind == "attempt_failed" {
			classes = append(classes, ev.Class)
		}
	}
	if !reflect.DeepEqual(classes, []router.Class{router.ClassRequestScoped}) {
		t.Fatalf("failure classes %v, want [request_scoped]", classes)
	}
	if next.Status != http.StatusOK {
		t.Fatalf("next request got %d, want 200: the failure says nothing about the account", next.Status)
	}
}

func writeRawCredential(t *testing.T, e *env, account string, extra map[string]any) {
	t.Helper()
	meta := map[string]any{"type": "claude", "email": account + "@router.invalid", "access_token": "sk-ant-oat01-fake-" + randomHex(8), "expired": time.Now().Add(time.Hour).UTC().Format(time.RFC3339)}
	for k, v := range extra {
		meta[k] = v
	}
	b, _ := json.Marshal(meta)
	if err := writePrivate(credentialFile(e.dir, router.AccountID(account)), b); err != nil {
		t.Fatal(err)
	}
}

func TestCredentialsThatChangeTheUpstreamPathAreRefused(t *testing.T) {
	cases := map[string]map[string]any{
		"proxy-url alias": {"proxy-url": "http://127.0.0.1:9"},
		"base-url alias":  {"base-url": "http://127.0.0.1:9"},
		"headers":         {"headers": map[string]string{"Authorization": "Bearer other"}},
		"unknown key":     {"cloak_mode": "always"},
		"disabled":        {"disabled": true},
	}
	for name, extra := range cases {
		e := newEnv(t, "acct-a")
		writeRawCredential(t, e, "acct-a", extra)
		_, err := Start(e.cfg, Options{Upstream: e.upstream})
		if err == nil {
			t.Fatalf("%s: start accepted the credential", name)
		}
	}
	e := newEnv(t, "acct-a")
	writeRawCredential(t, e, "acct-a", map[string]any{"refresh_token": "fake", "last_refresh": "x", "disabled": false, "claude_device_ids": []string{"d"}})
	if err := CheckCredential(credentialFile(e.dir, "acct-a")); err != nil {
		t.Fatalf("a credential with only login and persistence keys was refused: %v", err)
	}
}

func TestUsageReadAuthFailureIsUnknownNotLogin(t *testing.T) {
	e := newEnv(t, "acct-a")
	t0 := time.Now()
	e.clock.set(t0)
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusUnauthorized})
	e.start()

	refused := e.send(msg{Session: sessionID(1)})
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	e.clock.set(t0.Add(time.Minute))
	after := e.send(msg{Session: sessionID(1)})

	if refused.Status != http.StatusServiceUnavailable || !strings.Contains(refused.ErrMsg, "paid overflow") {
		t.Fatalf("first request got %d %q, want a refusal for an unknown reading", refused.Status, refused.ErrMsg)
	}
	for _, want := range []string{"usage endpoint answered 401", "claude-router login -account acct-a"} {
		if !strings.Contains(refused.ErrMsg, want) {
			t.Fatalf("refusal %q does not name the failed read: want %q", refused.ErrMsg, want)
		}
	}
	if after.Status != 200 || after.Text != served("acct-a") {
		t.Fatalf("after a successful reading got %d %q, want 200 from acct-a", after.Status, after.Text)
	}
}

func TestServedMarkFailureStopsTheCompletedBlock(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Before: func() { _ = e.svc.store.Close() }})

	r := e.send(msg{Session: sessionID(1)})

	for _, ev := range r.Events {
		if ev == "content_block_stop" {
			t.Fatalf("client saw a completed block although the served mark failed: %v", r.Events)
		}
	}
	if r.ErrType != "api_error" && r.Status != http.StatusServiceUnavailable {
		t.Fatalf("client got %d %v, want an SSE error or a 503", r.Status, r.Events)
	}
}

func TestCleanEndWithoutMessageStopSendsAnError(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Events: 4, CleanCut: true})

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Events[len(r.Events)-1] != "error" {
		t.Fatalf("client saw %d %v, want the stream to end with an error event", r.Status, r.Events)
	}
}

func TestSubagentSuccessMarksTheAssignmentServed(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()

	e.send(msg{Session: sessionID(1), Agent: "agent-1", NonStream: true})

	if !e.binding(sessionID(1)).Served {
		t.Fatal("a successful subagent response did not mark the assignment served")
	}
}

type countingRT struct{ n atomic.Int32 }

func (c *countingRT) RoundTrip(req *http.Request) (*http.Response, error) {
	c.n.Add(1)
	return &http.Response{StatusCode: 200, Header: http.Header{}, Body: io.NopCloser(strings.NewReader("{}")), Request: req}, nil
}

func TestTransportCapsAttemptsAndRefusesAnotherAccount(t *testing.T) {
	inner := &countingRT{}
	tr := newTransport(inner, nil, defaultMaxAttempts, []router.Account{{ID: "acct-a", Capacity: 1}, {ID: "acct-b", Capacity: 1}}, time.Now, nil)
	budget := &atomic.Int32{}
	u, _ := url.Parse("https://api.anthropic.com/v1/messages")
	send := func(account router.AccountID) error {
		ctx := withCall(t.Context(), "c1", "acct-a", budget)
		req, _ := http.NewRequestWithContext(ctx, http.MethodPost, u.String(), nil)
		_, err := tr.roundTrip(account, req)
		return err
	}

	other := send("acct-b")
	var errs []bool
	for i := 0; i < defaultMaxAttempts+1; i++ {
		errs = append(errs, send("acct-a") != nil)
	}

	if other == nil {
		t.Fatal("transport sent an attempt for another account than the call's")
	}
	if !reflect.DeepEqual(errs, []bool{false, false, false, false, true}) || inner.n.Load() != defaultMaxAttempts {
		t.Fatalf("refusals %v and %d upstream sends, want the fifth refused and 4 sent", errs, inner.n.Load())
	}
}
