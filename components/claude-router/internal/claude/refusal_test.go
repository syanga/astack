package claude

import (
	"errors"
	"path/filepath"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

const sdkErrorForExpiredRefreshToken = `token refresh failed after 3 attempts: token refresh failed with status 400: {"error": "invalid_grant", "error_description": "Refresh token expired"}`

func (e *env) needsLogin(account string) bool {
	e.t.Helper()
	for _, a := range e.svc.Status().Accounts {
		if string(a.ID) == account {
			return a.NeedsLogin
		}
	}
	e.t.Fatalf("status lists no account %s", account)
	return false
}

func (e *env) loginRequired(account string) int {
	n := 0
	for _, ev := range e.events() {
		if ev.Kind == "login_required" && string(ev.Account) == account && ev.Reason == "refresh_refused" {
			n++
		}
	}
	return n
}

func (e *env) refuseAccessToken(account string) {
	e.upstream.mu.Lock()
	delete(e.upstream.accounts, hashToken(e.tokens[account]))
	e.upstream.mu.Unlock()
}

func TestSDKRefreshRefusedForAnExpiredRefreshTokenMarksTheAccountAsNeedingLogin(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	x := &tokenExchange{up: e.upstream, account: "acct-a", failErr: errors.New(sdkErrorForExpiredRefreshToken)}
	e.replaceTokenExchange(x)

	e.waitFor("the SDK's refresh failed", 10*time.Second, func() bool { return e.sdkAuth("acct-a").LastError != nil })
	r := e.send(msg{Session: sessionID(1)})

	if !e.needsLogin("acct-a") {
		t.Fatal("acct-a needs no login after the token endpoint refused its refresh token")
	}
	if r.Status != 200 || r.Text != served("acct-b") {
		t.Fatalf("got %d %q, want 200 from acct-b: placement skips an account that needs login", r.Status, r.Text)
	}
	if n := x.exchanges(t); n != 1 {
		t.Fatalf("token exchanges %d, want 1: the SDK's", n)
	}
	if n := e.loginRequired("acct-a"); n != 1 {
		t.Fatalf("login_required events for acct-a %d, want 1", n)
	}
}

func TestOnlyARefusedRefreshTokenMarksTheAccountAsNeedingLogin(t *testing.T) {
	cases := []struct {
		name    string
		err     string
		refused bool
	}{
		{"expired refresh token", sdkErrorForExpiredRefreshToken, true},
		{"revoked refresh token answered 401",
			`token refresh failed after 3 attempts: token refresh failed with status 401: {"error":"invalid_grant","error_description":"Refresh token revoked"}`, true},
		{"server error",
			`token refresh failed after 3 attempts: token refresh failed with status 503: {"error":"overloaded"}`, false},
		{"server error whose body quotes a refusal",
			`token refresh failed after 3 attempts: token refresh failed with status 503: upstream said token refresh failed with status 400: {"error":"invalid_grant"}`, false},
		{"rate limit",
			`token refresh failed after 3 attempts: token refresh failed with status 429: refresh temporarily blocked until 2026-11-19T17:00:00Z`, false},
		{"transport error",
			`token refresh failed after 3 attempts: token refresh request failed: dial tcp: lookup platform.claude.com: i/o timeout`, false},
		{"malformed request",
			`token refresh failed after 3 attempts: token refresh failed with status 400: {"error":"invalid_request","error_description":"missing grant_type"}`, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			e := newEnv(t, "acct-a")
			x := &tokenExchange{up: e.upstream, account: "acct-a", failErr: errors.New(c.err)}
			e.startWithoutSDKRefresh(x)

			e.recheckNow(31 * time.Minute)
			e.send(msg{Session: sessionID(1)})

			if n := x.exchanges(t); n != 1 {
				t.Fatalf("token exchanges %d, want 1: the router's", n)
			}
			if got := e.needsLogin("acct-a"); got != c.refused {
				t.Fatalf("needs login %t after the exchange failed with %q, want %t", got, c.err, c.refused)
			}
		})
	}
}

func TestRefusalOfAnSDKRefreshThatBeganBeforeTheGuardMarksTheAccountAsNeedingLogin(t *testing.T) {
	cases := []struct {
		name                string
		endedBeforeTheGuard bool
	}{
		{"refused after the guard is registered", false},
		{"refused before the guard is registered", true},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			t.Parallel()
			e := newEnv(t, "acct-a")
			x := &tokenExchange{up: e.upstream, account: "acct-a", failErr: errors.New(sdkErrorForExpiredRefreshToken)}
			release := e.holdExchanges(x)
			e.startWithSDKRefreshBeforeTheGuard("acct-a", x)
			if c.endedBeforeTheGuard {
				release()
				e.waitFor("the SDK recorded the refusal", 10*time.Second, func() bool { return e.sdkAuth("acct-a").LastError != nil })
			} else {
				time.AfterFunc(time.Second, release)
			}
			e.registerGuard()

			e.recheckNow(31 * time.Minute)
			e.send(msg{Session: sessionID(1)})

			if !e.needsLogin("acct-a") {
				t.Fatal("acct-a needs no login after the token endpoint refused its refresh token")
			}
			if n := x.exchanges(t); n != 1 {
				t.Fatalf("token exchanges %d, want 1: the SDK's", n)
			}
			if n := e.loginRequired("acct-a"); n != 1 {
				t.Fatalf("login_required events for acct-a %d, want 1", n)
			}
		})
	}
}

func (e *env) refuseRefreshOfAcctA() result {
	e.t.Helper()
	e.clock.set(time.Now())
	e.startWith(Options{ReadRetryBase: time.Hour})
	e.replaceTokenExchange(&tokenExchange{up: e.upstream, account: "acct-a", failErr: errors.New(sdkErrorForExpiredRefreshToken)})
	e.refuseAccessToken("acct-a")
	e.recheckNow(31 * time.Minute)
	return e.send(msg{Session: sessionID(1)})
}

func TestASettingsReadThatSucceedsAfterARefusalKeepsTheAccountNeedingLogin(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.refuseRefreshOfAcctA()
	marked := e.needsLogin("acct-a")
	e.upstream.bind(e.tokens["acct-a"], "acct-a")
	before := len(e.upstream.usageReads())

	e.recheckNow(31 * time.Minute)
	read := e.send(msg{Session: sessionID(2)})
	e.restart()
	afterRestart := e.needsLogin("acct-a")
	restarted := e.send(msg{Session: sessionID(3)})

	accepted := 0
	for _, u := range e.upstream.usageReads()[before:] {
		if u.Account == "acct-a" {
			accepted++
		}
	}
	if !marked || accepted == 0 {
		t.Fatalf("needs login %t after the refusal and %d accepted settings reads of acct-a after, want true and at least 1", marked, accepted)
	}
	for i, r := range []result{read, restarted} {
		if r.Status != 200 || r.Text != served("acct-b") {
			t.Fatalf("send %d got %d %q, want 200 from acct-b", i+2, r.Status, r.Text)
		}
	}
	if !afterRestart || e.eventCount("login_confirmed") != 0 {
		t.Fatalf("needs login %t after a restart with %d login_confirmed events, want true and none", afterRestart, e.eventCount("login_confirmed"))
	}
}

func TestAnAccountWhoseRefreshTokenWasRefusedNeedsLoginAfterARestartUntilItSignsInAgain(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	refused := e.refuseRefreshOfAcctA()

	e.restart()
	afterRestart := e.needsLogin("acct-a")
	restarted := e.send(msg{Session: sessionID(2)})

	e.svc.Close()
	e.writeCredential("acct-a")
	store, err := router.OpenStore(filepath.Join(e.dir, "assignments"))
	if err != nil {
		t.Fatal(err)
	}
	if err := store.ClearNeedsLogin("acct-a"); err != nil {
		t.Fatal(err)
	}
	store.Close()
	e.start()
	signedIn := e.send(msg{Session: sessionID(3)})

	for i, r := range []result{refused, restarted} {
		if r.Status != 200 || r.Text != served("acct-b") {
			t.Fatalf("send %d got %d %q, want 200 from acct-b", i+1, r.Status, r.Text)
		}
	}
	if !afterRestart {
		t.Fatal("acct-a needs no login after a restart, want the login requirement kept")
	}
	if e.needsLogin("acct-a") || signedIn.Status != 200 || signedIn.Text != served("acct-a") {
		t.Fatalf("after the new login got %d %q with needs login %t, want 200 from acct-a and no login needed",
			signedIn.Status, signedIn.Text, e.needsLogin("acct-a"))
	}
}
