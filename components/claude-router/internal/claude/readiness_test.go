package claude

import (
	"context"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// fakeTokenEndpoint stands in for the SDK's OAuth token exchange. Each
// refresh installs a new access token valid for 8 hours through the SDK's
// auth manager, which persists it as the SDK's own refresh does. With
// accepted set, the fake upstream accepts the new token.
type fakeTokenEndpoint struct {
	up       *fakeUpstream
	accepted bool

	mu     sync.Mutex
	issued []string
}

func (f *fakeTokenEndpoint) refresh(ctx context.Context, core *coreauth.Manager, authID string) (*coreauth.Auth, error) {
	cur, ok := core.GetByID(authID)
	if !ok {
		return nil, context.Canceled
	}
	a := cur.Clone()
	account := strings.TrimSuffix(baseName(a.FileName), ".json")
	token := "sk-ant-oat01-fake-" + account + "-refreshed-" + randomHex(8)
	if f.accepted {
		f.up.bind(token, account)
	}
	a.Metadata["access_token"] = token
	a.Metadata["expired"] = time.Now().Add(8 * time.Hour).UTC().Format(time.RFC3339)
	a.Metadata["last_refresh"] = time.Now().UTC().Format(time.RFC3339)
	f.mu.Lock()
	f.issued = append(f.issued, token)
	f.mu.Unlock()
	return core.Update(ctx, a)
}

func (f *fakeTokenEndpoint) refreshes() int {
	f.mu.Lock()
	defer f.mu.Unlock()
	return len(f.issued)
}

func (e *env) overageState(account string) router.Overage {
	for _, a := range e.svc.Status().Accounts {
		if string(a.ID) == account {
			return a.Overage.State
		}
	}
	return ""
}

func (e *env) waitDisabled(account string, limit time.Duration) (time.Duration, bool) {
	start := time.Now()
	for time.Since(start) < limit {
		if e.overageState(account) == router.OverageDisabled {
			return time.Since(start), true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return time.Since(start), false
}

func (e *env) waitReadsSettle(quiet, limit time.Duration) int {
	start := time.Now()
	n, since := len(e.upstream.usageReads()), time.Now()
	for time.Since(start) < limit {
		time.Sleep(20 * time.Millisecond)
		if m := len(e.upstream.usageReads()); m != n {
			n, since = m, time.Now()
		} else if time.Since(since) >= quiet {
			break
		}
	}
	return n
}

func TestExpiredCredentialAtStartIsRefreshedBeforeTheSettingsRead(t *testing.T) {
	e := newEnv(t, "acct-a")
	expired := e.writeCredentialExpiring("acct-a", time.Now().Add(-time.Hour))
	tokens := &fakeTokenEndpoint{up: e.upstream, accepted: true}

	begun := time.Now()
	e.startWith(Options{Refresh: tokens.refresh})
	ready := time.Since(begun)
	r := e.send(msg{Session: sessionID(1)})

	if got := e.overageState("acct-a"); got != router.OverageDisabled {
		t.Fatalf("reading after start is %q, want disabled", got)
	}
	if ready > 10*time.Second {
		t.Fatalf("start took %s, want a few seconds", ready)
	}
	if n := tokens.refreshes(); n != 1 {
		t.Fatalf("refreshes %d, want 1", n)
	}
	for _, u := range e.upstream.usageReads() {
		if u.Token == expired {
			t.Fatal("a settings read used the expired access token the SDK replaces")
		}
	}
	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a on the refreshed credential", r.Status, r.Text)
	}
}

func TestUnauthorizedSettingsReadRefreshesOnceAndReadsAgain(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialExpiring("acct-a", time.Now().Add(24*time.Hour))
	tokens := &fakeTokenEndpoint{up: e.upstream, accepted: true}

	e.startWith(Options{Refresh: tokens.refresh})
	r := e.send(msg{Session: sessionID(1)})

	if got := e.overageState("acct-a"); got != router.OverageDisabled {
		t.Fatalf("reading after start is %q, want disabled", got)
	}
	if n, reads := tokens.refreshes(), len(e.upstream.usageReads()); n != 1 || reads != 2 {
		t.Fatalf("refreshes %d and settings reads %d, want 1 and 2", n, reads)
	}
	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
}

func TestRateLimitedStartReadIsReadAgainAfterRetryAfter(t *testing.T) {
	e := newEnv(t, "acct-a")
	disabled := false
	e.upstream.setUsage("acct-a", usageReply{
		Status: http.StatusTooManyRequests,
		Header: map[string]string{"Retry-After": "1"},
		Before: func() { e.upstream.setUsage("acct-a", usageReply{Enabled: &disabled}) },
	})

	e.startWith(Options{ReadRetryBase: 50 * time.Millisecond})
	refused := e.send(msg{Session: sessionID(1)})
	after, ready := e.waitDisabled("acct-a", 5*time.Second)
	r := e.send(msg{Session: sessionID(1)})

	if refused.Status != http.StatusServiceUnavailable {
		t.Fatalf("before the re-read got %d, want 503", refused.Status)
	}
	if !ready || after < 900*time.Millisecond || after > 3*time.Second {
		t.Fatalf("ready=%v after %s, want ready about 1 s after start, as Retry-After asked", ready, after)
	}
	if r.Status != 200 || len(e.upstream.inference()) != 1 {
		t.Fatalf("got %d with %d inference attempts, want 200 and 1", r.Status, len(e.upstream.inference()))
	}
	if n := len(e.upstream.usageReads()); n != 2 {
		t.Fatalf("settings reads %d, want 2", n)
	}
}

func TestPersistentUnauthorizedReadStaysUnknownWithBoundedReads(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialExpiring("acct-a", time.Now().Add(-time.Hour))
	tokens := &fakeTokenEndpoint{up: e.upstream, accepted: false}

	e.startWith(Options{Refresh: tokens.refresh, ReadRetryBase: 10 * time.Millisecond})
	reads := e.waitReadsSettle(1500*time.Millisecond, 15*time.Second)
	r := e.send(msg{Session: sessionID(1)})

	if got := e.overageState("acct-a"); got != "" {
		t.Fatalf("reading is %q, want unknown", got)
	}
	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
	if !strings.Contains(r.ErrMsg, "401") {
		t.Fatalf("refusal %q does not name the failed read", r.ErrMsg)
	}
	if bound := 2 * (1 + readRetries); reads > bound || reads < 2 {
		t.Fatalf("settings reads %d, want between 2 and %d: the start read and %d re-reads, two requests each", reads, bound, readRetries)
	}
	if n := tokens.refreshes(); n > 1+readRetries {
		t.Fatalf("refreshes %d, want at most one per read", n)
	}
}
