package claude

import (
	"bytes"
	"context"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"regexp"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func TestConversationStaysOnItsAccount(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()

	first := e.send(msg{Session: sessionID(1)})
	second := e.send(msg{Session: sessionID(2)})
	again := e.send(msg{Session: sessionID(1)})
	subagent := e.send(msg{Session: sessionID(1), Agent: "agent-7"})
	fallback := e.send(msg{Session: sessionID(1), NonStream: true})
	otherModel := e.send(msg{Session: sessionID(1), Model: "claude-opus-4-1-20250805"})

	if first.Status != 200 || first.Text != served("acct-a") {
		t.Fatalf("first conversation got %d %q, want 200 from acct-a", first.Status, first.Text)
	}
	if second.Text != served("acct-b") {
		t.Fatalf("second conversation served %q, want acct-b", second.Text)
	}
	for name, r := range map[string]result{"repeat": again, "subagent": subagent, "non-streaming fallback": fallback, "model change": otherModel} {
		if r.Status != 200 || r.Text != served("acct-a") {
			t.Fatalf("%s request got %d %q, want 200 from acct-a", name, r.Status, r.Text)
		}
	}
	want := []string{"acct-a", "acct-b", "acct-a", "acct-a", "acct-a", "acct-a"}
	if got := e.upstream.accountsServed(); !reflect.DeepEqual(got, want) {
		t.Fatalf("upstream attempts by account %v, want %v", got, want)
	}
	var models []string
	for _, r := range e.upstream.inference() {
		models = append(models, rawString(rawFields(t, r.Body)["model"]))
	}
	if models[5] != "claude-opus-4-1-20250805" {
		t.Fatalf("upstream model %q, want the requested claude-opus-4-1-20250805", models[5])
	}
}

func rawString(raw []byte) string {
	s, _ := strconv.Unquote(string(raw))
	return s
}

func TestClientTokenIsRequired(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()

	missing := e.send(msg{Session: sessionID(1), Token: "-"})
	wrong := e.send(msg{Session: sessionID(1), Token: "client-" + randomHex(24)})
	status, _ := http.NewRequest(http.MethodGet, "http://"+e.svc.Addr()+"/claude-router/status", nil)
	statusNoToken := readResult(t, status)

	for name, r := range map[string]result{"missing token": missing, "wrong token": wrong, "status without token": statusNoToken} {
		if r.Status != http.StatusUnauthorized || r.ErrType != "authentication_error" {
			t.Fatalf("%s: got %d %q, want 401 authentication_error", name, r.Status, r.ErrType)
		}
		if r.Header.Get("X-Should-Retry") != "false" {
			t.Fatalf("%s: x-should-retry %q, want false", name, r.Header.Get("X-Should-Retry"))
		}
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("unauthenticated requests made %d upstream attempts", n)
	}
}

func TestClaudeCodePreconnectIsAnsweredWithoutAuthentication(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	eventsBefore := len(e.events())
	usageBefore := len(e.upstream.usageReads())

	probe, _ := http.NewRequest(http.MethodHead, "http://"+e.svc.Addr()+"/api/hello", nil)
	if r := readResult(t, probe); r.Status != http.StatusOK {
		t.Fatalf("HEAD /api/hello without credentials answered %d, want 200", r.Status)
	}
	if got := e.events()[eventsBefore:]; len(got) != 0 {
		t.Fatalf("the preconnect recorded events %+v, want none", got)
	}
	if n := len(e.upstream.inference()); n != 0 || len(e.upstream.usageReads()) != usageBefore {
		t.Fatalf("the preconnect reached the upstream (%d inference requests)", n)
	}

	for _, rt := range []struct{ method, path string }{{http.MethodGet, "/api/hello"}, {http.MethodHead, "/v1/messages"}, {http.MethodHead, "/api/hello/x"}} {
		req, _ := http.NewRequest(rt.method, "http://"+e.svc.Addr()+rt.path, nil)
		if r := readResult(t, req); r.Status != http.StatusUnauthorized {
			t.Fatalf("%s %s without credentials answered %d, want 401", rt.method, rt.path, r.Status)
		}
	}
}

func TestModelAliasesTheAPIAcceptsAreServed(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()

	sonnet := e.send(msg{Session: sessionID(1), Model: "claude-sonnet-4-5"})
	haiku := e.send(msg{Session: sessionID(1), Model: "claude-haiku-4-5", NonStream: true})

	for name, r := range map[string]result{"claude-sonnet-4-5": sonnet, "claude-haiku-4-5": haiku} {
		if r.Status != 200 || r.Text != served("acct-a") {
			t.Fatalf("%s got %d %q %q, want 200 from acct-a", name, r.Status, r.ErrType, r.ErrMsg)
		}
	}
	var models []string
	for _, r := range e.upstream.inference() {
		models = append(models, rawString(rawFields(t, r.Body)["model"]))
	}
	if want := []string{"claude-sonnet-4-5-20250929", "claude-haiku-4-5-20251001"}; !reflect.DeepEqual(models, want) {
		t.Fatalf("upstream models %v, want the dated IDs the aliases name %v", models, want)
	}
}

func TestRequestsWithoutSessionIdentityAreRejected(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()

	cases := map[string]msg{
		"missing header":    {NoSession: true, MetaSession: sessionID(1)},
		"mismatched header": {Session: sessionID(1), MetaSession: sessionID(2)},
		"no metadata":       {Session: sessionID(1), Body: []byte(`{"model":"claude-sonnet-4-5-20250929","max_tokens":8,"stream":true,"messages":[{"role":"user","content":"hi"}]}`)},
	}
	for name, m := range cases {
		r := e.send(m)
		if r.Status != http.StatusBadRequest || r.ErrType != "invalid_request_error" {
			t.Fatalf("%s: got %d %q, want 400 invalid_request_error", name, r.Status, r.ErrType)
		}
		if r.Header.Get("X-Should-Retry") != "false" || !strings.Contains(r.ErrMsg, "x-claude-code-session-id") {
			t.Fatalf("%s: x-should-retry %q message %q, want false and a message naming the header", name, r.Header.Get("X-Should-Retry"), r.ErrMsg)
		}
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("rejected requests made %d upstream attempts", n)
	}
	if bindings, _ := e.svc.store.Bindings(); len(bindings) != 0 {
		n := len(bindings)
		t.Fatalf("rejected requests created %d assignments", n)
	}
}

func TestAForkIsANewConversation(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()

	origin := e.send(msg{Session: sessionID(1)})
	fork := e.send(msg{Session: sessionID(2)})

	if origin.Text != served("acct-a") || fork.Text != served("acct-b") {
		t.Fatalf("origin served %q and fork %q, want the fork placed as a new conversation on acct-b", origin.Text, fork.Text)
	}
}

func sdkPort(t *testing.T, dir string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(dir, "sdk-config.yaml"))
	if err != nil {
		t.Fatal(err)
	}
	m := regexp.MustCompile(`(?m)^  port: (\d+)$`).FindSubmatch(data)
	if m == nil {
		t.Fatal("no port in the SDK configuration")
	}
	return string(m[1])
}

func TestStockSDKRoutesAreClosed(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	body := e.body(msg{Session: sessionID(1)})

	routes := []struct{ method, path string }{
		{http.MethodPost, "/v1/messages"},
		{http.MethodPost, "/v1/messages/count_tokens"},
		{http.MethodGet, "/v1/models"},
		{http.MethodPost, "/v1/chat/completions"},
		{http.MethodPost, "/v1/responses"},
	}
	for _, rt := range routes {
		path := rt.path
		for _, token := range []string{"", e.token} {
			req, _ := http.NewRequest(rt.method, "http://127.0.0.1:"+sdkPort(t, e.dir)+path, bytes.NewReader(body))
			req.Header.Set(headerSessionID, sessionID(1))
			if token != "" {
				req.Header.Set("X-Api-Key", token)
			}
			if r := readResult(t, req); r.Status != http.StatusUnauthorized {
				t.Fatalf("SDK route %s with token=%v answered %d, want 401", path, token != "", r.Status)
			}
		}
	}
	req, _ := http.NewRequest(http.MethodPost, "http://"+e.svc.Addr()+"/v1/messages/count_tokens", bytes.NewReader(body))
	req.Header.Set("X-Api-Key", e.token)
	if r := readResult(t, req); r.Status != http.StatusNotFound {
		t.Fatalf("router count_tokens answered %d, want 404", r.Status)
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("closed routes made %d upstream attempts", n)
	}
}

func TestRestartKeepsBindings(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.send(msg{Session: sessionID(2)})

	// After the restart acct-b has twice acct-a's capacity, so placement
	// alone would put both conversations on acct-b.
	e.cfg.Accounts[1].Capacity = 2
	e.restart()
	r1 := e.send(msg{Session: sessionID(1)})
	r2 := e.send(msg{Session: sessionID(2)})
	fresh := e.send(msg{Session: sessionID(3)})

	if r1.Text != served("acct-a") || r2.Text != served("acct-b") {
		t.Fatalf("after restart conversations served %q and %q, want acct-a and acct-b", r1.Text, r2.Text)
	}
	if fresh.Text != served("acct-b") {
		t.Fatalf("new conversation after restart served %q, want acct-b under the restarted capacities", fresh.Text)
	}
}

func TestRotatedCredentialAppliesAtRestartOnTheSameAccount(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	before := e.send(msg{Session: sessionID(1)})
	authID := e.svc.authIDs["acct-a"]

	e.svc.Close()
	rotated := e.writeCredential("acct-a")
	e.start()
	after := e.send(msg{Session: sessionID(1)})

	seen := e.upstream.inference()
	if seen[1].Token != rotated {
		t.Fatal("the restarted service did not use the rotated credential")
	}
	if before.Text != served("acct-a") || after.Text != served("acct-a") {
		t.Fatalf("served %q before and %q after the rotation, want acct-a", before.Text, after.Text)
	}
	if e.svc.authIDs["acct-a"] != authID {
		t.Fatalf("auth ID changed from %s to %s across rotation", authID, e.svc.authIDs["acct-a"])
	}
}

func TestPreOutputFailuresRetryOnTheAssignedAccount(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Status: 529}, reply{StreamError: "overloaded_error"})

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a after two failures", r.Status, r.Text)
	}
	want := []string{"acct-a", "acct-a", "acct-a", "acct-a"}
	if got := e.upstream.accountsServed(); !reflect.DeepEqual(got, want) {
		t.Fatalf("upstream attempts %v, want %v", got, want)
	}
}

func TestRetryBudgetCapsUpstreamAttempts(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	for i := 0; i < 6; i++ {
		e.upstream.script("acct-a", reply{Status: 503})
	}

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusServiceUnavailable || r.Header.Get("X-Should-Retry") != "false" {
		t.Fatalf("got %d with x-should-retry %q, want 503 and false", r.Status, r.Header.Get("X-Should-Retry"))
	}
	if n := len(e.upstream.inference()); n != 3 {
		t.Fatalf("router made %d upstream attempts, want MaxAttempts = 3", n)
	}
}

func TestASingleAttemptCapNeverRetries(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.MaxUpstreamAttempts = 1
	e.start()
	e.upstream.script("acct-a", reply{Status: 529}, reply{Status: 529})

	failed := e.send(msg{Session: sessionID(1)})
	next := e.send(msg{Session: sessionID(1)})

	if failed.Status != http.StatusServiceUnavailable || failed.Header.Get("X-Should-Retry") != "false" {
		t.Fatalf("got %d with x-should-retry %q, want 503 and false", failed.Status, failed.Header.Get("X-Should-Retry"))
	}
	if next.Status != http.StatusServiceUnavailable {
		t.Fatalf("second request got %d, want 503 after its one failed attempt", next.Status)
	}
	if n := len(e.upstream.inference()); n != 2 {
		t.Fatalf("2 client requests made %d upstream attempts, want 1 each", n)
	}
}

func TestUpstreamAttemptCapOutsideOneToFourIsRefused(t *testing.T) {
	for _, n := range []int{-1, 5} {
		e := newEnv(t, "acct-a")
		e.cfg.MaxUpstreamAttempts = n
		if svc, err := Start(e.cfg, Options{Upstream: e.upstream}); err == nil {
			svc.Close()
			t.Fatalf("max_upstream_attempts %d started, want a configuration error", n)
		}
	}
}

func exhaustedHeaders(reset time.Time) map[string]string {
	return map[string]string{
		"anthropic-ratelimit-unified-status":    "rejected",
		"anthropic-ratelimit-unified-5h-status": "rejected",
		"anthropic-ratelimit-unified-5h-reset":  strconv.FormatInt(reset.Unix(), 10),
		"anthropic-ratelimit-unified-reset":     strconv.FormatInt(reset.Unix(), 10),
	}
}

func TestExhaustionIsAnsweredLocallyWithoutMoving(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(2 * time.Hour).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})

	r := e.send(msg{Session: sessionID(1)})
	again := e.send(msg{Session: sessionID(1)})

	for name, got := range map[string]result{"exhausting request": r, "next request": again} {
		if got.Status != http.StatusTooManyRequests || got.ErrType != "rate_limit_error" {
			t.Fatalf("%s: got %d %q, want 429 rate_limit_error", name, got.Status, got.ErrType)
		}
		if got.Header.Get("Anthropic-Ratelimit-Unified-Reset") != strconv.FormatInt(reset.Unix(), 10) {
			t.Fatalf("%s: unified reset %q, want %d", name, got.Header.Get("Anthropic-Ratelimit-Unified-Reset"), reset.Unix())
		}
		if ra, _ := strconv.Atoi(got.Header.Get("Retry-After")); ra < 7000 || ra > 7200 {
			t.Fatalf("%s: retry-after %q, want about 7200", name, got.Header.Get("Retry-After"))
		}
	}
	if n := len(e.upstream.inference()); n != 2 {
		t.Fatalf("upstream attempts %d, want 2: no attempt on the known exhausted account", n)
	}
	if b := e.binding(sessionID(1)); b.Account != "acct-a" {
		t.Fatalf("conversation moved to %s, want it kept on acct-a", b.Account)
	}
}

func TestHeaderlessThrottleAfterExhaustionRetries(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	past := time.Now().Add(-time.Minute).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Header: exhaustedHeaders(past)}, reply{Status: 429})

	first := e.send(msg{Session: sessionID(1)})
	second := e.send(msg{Session: sessionID(1)})

	if first.Status != 200 || second.Status != 200 || second.Text != served("acct-a") {
		t.Fatalf("got %d then %d %q, want the headerless 429 retried to 200 on acct-a", first.Status, second.Status, second.Text)
	}
	if n := len(e.upstream.inference()); n != 3 {
		t.Fatalf("upstream attempts %d, want 3", n)
	}
	for _, ev := range e.events() {
		if ev.Kind == "attempt_failed" && ev.Class != router.ClassThrottle {
			t.Fatalf("headerless 429 classified %s, want throttle", ev.Class)
		}
	}
}

func TestUpstreamAuthFailureNeverReachesTheClientAs401(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Status: 401}, reply{Status: 401})

	servedConv := e.send(msg{Session: sessionID(1)})
	unserved := e.send(msg{Session: sessionID(3), NonStream: true})

	if servedConv.Status == http.StatusUnauthorized || servedConv.Status != http.StatusServiceUnavailable {
		t.Fatalf("served conversation got %d, want 503 and never 401", servedConv.Status)
	}
	if !strings.Contains(servedConv.ErrMsg, "claude-router login") {
		t.Fatalf("message %q does not tell the operator to log the account in", servedConv.ErrMsg)
	}
	if b := e.binding(sessionID(1)); b.Account != "acct-a" {
		t.Fatalf("served conversation moved to %s, want it kept on acct-a", b.Account)
	}
	if unserved.Status != 200 || unserved.Text != served("acct-b") {
		t.Fatalf("new conversation got %d %q, want 200 from acct-b", unserved.Status, unserved.Text)
	}
}

func TestServedMarkFollowsTheFirstCompletedBlock(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Events: 3}, reply{Events: 4})

	cut := e.send(msg{Session: sessionID(1)})
	afterCut := e.binding(sessionID(1)).Served
	completed := e.send(msg{Session: sessionID(1)})
	afterBlock := e.binding(sessionID(1)).Served

	if cut.Status != 200 || completed.Status != 200 {
		t.Fatalf("statuses %d and %d, want 200 with a cut stream", cut.Status, completed.Status)
	}
	if afterCut {
		t.Fatal("assignment marked served by a stream cut before any completed block")
	}
	if !afterBlock {
		t.Fatal("assignment not marked served after a completed content block")
	}
}

func TestUsageCountersAreRecordedAndUnknownStaysUnknown(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{}, reply{NoUsage: true})

	e.send(msg{Session: sessionID(1)})
	e.send(msg{Session: sessionID(1)})

	var usages []*Usage
	for _, ev := range e.events() {
		if ev.Kind == "request" {
			usages = append(usages, ev.Usage)
		}
	}
	if len(usages) != 2 || usages[0] == nil {
		t.Fatalf("request events carry usage %v, want counters on the first", usages)
	}
	u := usages[0]
	if u.CacheCreationInputTokens == nil || *u.CacheCreationInputTokens != 100 || u.CacheReadInputTokens == nil || *u.CacheReadInputTokens != 2000 {
		t.Fatalf("cache counters %+v, want creation 100 and read 2000", u)
	}
	if usages[1] != nil {
		t.Fatalf("response without usage recorded %+v, want unknown", *usages[1])
	}
	raw, _ := os.ReadFile(filepath.Join(e.dir, "events.jsonl"))
	if !bytes.Contains(raw, []byte(`"cache_read_input_tokens":2000`)) {
		t.Fatal("events.jsonl does not carry the cache read counter")
	}
}

func TestClientCancellationStopsTheUpstreamStream(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	ctx, cancel := context.WithTimeout(context.Background(), 300*time.Millisecond)
	defer cancel()
	e.upstream.script("acct-a", reply{Events: 4, Hold: true})

	r := e.sendCtx(ctx, msg{Session: sessionID(1)})
	canceledAt := time.Now()

	select {
	case ended := <-e.upstream.ended:
		if lag := ended.Sub(canceledAt); lag > time.Second {
			t.Fatalf("upstream stream ended %v after the client went away, want under 1s", lag)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("upstream stream still open 5s after the client went away")
	}
	if r.Status != 200 || !reflect.DeepEqual(r.Events[:4], []string{"message_start", "content_block_start", "content_block_delta", "content_block_stop"}) {
		t.Fatalf("client saw %d %v before canceling, want the first four events", r.Status, r.Events)
	}
}

func TestRequestScopedRefusalIsReportedWithoutRetry(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Status: 429, Body: errorBody("rate_limit_error", "Usage credits are required for fast mode")})
	fast := e.body(msg{Session: sessionID(1)})
	fast = append(bytes.TrimSuffix(fast, []byte("}")), []byte(`,"speed":"fast"}`)...)

	r := e.send(msg{Session: sessionID(1), Body: fast})
	next := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusTooManyRequests || r.Header.Get("X-Should-Retry") != "false" || !strings.Contains(r.ErrMsg, "fast mode") {
		t.Fatalf("fast refusal reached the client as %d %q (x-should-retry %q), want 429 with the upstream message and no retry", r.Status, r.ErrMsg, r.Header.Get("X-Should-Retry"))
	}
	want := []string{"acct-a", "acct-a", "acct-a"}
	if got := e.upstream.accountsServed(); !reflect.DeepEqual(got, want) {
		t.Fatalf("upstream attempts %v, want %v: one attempt for the refusal, and the account still serves", got, want)
	}
	if next.Status != 200 {
		t.Fatalf("next request got %d, want 200: a request-scoped refusal says nothing about the account", next.Status)
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
}
