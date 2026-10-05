package claude

import (
	"context"
	"errors"
	"net/http"
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
