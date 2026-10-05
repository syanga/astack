package claude

import (
	"context"
	"errors"
	"net/http"
	"strings"
	"sync"
	"testing"
	"time"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// tokenExchange replaces the token exchange in the Claude executor's
// Refresh, the one step of an SDK refresh that dials platform.claude.com.
// Registered after Start, it delegates everything else to the real
// executor, so a refresh still runs through the SDK's refresh lock, auth
// state, and file store. A successful exchange revokes the old access token
// at the fake upstream, as the token endpoint does, and rotates the refresh
// token. The new access token is accepted only with accept set.
type tokenExchange struct {
	coreauth.ProviderExecutor
	up      *fakeUpstream
	account string
	delay   time.Duration
	accept  bool
	fail    bool

	mu     sync.Mutex
	calls  int
	reused int
	lastRT string
}

func (x *tokenExchange) PrepareRequest(req *http.Request, a *coreauth.Auth) error {
	return x.ProviderExecutor.(coreauth.RequestPreparer).PrepareRequest(req, a)
}

func (x *tokenExchange) ShouldPrepareRequestAuth(a *coreauth.Auth) bool {
	p, ok := x.ProviderExecutor.(coreauth.RequestAuthPreparer)
	return ok && p.ShouldPrepareRequestAuth(a)
}

func (x *tokenExchange) PrepareRequestAuth(ctx context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	return x.ProviderExecutor.(coreauth.RequestAuthPreparer).PrepareRequestAuth(ctx, a)
}

func (x *tokenExchange) Refresh(_ context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	x.mu.Lock()
	x.calls++
	if rt, _ := a.Metadata["refresh_token"].(string); rt != x.lastRT {
		x.reused++
	}
	x.mu.Unlock()
	time.Sleep(x.delay)
	if x.fail {
		return nil, errors.New("token exchange failed")
	}
	old := accessToken(a)
	b := a.Clone()
	at := "sk-ant-oat01-fake-" + x.account + "-refreshed-" + randomHex(8)
	rt := "rt-" + randomHex(8)
	x.up.mu.Lock()
	delete(x.up.accounts, hashToken(old))
	x.up.mu.Unlock()
	if x.accept {
		x.up.bind(at, x.account)
	}
	b.Metadata["access_token"] = at
	b.Metadata["refresh_token"] = rt
	b.Metadata["expired"] = time.Now().Add(8 * time.Hour).UTC().Format(time.RFC3339)
	b.Metadata["last_refresh"] = time.Now().UTC().Format(time.RFC3339)
	x.mu.Lock()
	x.lastRT = rt
	x.mu.Unlock()
	return b, nil
}

// exchanges counts the token exchanges. It fails the test if one sent a
// refresh token that an earlier exchange had replaced.
func (x *tokenExchange) exchanges(t *testing.T) int {
	t.Helper()
	x.mu.Lock()
	defer x.mu.Unlock()
	if x.reused != 0 {
		t.Fatalf("%d token exchanges sent a replaced refresh token", x.reused)
	}
	return x.calls
}

// replaceTokenExchange puts x in front of the SDK's Claude executor. The
// SDK registers its executor while it starts, so this runs after Start.
// Test credentials carry no refresh token, so a refresh by the real
// executor before this point makes no request.
func (e *env) replaceTokenExchange(x *tokenExchange) {
	e.t.Helper()
	orig, ok := e.svc.core.Executor("claude")
	if !ok {
		e.t.Fatal("the SDK has no claude executor")
	}
	x.ProviderExecutor = orig
	e.svc.core.RegisterExecutor(x)
}

func (e *env) sdkAuth(account string) *coreauth.Auth {
	e.t.Helper()
	a, ok := e.svc.core.GetByID(e.svc.authIDs[router.AccountID(account)])
	if !ok {
		e.t.Fatalf("%s is not registered in the SDK", account)
	}
	return a
}

func (e *env) waitFor(what string, limit time.Duration, cond func() bool) {
	e.t.Helper()
	deadline := time.Now().Add(limit)
	for !cond() {
		if time.Now().After(deadline) {
			e.t.Fatalf("timed out waiting until %s", what)
		}
		time.Sleep(5 * time.Millisecond)
	}
}

// writeCredentialDueIn writes an accepted credential that enters the SDK's
// four-hour refresh lead after d.
func (e *env) writeCredentialDueIn(account string, d time.Duration) string {
	e.t.Helper()
	token := e.writeCredentialExpiring(account, time.Now().Add(4*time.Hour+d))
	e.upstream.bind(token, account)
	return token
}

// recheckNow makes the next send read the account's setting on demand:
// the reading is stale and the last read is older than the recheck
// interval.
func (e *env) recheckNow(step time.Duration) {
	e.clock.set(e.clock.now().Add(step))
}

func (e *env) refreshEvents(outcome string) int {
	n := 0
	for _, ev := range e.events() {
		if ev.Kind == "credential_refresh" && ev.Outcome == outcome {
			n++
		}
	}
	return n
}

func (e *env) overageState(account string) router.Overage {
	for _, a := range e.svc.Status().Accounts {
		if string(a.ID) == account {
			return a.Overage.State
		}
	}
	return ""
}

func (e *env) waitDisabled(account string, limit time.Duration) bool {
	deadline := time.Now().Add(limit)
	for time.Now().Before(deadline) {
		if e.overageState(account) == router.OverageDisabled {
			return true
		}
		time.Sleep(10 * time.Millisecond)
	}
	return false
}

func TestReadOfADueCredentialWaitsForTheSDKRefresh(t *testing.T) {
	e := newEnv(t, "acct-a")
	old := e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.start()
	x := &tokenExchange{up: e.upstream, account: "acct-a", delay: time.Second, accept: true}
	e.replaceTokenExchange(x)
	e.waitFor("the SDK has a refresh pending", 10*time.Second, func() bool {
		return e.sdkAuth("acct-a").NextRefreshAfter.After(time.Now())
	})
	before := len(e.upstream.usageReads())

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a once the SDK's refresh landed", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the read uses the SDK's refresh", n)
	}
	for _, u := range e.upstream.usageReads()[before:] {
		if u.Token == old {
			t.Fatal("a settings read used the access token the SDK was replacing")
		}
	}
	if n := e.refreshEvents("sdk_refreshed"); n != 1 {
		t.Fatalf("sdk_refreshed events %d, want 1", n)
	}
}

// startWithoutSDKRefresh starts the service with a credential that becomes
// due after start, and stops the SDK's refresh loop, so the router is the
// only refresher.
func (e *env) startWithoutSDKRefresh(x *tokenExchange) string {
	e.t.Helper()
	old := e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	e.svc.core.StopAutoRefresh()
	e.replaceTokenExchange(x)
	e.waitFor("the credential is due", 10*time.Second, func() bool { return refreshDue(e.sdkAuth("acct-a"), time.Now()) })
	return old
}

func TestRouterRefreshesADueCredentialTheSDKHasNotQueued(t *testing.T) {
	e := newEnv(t, "acct-a")
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	old := e.startWithoutSDKRefresh(x)
	before := len(e.upstream.usageReads())

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a on the refreshed credential", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1", n)
	}
	for _, u := range e.upstream.usageReads()[before:] {
		if u.Token == old {
			t.Fatal("a settings read used the due access token")
		}
	}
}

func TestReadThatRefreshedDoesNotRefreshAgainAfterA401(t *testing.T) {
	e := newEnv(t, "acct-a")
	x := &tokenExchange{up: e.upstream, account: "acct-a"}
	e.startWithoutSDKRefresh(x)
	before := len(e.upstream.usageReads())

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
	if n, reads := x.exchanges(t), len(e.upstream.usageReads())-before; n != 1 || reads != 1 {
		t.Fatalf("token exchanges %d and settings reads %d, want 1 and 1", n, reads)
	}
}

func TestReadDoesNotRefreshWhileTheSDKRefreshIsRunning(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", delay: 2 * sdkRefreshWait, accept: true}
	e.replaceTokenExchange(x)
	e.waitFor("the SDK has a refresh pending", 10*time.Second, func() bool {
		return e.sdkAuth("acct-a").NextRefreshAfter.After(time.Now())
	})

	e.recheckNow(31 * time.Minute)
	waited := e.send(msg{Session: sessionID(1)})
	e.waitFor("the SDK's refresh landed", 10*time.Second, func() bool { return x.exchanges(t) == 1 && !refreshDue(e.sdkAuth("acct-a"), time.Now()) })
	e.recheckNow(31 * time.Second)
	r := e.send(msg{Session: sessionID(1)})

	if waited.Status != http.StatusServiceUnavailable {
		t.Fatalf("while the SDK's refresh ran got %d, want 503", waited.Status)
	}
	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("after the SDK's refresh got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the read never refreshes behind the SDK", n)
	}
}

func TestUnauthorizedReadUsesTheTokenTheSDKInstalledDuringTheRead(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.clock.set(time.Now())
	e.start()
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	e.replaceTokenExchange(x)
	id := e.svc.authIDs["acct-a"]
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusUnauthorized, Before: func() {
		if _, err := e.svc.core.ForceRefreshAuth(context.Background(), id); err != nil {
			t.Errorf("SDK refresh: %v", err)
		}
		e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	}})

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the read reuses the SDK's new token", n)
	}
}

func TestUnauthorizedSettingsReadRefreshesOnceAndReadsAgain(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.clock.set(time.Now())
	e.start()
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	e.replaceTokenExchange(x)
	e.upstream.mu.Lock()
	delete(e.upstream.accounts, hashToken(e.tokens["acct-a"]))
	e.upstream.mu.Unlock()
	before := len(e.upstream.usageReads())

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
	if n, reads := x.exchanges(t), len(e.upstream.usageReads())-before; n != 1 || reads != 2 {
		t.Fatalf("token exchanges %d and settings reads %d, want 1 and 2", n, reads)
	}
}

// After the SDK's refresh fails, a read neither waits for the SDK nor
// refreshes again; the SDK's retry backoff paces the next attempt.
func TestFailedSDKRefreshIsNotRepeatedByReads(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", fail: true}
	e.replaceTokenExchange(x)
	e.waitFor("the SDK's refresh failed", 10*time.Second, func() bool {
		return e.sdkAuth("acct-a").LastError != nil
	})
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	before := len(e.upstream.usageReads())

	for i := 0; i < 4; i++ {
		e.recheckNow(31 * time.Second)
		begun := time.Now()
		r := e.send(msg{Session: sessionID(1)})
		if took := time.Since(begun); r.Status != http.StatusServiceUnavailable || took > sdkRefreshWait/2 {
			t.Fatalf("send %d got %d after %s, want 503 at once", i, r.Status, took)
		}
	}

	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's own", n)
	}
	if n := len(e.upstream.usageReads()) - before; n != 0 {
		t.Fatalf("settings reads %d after the failed refresh, want 0", n)
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("inference attempts %d, want 0", n)
	}
	if got := e.overageState("acct-a"); got != "" {
		t.Fatalf("reading is %q, want unknown", got)
	}
	if n := e.refreshEvents("failed"); n != 1 {
		t.Fatalf("failed credential_refresh events %d, want 1 for the failure run", n)
	}
}

// A refresh the router starts and that fails is the only one it starts
// until the access token changes.
func TestRouterRefreshIsNotRepeatedAfterItFails(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", fail: true}
	e.replaceTokenExchange(x)
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	e.upstream.mu.Lock()
	delete(e.upstream.accounts, hashToken(e.tokens["acct-a"]))
	e.upstream.mu.Unlock()

	for i := 0; i < 4; i++ {
		e.recheckNow(31 * time.Second)
		if r := e.send(msg{Session: sessionID(1)}); r.Status != http.StatusServiceUnavailable {
			t.Fatalf("send %d got %d, want 503", i, r.Status)
		}
	}

	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1", n)
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("inference attempts %d, want 0", n)
	}
	if n := e.refreshEvents("failed"); n != 1 {
		t.Fatalf("failed credential_refresh events %d, want 1", n)
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
	ready := e.waitDisabled("acct-a", 5*time.Second)
	r := e.send(msg{Session: sessionID(1)})

	if refused.Status != http.StatusServiceUnavailable {
		t.Fatalf("before the re-read got %d, want 503", refused.Status)
	}
	reads := e.upstream.usageReads()
	if !ready || len(reads) != 2 {
		t.Fatalf("ready=%v after %d settings reads, want ready after 2", ready, len(reads))
	}
	if gap := reads[1].At.Sub(reads[0].At); gap < time.Second || gap > 3*time.Second {
		t.Fatalf("re-read %s after the 429, want about 1 s, as Retry-After asked", gap)
	}
	if r.Status != 200 || len(e.upstream.inference()) != 1 {
		t.Fatalf("got %d with %d inference attempts, want 200 and 1", r.Status, len(e.upstream.inference()))
	}
}

func TestPersistentUnauthorizedReadStaysUnknownWithBoundedReads(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.OverageCheckEvery = Duration(2 * time.Second)
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})
	e.startWith(Options{ReadRetryBase: 100 * time.Millisecond})
	x := &tokenExchange{up: e.upstream, account: "acct-a"}
	e.replaceTokenExchange(x)
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	e.upstream.mu.Lock()
	delete(e.upstream.accounts, hashToken(e.tokens["acct-a"]))
	e.upstream.mu.Unlock()

	// The start read, then 6 re-reads of two usage requests and one
	// refresh each: every new token is refused.
	e.waitFor("the re-reads end", 10*time.Second, func() bool { return len(e.upstream.usageReads()) >= 13 })
	time.Sleep(time.Second)
	run := e.upstream.usageReads()
	exchanges := x.exchanges(t)
	e.waitFor("the next regular read", 5*time.Second, func() bool { return len(e.upstream.usageReads()) > 13 })
	next := e.upstream.usageReads()[13]
	r := e.send(msg{Session: sessionID(1)})

	if len(run) != 13 || exchanges != 6 {
		t.Fatalf("settings reads %d and token exchanges %d in the failure run, want 13 and 6", len(run), exchanges)
	}
	if gap := next.At.Sub(run[12].At); gap < 2*time.Second {
		t.Fatalf("next read %s after the last re-read, want the regular interval of 2 s", gap)
	}
	if got := e.overageState("acct-a"); got != "" {
		t.Fatalf("reading is %q, want unknown", got)
	}
	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
	if !strings.Contains(r.ErrMsg, "401") {
		t.Fatalf("refusal %q does not name the failed read", r.ErrMsg)
	}
}

func TestSuccessfulRecheckEndsTheRunOfFailedReads(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.OverageCheckEvery = Duration(4 * time.Second)
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: 1500 * time.Millisecond})

	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	e.recheckNow(31 * time.Second)
	r := e.send(msg{Session: sessionID(1)})
	e.waitFor("the next scheduled read", 6*time.Second, func() bool { return len(e.upstream.usageReads()) >= 3 })

	if r.Status != 200 {
		t.Fatalf("got %d, want 200 after the recheck read the setting", r.Status)
	}
	reads := e.upstream.usageReads()
	if gap := reads[2].At.Sub(reads[1].At); gap < 4*time.Second {
		t.Fatalf("scheduled read %s after the successful recheck, want the regular interval of 4 s", gap)
	}
}
