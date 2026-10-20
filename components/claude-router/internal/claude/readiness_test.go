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

// tokenExchange stands in for the token exchange inside the Claude
// executor's Refresh, the one step of an SDK refresh that dials
// platform.claude.com. It sits behind the router's refresh guard, so every
// refresh, the SDK's and the router's, still runs through the SDK's refresh
// lock, auth state, and file store, and through the guard. A successful
// exchange revokes the old access token at the fake upstream, as the token
// endpoint does, and rotates the refresh token. The new access token is
// accepted only with accept set. With hold set, an exchange waits until hold
// is closed.
type tokenExchange struct {
	up      *fakeUpstream
	account string
	delay   time.Duration
	accept  bool
	fail    bool
	hold    chan struct{}

	mu       sync.Mutex
	calls    int
	active   int
	reused   int
	resent   int
	lastRT   string
	failedRT map[string]bool
}

func (x *tokenExchange) set(f func(x *tokenExchange)) {
	x.mu.Lock()
	defer x.mu.Unlock()
	f(x)
}

func (x *tokenExchange) Refresh(ctx context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	rt := refreshToken(a)
	_, byRouter := routerRefreshOf(ctx)
	x.mu.Lock()
	x.calls++
	if rt != x.lastRT {
		x.reused++
	}
	if byRouter && x.failedRT[rt] {
		x.resent++
	}
	x.active++
	delay, fail, accept, hold := x.delay, x.fail, x.accept, x.hold
	x.mu.Unlock()
	defer x.set(func(x *tokenExchange) { x.active-- })
	time.Sleep(delay)
	if hold != nil {
		<-hold
	}
	if fail {
		x.mu.Lock()
		if x.failedRT == nil {
			x.failedRT = map[string]bool{}
		}
		x.failedRT[rt] = true
		x.mu.Unlock()
		return nil, errors.New("token exchange failed")
	}
	old := accessToken(a)
	b := a.Clone()
	at := "sk-ant-oat01-fake-" + x.account + "-refreshed-" + randomHex(8)
	next := "rt-" + randomHex(8)
	x.up.mu.Lock()
	delete(x.up.accounts, hashToken(old))
	x.up.mu.Unlock()
	if accept {
		x.up.bind(at, x.account)
	}
	b.Metadata["access_token"] = at
	b.Metadata["refresh_token"] = next
	b.Metadata["expired"] = time.Now().Add(8 * time.Hour).UTC().Format(time.RFC3339)
	b.Metadata["last_refresh"] = time.Now().UTC().Format(time.RFC3339)
	x.mu.Lock()
	x.lastRT = next
	x.mu.Unlock()
	return b, nil
}

// exchanges counts the token exchanges. It fails the test if one sent a
// refresh token that an earlier exchange had replaced, or if a refresh the
// router started sent a refresh token whose exchange had failed.
func (x *tokenExchange) exchanges(t *testing.T) int {
	t.Helper()
	x.mu.Lock()
	defer x.mu.Unlock()
	if x.reused != 0 {
		t.Fatalf("%d token exchanges sent a replaced refresh token", x.reused)
	}
	if x.resent != 0 {
		t.Fatalf("%d refreshes the router started resent a refresh token whose exchange failed", x.resent)
	}
	return x.calls
}

// holdExchanges makes x's exchanges wait until release is called. The test
// releases them at the latest when it ends.
func (e *env) holdExchanges(x *tokenExchange) (release func()) {
	e.t.Helper()
	hold := make(chan struct{})
	var once sync.Once
	release = func() { once.Do(func() { close(hold) }) }
	e.t.Cleanup(release)
	x.set(func(x *tokenExchange) { x.hold = hold })
	return release
}

func (e *env) waitExchanging(x *tokenExchange) {
	e.t.Helper()
	e.waitFor("an exchange is in flight", 10*time.Second, func() bool {
		x.mu.Lock()
		defer x.mu.Unlock()
		return x.active == 1
	})
}

// sendBehindTheHeldExchange sends a request whose on-demand read must
// refresh, while a held exchange of the SDK's holds the credential's refresh
// lock. The read has to wait for that lock: the test fails if the send
// returns before release.
func (e *env) sendBehindTheHeldExchange(release func()) result {
	e.t.Helper()
	sent := make(chan result, 1)
	go func() { sent <- e.send(msg{Session: sessionID(1)}) }()
	select {
	case r := <-sent:
		e.t.Fatalf("the send returned %d while the SDK's exchange held the refresh lock, want it to wait for the lock", r.Status)
	case <-time.After(500 * time.Millisecond):
	}
	release()
	return <-sent
}

// replaceTokenExchange puts x behind the router's refresh guard. Test
// credentials carry no refresh token, so a refresh by the real executor
// before this point makes no request.
func (e *env) replaceTokenExchange(x *tokenExchange) {
	e.t.Helper()
	g := e.svc.guard
	g.mu.Lock()
	g.exchange = x.Refresh
	g.mu.Unlock()
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

// recheckNow advances the service clock by step. A step past the recheck
// interval makes the next send read an unknown setting on demand; a step
// past overage_fresh_for does the same for a disabled reading.
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

// waitPending waits until the SDK's refresh loop marks a refresh of the
// account's credential pending.
func (e *env) waitPending(account string) time.Time {
	e.t.Helper()
	var until time.Time
	e.waitFor("the SDK has a refresh pending", 10*time.Second, func() bool {
		until = e.sdkAuth(account).NextRefreshAfter
		return until.After(time.Now())
	})
	return until
}

// sdkRefreshWithoutMarker starts a refresh the way the SDK's request-time
// 401 path does: under the credential's refresh lock and with no pending
// marker. It returns once the refresh's exchange is in flight, held until
// release.
func (e *env) sdkRefreshWithoutMarker(x *tokenExchange, account string) (done <-chan struct{}, release func()) {
	e.t.Helper()
	release = e.holdExchanges(x)
	finished := make(chan struct{})
	go func() {
		defer close(finished)
		_, _ = e.svc.core.ForceRefreshAuth(context.Background(), e.svc.authIDs[router.AccountID(account)])
	}()
	e.waitExchanging(x)
	return finished, release
}

func TestRouterRefreshQueuedBehindAFailingSDKRefreshSendsNoExchange(t *testing.T) {
	e := newEnv(t, "acct-a")
	x := &tokenExchange{up: e.upstream, account: "acct-a", fail: true}
	e.startWithoutSDKRefresh(x)
	sdk, release := e.sdkRefreshWithoutMarker(x, "acct-a")

	e.recheckNow(31 * time.Minute)
	r := e.sendBehindTheHeldExchange(release)
	<-sdk

	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's", n)
	}
	if n := e.refreshEvents("failed"); n != 1 {
		t.Fatalf("failed credential_refresh events %d, want 1", n)
	}
}

func TestDueReadUsesTheTokenTheSDKInstalledWhileItWaitedForTheLock(t *testing.T) {
	e := newEnv(t, "acct-a")
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	e.startWithoutSDKRefresh(x)
	sdk, release := e.sdkRefreshWithoutMarker(x, "acct-a")

	e.recheckNow(31 * time.Minute)
	r := e.sendBehindTheHeldExchange(release)
	<-sdk

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a on the SDK's token", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the router does not rotate the SDK's new token", n)
	}
	if n, m := e.refreshEvents("sdk_refreshed"), e.refreshEvents("refreshed"); n != 1 || m != 0 {
		t.Fatalf("sdk_refreshed %d and refreshed %d events, want 1 and 0", n, m)
	}
}

// The SDK's refresh job outlives its 60 s pending marker, so a read after
// the marker finds no refresh pending and refreshes through the SDK, behind
// the job. The test waits out the SDK's real marker; it runs in parallel
// with the other test that does.
func TestRouterRefreshAfterTheSDKMarkerExpiresSendsNoExchange(t *testing.T) {
	t.Parallel()
	e := newEnv(t, "acct-a")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", fail: true}
	release := e.holdExchanges(x)
	e.replaceTokenExchange(x)
	marker := e.waitPending("acct-a")
	e.waitExchanging(x)
	time.Sleep(time.Until(marker) + time.Second)

	e.recheckNow(31 * time.Minute)
	r := e.sendBehindTheHeldExchange(release)

	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's", n)
	}
	if n := e.refreshEvents("failed"); n != 1 {
		t.Fatalf("failed credential_refresh events %d, want 1", n)
	}
}

func TestSDKRefreshThatFailsDuringTheWaitFailsTheReadAtOnce(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", delay: time.Second, fail: true}
	e.replaceTokenExchange(x)
	e.waitPending("acct-a")

	e.recheckNow(31 * time.Minute)
	begun := time.Now()
	r := e.send(msg{Session: sessionID(1)})
	took := time.Since(begun)
	for i := 0; i < 3; i++ {
		e.recheckNow(31 * time.Second)
		if r := e.send(msg{Session: sessionID(1)}); r.Status != http.StatusServiceUnavailable {
			t.Fatalf("send %d after the failure got %d, want 503", i, r.Status)
		}
	}

	if r.Status != http.StatusServiceUnavailable || took >= sdkRefreshWait {
		t.Fatalf("got %d after %s, want 503 before the %s wait ends", r.Status, took, sdkRefreshWait)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's", n)
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("inference attempts %d, want 0", n)
	}
	var details []string
	for _, ev := range e.events() {
		if ev.Kind == "credential_refresh" && ev.Outcome == "failed" {
			details = append(details, ev.Detail)
		}
	}
	if len(details) != 1 || details[0] != "sdk refresh" {
		t.Fatalf("failed credential_refresh details %q, want one \"sdk refresh\"", details)
	}
}

func TestInferenceErrorDuringAPendingSDKRefreshDoesNotFailTheRead(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", delay: time.Second, accept: true}
	e.replaceTokenExchange(x)
	e.waitPending("acct-a")
	id := e.svc.authIDs["acct-a"]
	e.svc.core.MarkResult(context.Background(), coreauth.Result{AuthID: id, Provider: "claude", Model: "claude-sonnet-4-5",
		Error: &coreauth.Error{Code: "rate_limited", Message: "inference 429", HTTPStatus: http.StatusTooManyRequests}})
	if e.sdkAuth("acct-a").LastError == nil {
		t.Fatal("the SDK did not record the inference error on the credential")
	}

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a once the SDK's refresh landed", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1", n)
	}
	if n, m := e.refreshEvents("sdk_refreshed"), e.refreshEvents("failed"); n != 1 || m != 0 {
		t.Fatalf("sdk_refreshed %d and failed %d events, want 1 and 0", n, m)
	}
}

func TestFailureRunEndsWhenTheAccessTokenChanges(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	e.svc.core.StopAutoRefresh()
	x := &tokenExchange{up: e.upstream, account: "acct-a", fail: true}
	e.replaceTokenExchange(x)
	e.upstream.mu.Lock()
	delete(e.upstream.accounts, hashToken(e.tokens["acct-a"]))
	e.upstream.mu.Unlock()
	e.recheckNow(31 * time.Minute)
	if r := e.send(msg{Session: sessionID(1)}); r.Status != http.StatusServiceUnavailable {
		t.Fatalf("after the failed refresh got %d, want 503", r.Status)
	}
	x.set(func(x *tokenExchange) { x.fail = false })
	if _, err := e.svc.core.ForceRefreshAuth(context.Background(), e.svc.authIDs["acct-a"]); err != nil {
		t.Fatalf("SDK refresh: %v", err)
	}
	x.set(func(x *tokenExchange) { x.accept = true })

	e.recheckNow(31 * time.Second)
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want 200 from acct-a: the SDK's new token ended the failure run", r.Status, r.Text)
	}
	if n, m := x.exchanges(t), e.refreshEvents("refreshed"); n != 3 || m != 1 {
		t.Fatalf("token exchanges %d and router refreshes %d, want 3 and 1", n, m)
	}
}

func TestFailedRecheckBringsTheReRead(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.OverageCheckEvery = Duration(time.Minute)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: 300 * time.Millisecond})
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})

	e.recheckNow(31 * time.Minute)
	r := e.send(msg{Session: sessionID(1)})
	e.waitFor("the re-read", 5*time.Second, func() bool { return len(e.upstream.usageReads()) >= 3 })

	if r.Status != http.StatusServiceUnavailable {
		t.Fatalf("got %d, want 503 after the recheck failed", r.Status)
	}
	reads := e.upstream.usageReads()
	if gap := reads[2].At.Sub(reads[1].At); gap < 300*time.Millisecond || gap > 2*time.Second {
		t.Fatalf("re-read %s after the failed recheck, want about 300 ms", gap)
	}
}
