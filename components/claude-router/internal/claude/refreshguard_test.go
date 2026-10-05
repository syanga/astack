package claude

import (
	"context"
	"errors"
	"maps"
	"net/http"
	"slices"
	"testing"
	"time"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
)

func guardAuth(at, rt string) *coreauth.Auth {
	return &coreauth.Auth{ID: "acct-a.json", Provider: "claude", Metadata: map[string]any{"access_token": at, "refresh_token": rt}}
}

// scriptedExchange answers each exchange with the next refresh token in
// next, or fails when that entry is empty, and records the refresh tokens
// it was sent.
type scriptedExchange struct {
	next []string
	sent []string
}

func (x *scriptedExchange) Refresh(_ context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	x.sent = append(x.sent, refreshToken(a))
	rt := x.next[0]
	x.next = x.next[1:]
	if rt == "" {
		return nil, errors.New("token exchange failed")
	}
	b := a.Clone()
	b.Metadata["access_token"] = "at-after-" + rt
	b.Metadata["refresh_token"] = rt
	return b, nil
}

func TestRefreshGuardDeclinesRouterRefreshesOfSpentTokens(t *testing.T) {
	cases := []struct {
		name    string
		sdk     []string
		sdkRT   string
		router  *coreauth.Auth
		observe string
		want    []string
	}{
		{"after the SDK's exchange failed", []string{""}, "rt-0", guardAuth("at-0", "rt-0"), "at-0", []string{"rt-0"}},
		{"after the SDK's exchange rotated it", []string{"rt-1"}, "rt-0", guardAuth("at-0", "rt-0"), "at-0", []string{"rt-0"}},
		{"of the token the SDK installed", []string{"rt-1"}, "rt-0", guardAuth("at-after-rt-1", "rt-1"), "at-after-rt-1", []string{"rt-0", "rt-1"}},
		{"after an exchange that kept the token", []string{"rt-0"}, "rt-0", guardAuth("at-after-rt-0", "rt-0"), "at-after-rt-0", []string{"rt-0", "rt-0"}},
		{"decided on an access token since replaced", nil, "", guardAuth("at-1", "rt-1"), "at-0", nil},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			x := &scriptedExchange{next: append(append([]string(nil), c.sdk...), "rt-9")}
			g := &refreshGuard{exchange: x.Refresh, spent: map[string][]spentToken{}}
			if c.sdkRT != "" {
				_, _ = g.Refresh(context.Background(), guardAuth("at-0", c.sdkRT))
			}

			_, err := g.Refresh(withRouterRefresh(context.Background(), c.observe), c.router)

			declined := len(x.sent) == len(c.sdk)
			if declined != errors.Is(err, errRefreshDeclined) || (declined && !errors.Is(err, context.Canceled)) {
				t.Fatalf("err %v with exchanges %v", err, x.sent)
			}
			if len(x.sent) != len(c.want) {
				t.Fatalf("exchanges sent %v, want %v", x.sent, c.want)
			}
			for i := range c.want {
				if x.sent[i] != c.want[i] {
					t.Fatalf("exchanges sent %v, want %v", x.sent, c.want)
				}
			}
		})
	}
}

func TestRouterRefreshesOnlyWhileTheGuardIsTheSDKExecutor(t *testing.T) {
	e := newEnv(t, "acct-a")
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	e.startWithoutSDKRefresh(x)
	guard := e.svc.guard
	e.svc.core.RegisterExecutor(guard.ProviderExecutor)

	e.recheckNow(31 * time.Minute)
	refused := []result{e.send(msg{Session: sessionID(1)})}
	e.recheckNow(31 * time.Second)
	refused = append(refused, e.send(msg{Session: sessionID(1)}))
	exchangesWithoutGuard := x.exchanges(t)
	e.svc.core.RegisterExecutor(guard)
	e.recheckNow(31 * time.Second)
	r := e.send(msg{Session: sessionID(1)})

	for i, refused := range refused {
		if refused.Status != http.StatusServiceUnavailable {
			t.Fatalf("send %d without the guard got %d, want 503", i, refused.Status)
		}
	}
	if exchangesWithoutGuard != 0 {
		t.Fatalf("token exchanges %d without the guard, want 0", exchangesWithoutGuard)
	}
	if n := e.eventCount("refresh_guard_replaced"); n != 1 {
		t.Fatalf("refresh_guard_replaced events %d, want 1", n)
	}
	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("with the guard registered again got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
	if n, m := x.exchanges(t), e.refreshEvents("refreshed"); n != 1 || m != 1 {
		t.Fatalf("token exchanges %d and router refreshes %d with the guard, want 1 and 1", n, m)
	}
}

func (e *env) eventCount(kind string) int {
	n := 0
	for _, ev := range e.events() {
		if ev.Kind == kind {
			n++
		}
	}
	return n
}

// executorBeforeTheGuard stands in for the SDK's Claude executor as it is
// before the router registers its guard, with x as its token exchange.
type executorBeforeTheGuard struct {
	coreauth.ProviderExecutor
	x *tokenExchange
}

func (b executorBeforeTheGuard) Refresh(ctx context.Context, a *coreauth.Auth) (*coreauth.Auth, error) {
	return b.x.Refresh(ctx, a)
}

// startWithSDKRefreshBeforeTheGuard reproduces the start of a service whose
// credential is due: the SDK's auto-refresh job begins its exchange before
// the router registers the guard. The service starts with a credential that
// becomes due after start, the guard's executor is swapped for one with no
// guard, and the function returns once the job's exchange is in flight on
// it. registerGuard then does what Start does next.
func (e *env) startWithSDKRefreshBeforeTheGuard(account string, x *tokenExchange) {
	e.t.Helper()
	e.writeCredentialDueIn(account, 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	e.svc.core.RegisterExecutor(executorBeforeTheGuard{ProviderExecutor: e.svc.guard.ProviderExecutor, x: x})
	e.waitPending(account)
	e.waitExchanging(x)
}

func (e *env) registerGuard() {
	e.t.Helper()
	g, err := installRefreshGuard(e.svc.core, slices.Collect(maps.Values(e.svc.authIDs)))
	if err != nil {
		e.t.Fatal(err)
	}
	e.svc.guard = g
}

func (e *env) sdkRefreshFailures() []string {
	var details []string
	for _, ev := range e.events() {
		if ev.Kind == "credential_refresh" && ev.Outcome == "failed" {
			details = append(details, ev.Detail)
		}
	}
	return details
}

func TestSDKRefreshThatBeganBeforeTheGuardAndFailsFailsTheReadAtOnce(t *testing.T) {
	e := newEnv(t, "acct-a")
	x := &tokenExchange{up: e.upstream, account: "acct-a", delay: 2 * time.Second, fail: true}
	e.startWithSDKRefreshBeforeTheGuard("acct-a", x)
	e.registerGuard()

	e.recheckNow(31 * time.Minute)
	begun := time.Now()
	r := e.send(msg{Session: sessionID(1)})
	took := time.Since(begun)

	if r.Status != http.StatusServiceUnavailable || took >= sdkRefreshWait {
		t.Fatalf("got %d after %s, want 503 before the %s wait ends", r.Status, took, sdkRefreshWait)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's", n)
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("inference attempts %d, want 0", n)
	}
	if d := e.sdkRefreshFailures(); len(d) != 1 || d[0] != "sdk refresh" {
		t.Fatalf("failed credential_refresh details %q, want one \"sdk refresh\"", d)
	}
}

// The SDK's job outlives its 60 s pending marker before the guard is
// registered, as after a start stalled that long, and fails while the
// router's refresh waits for the lock. The test waits out the SDK's real
// marker; it runs in parallel with the other test that does.
func TestSDKRefreshThatBeganBeforeTheGuardAndOutlivedItsMarkerIsNotResent(t *testing.T) {
	t.Parallel()
	e := newEnv(t, "acct-b")
	x := &tokenExchange{up: e.upstream, account: "acct-b", fail: true}
	release := e.holdExchanges(x)
	e.startWithSDKRefreshBeforeTheGuard("acct-b", x)
	time.Sleep(time.Until(e.sdkAuth("acct-b").NextRefreshAfter) + time.Second)
	e.registerGuard()

	e.recheckNow(31 * time.Minute)
	r := e.sendBehindTheHeldExchange(release)

	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's", n)
	}
	if d := e.sdkRefreshFailures(); len(d) != 1 || d[0] != "sdk refresh" {
		t.Fatalf("failed credential_refresh details %q, want one \"sdk refresh\"", d)
	}
}
