package claude

import (
	"encoding/json"
	"net/http"
	"strings"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func (e *env) writeRefreshableCredential(account string) {
	e.t.Helper()
	token := "sk-ant-oat01-fake-" + account + "-" + randomHex(8)
	e.tokens[account] = token
	payload, _ := json.Marshal(map[string]any{
		"type":                 "claude",
		"email":                account + "@router.invalid",
		"access_token":         token,
		"refresh_token":        "rt-" + randomHex(8),
		"expired":              time.Now().Add(24 * time.Hour).UTC().Format(time.RFC3339),
		"skip_account_profile": true,
	})
	if err := writePrivate(credentialFile(e.dir, router.AccountID(account)), payload); err != nil {
		e.t.Fatal(err)
	}
	e.upstream.bind(token, account)
}

func (e *env) refusedAttempts() []string {
	e.t.Helper()
	var out []string
	for _, ev := range e.events() {
		if ev.Kind == "attempt_refused" {
			out = append(out, string(ev.Account)+" "+ev.Reason)
		}
	}
	return out
}

func TestRefreshRedispatchIsRefusedAfterABlockingObservation(t *testing.T) {
	paid := map[string]string{
		"anthropic-ratelimit-unified-representative-claim": "overage",
		"anthropic-ratelimit-unified-overage-status":       "allowed",
		"anthropic-ratelimit-unified-overage-in-use":       "true",
	}
	cases := []struct {
		name       string
		blocking   reply
		wantStatus int
		wantType   string
		wantMsg    string
		wantLogged string
	}{
		{"paid use", reply{Header: paid}, http.StatusServiceUnavailable, "api_error", "account acct-a reported paid use", "acct-a paid_use_observed"},
		{"exhaustion", reply{Status: http.StatusTooManyRequests, Header: exhaustedHeaders(time.Now().Add(2 * time.Hour))}, http.StatusTooManyRequests, "rate_limit_error", "no account can serve this model now", "acct-a exhausted"},
	}
	for _, c := range cases {
		for _, nonStream := range []bool{false, true} {
			name := c.name + "/stream"
			if nonStream {
				name = c.name + "/non-stream"
			}
			t.Run(name, func(t *testing.T) {
				e := newEnv(t)
				e.cfg.Accounts = []router.Account{{ID: "acct-a", Capacity: 1}}
				e.writeRefreshableCredential("acct-a")
				e.start()
				x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
				e.replaceTokenExchange(x)
				release := e.holdExchanges(x)
				e.upstream.script("acct-a", reply{Status: http.StatusUnauthorized}, c.blocking)

				refreshing := make(chan result, 1)
				go func() { refreshing <- e.send(msg{Session: sessionID(1), NonStream: nonStream}) }()
				e.waitExchanging(x)
				e.send(msg{Session: sessionID(2), NonStream: nonStream})
				before := len(e.upstream.inference())
				release()

				var r result
				select {
				case r = <-refreshing:
				case <-time.After(15 * time.Second):
					t.Fatal("the request whose redispatch the router refused did not end")
				}
				if after := len(e.upstream.inference()); before != 2 || after != 2 {
					t.Fatalf("inference attempts %d before the refresh finished and %d after, want 2 and 2: none after the blocking observation", before, after)
				}
				if r.Status != c.wantStatus || r.ErrType != c.wantType || !strings.Contains(r.ErrMsg, c.wantMsg) {
					t.Fatalf("refused redispatch answered %d %s %q, want %d %s containing %q", r.Status, r.ErrType, r.ErrMsg, c.wantStatus, c.wantType, c.wantMsg)
				}
				if got := e.refusedAttempts(); len(got) != 1 || got[0] != c.wantLogged {
					t.Fatalf("attempt_refused events %q, want one %q", got, c.wantLogged)
				}
			})
		}
	}
}

func TestRefusedRedispatchMovesTheConversationAfterPaidUse(t *testing.T) {
	e := newEnv(t, "acct-b")
	e.cfg.Accounts = append([]router.Account{{ID: "acct-a", Capacity: 4}}, e.cfg.Accounts...)
	e.writeRefreshableCredential("acct-a")
	e.start()
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	e.replaceTokenExchange(x)
	release := e.holdExchanges(x)
	e.upstream.script("acct-a", reply{Status: http.StatusUnauthorized}, reply{Header: map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true"}})

	refreshing := make(chan result, 1)
	go func() { refreshing <- e.send(msg{Session: sessionID(1)}) }()
	e.waitExchanging(x)
	other := e.send(msg{Session: sessionID(2)})
	release()
	r := <-refreshing

	if other.Text != served("acct-a") {
		t.Fatalf("second conversation got %d %q, want it served by acct-a", other.Status, other.Text)
	}
	if r.Status != http.StatusOK || r.Text != served("acct-b") {
		t.Fatalf("refused redispatch answered %d %q, want 200 from acct-b", r.Status, r.Text)
	}
	if b := e.binding(sessionID(1)); b.Account != "acct-b" || b.Reason != router.ReasonOverageObserved {
		t.Fatalf("binding %+v, want acct-b for overage_observed", b)
	}
	if got := e.refusedAttempts(); len(got) != 1 || got[0] != "acct-a paid_use_observed" {
		t.Fatalf("attempt_refused events %q, want one %q", got, "acct-a paid_use_observed")
	}
	want := []string{"acct-a", "acct-a", "acct-b"}
	if got := e.upstream.accountsServed(); strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("upstream attempts %v, want %v", got, want)
	}
}
