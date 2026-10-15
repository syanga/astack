package router

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

// openDurable opens the harness's store with a router whose account state
// survives restarts, as the service does.
func (h *harness) openDurable(now time.Time) {
	h.t.Helper()
	s, err := OpenStore(h.dir)
	if err != nil {
		h.t.Fatalf("open store: %v", err)
	}
	r, err := Open(h.cfg, h.accounts, s, now)
	if err != nil {
		s.Close()
		h.t.Fatalf("open router: %v", err)
	}
	h.store, h.r = s, r
}

func (h *harness) restartDurable(now time.Time) {
	h.t.Helper()
	if err := h.store.Close(); err != nil {
		h.t.Fatal(err)
	}
	h.store = nil
	h.openDurable(now)
}

func newDurable(t *testing.T, accounts ...Account) *harness {
	t.Helper()
	h := &harness{t: t, dir: t.TempDir(), cfg: cfgWith(ResetAware), accounts: accounts, manualOverage: true}
	h.openDurable(t0)
	t.Cleanup(func() {
		if h.store != nil {
			h.store.Close()
		}
	})
	return h
}

func (h *harness) decide(now time.Time, conv string) Decision {
	h.t.Helper()
	d, err := h.r.Decide(now, req(conv))
	if err != nil {
		h.t.Fatalf("decide %s: %v", conv, err)
	}
	return d
}

func TestExhaustionLoginAndOverageStateSurviveRestart(t *testing.T) {
	h := newDurable(t, accts(a, 1.0, b, 1.0, c, 1.0)...)
	for _, acct := range []AccountID{a, b, c} {
		h.r.ObserveOverage(acct, OverageDisabled, t0)
	}
	if _, err := h.r.CommitAssignment(t0, "on-a", a, ReasonCapacity); err != nil {
		t.Fatal(err)
	}
	if _, err := h.r.CommitAssignment(t0, "on-b", b, ReasonCapacity); err != nil {
		t.Fatal(err)
	}
	if err := h.r.Served(t0, "on-b", b); err != nil {
		t.Fatal(err)
	}
	reset := t0.Add(3 * time.Hour)
	h.report(t0.Add(time.Minute), a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, reset))
	h.report(t0.Add(time.Minute), b, "claude-sonnet-4-5", ClassAuth)
	h.r.ObserveOverage(c, OveragePaidUse, t0.Add(2*time.Minute))

	h.restartDurable(t0.Add(10 * time.Minute))
	later := t0.Add(10 * time.Minute)

	if d := h.decide(later, "new"); d.Kind != Wait || !d.Until.Equal(reset) || !d.ResetKnown {
		t.Fatalf("new conversation after restart: %+v, want a wait until acct-a's reset %v (acct-b needs login, acct-c showed paid use)", d, reset)
	}
	if d := h.decide(later, "on-b"); d.Kind != Reauth || d.Account != b {
		t.Fatalf("served conversation on the logged-out account after restart: %+v, want reauth on acct-b", d)
	}
	h.r.ObserveOverage(a, OverageDisabled, reset)
	if d := h.decide(reset, "on-a"); d.Kind != Dispatch || d.Account != a {
		t.Fatalf("at acct-a's reset: %+v, want dispatch on acct-a", d)
	}
}

func TestRestartBeforeTheResetNeverDispatchesToTheExhaustedAccount(t *testing.T) {
	h := newDurable(t, accts(a, 1.0)...)
	h.r.ObserveOverage(a, OverageDisabled, t0)
	if _, err := h.r.CommitAssignment(t0, "conv", a, ReasonCapacity); err != nil {
		t.Fatal(err)
	}
	reset := t0.Add(time.Hour)
	h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, reset), rejected(Weekly, reset.Add(time.Hour)))

	for _, at := range []time.Time{t0.Add(time.Minute), reset.Add(time.Minute)} {
		h.restartDurable(at)
		h.r.ObserveOverage(a, OverageDisabled, at)
		if d := h.decide(at, "conv"); d.Kind != Wait || !d.Until.Equal(reset.Add(time.Hour)) {
			t.Fatalf("after a restart at %v: %+v, want a wait until the weekly reset", at, d)
		}
	}
}

func TestAMemoryOnlyRouterForgetsAccountStateAtRestart(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.report(t0, a, "claude-sonnet-4-5", ClassAuth)

	h.restart()

	if _, err := os.Stat(filepath.Join(h.dir, accountsName)); !os.IsNotExist(err) {
		t.Fatalf("New wrote account state (%v); only Open persists it", err)
	}
	if d := h.decide(t0, "conv"); d.Kind != Refuse || !d.RecheckOverage {
		t.Fatalf("memory-only router after restart: %+v, want refuse with a recheck: the check and the login failure are gone", d)
	}
}

func TestClearNeedsLoginAppliesAtTheNextStart(t *testing.T) {
	h := newDurable(t, accts(a, 1.0)...)
	h.r.ObserveOverage(a, OverageDisabled, t0)
	if _, err := h.r.CommitAssignment(t0, "conv", a, ReasonCapacity); err != nil {
		t.Fatal(err)
	}
	h.report(t0, a, "claude-sonnet-4-5", ClassAuth)
	if err := h.store.Close(); err != nil {
		t.Fatal(err)
	}

	login, err := OpenStore(h.dir)
	if err != nil {
		t.Fatal(err)
	}
	if err := login.ClearNeedsLogin(a); err != nil {
		t.Fatal(err)
	}
	login.Close()
	h.openDurable(t0.Add(time.Minute))

	if d := h.decide(t0.Add(time.Minute), "conv"); d.Kind != Dispatch || d.Account != a {
		t.Fatalf("after login: %+v, want dispatch on acct-a", d)
	}
}

func TestOnlyASuccessStartedAfterTheAuthFailureConfirmsTheLogin(t *testing.T) {
	h := newDurable(t, accts(a, 1.0)...)
	h.r.ObserveOverage(a, OverageDisabled, t0)
	if _, err := h.r.CommitAssignment(t0, "conv", a, ReasonCapacity); err != nil {
		t.Fatal(err)
	}
	failedAt := t0.Add(time.Minute)
	h.report(failedAt, a, "claude-sonnet-4-5", ClassAuth)

	if h.r.LoginConfirmed(a, failedAt) {
		t.Fatal("a success that started with the failed attempt cleared the login requirement")
	}
	if d := h.decide(failedAt, "conv"); d.Kind != Reauth {
		t.Fatalf("before a later success: %+v, want reauth", d)
	}
	if !h.r.LoginConfirmed(a, failedAt.Add(time.Second)) {
		t.Fatal("a success that started after the failure did not clear the login requirement")
	}
	h.restartDurable(failedAt.Add(time.Minute))
	if d := h.decide(failedAt.Add(time.Minute), "conv"); d.Kind != Dispatch {
		t.Fatalf("after a confirmed login and a restart: %+v, want dispatch", d)
	}
}

func TestSavedStampsLaterThanTheClockAreClampedOnLoad(t *testing.T) {
	h := newDurable(t, accts(a, 1.0, b, 1.0)...)
	future := t0.Add(time.Hour)
	h.r.ObserveOverage(a, OverageDisabled, future)
	h.r.ObserveOverage(b, OverageEnabled, future)
	h.report(future, b, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, future.Add(time.Hour)))

	h.restartDurable(t0)

	if d := h.decide(t0, "conv"); d.Kind != Refuse || !d.RecheckOverage {
		t.Fatalf("after a restart with a clock behind the saved stamps: %+v, want refuse with a recheck (acct-a's disabled check dropped, acct-b enabled)", d)
	}
	for _, st := range h.r.AccountStates() {
		if st.ID == b && (len(st.Rejections) != 1 || st.Rejections[0].At.After(t0) || !st.CheckAt.Equal(t0)) {
			t.Fatalf("acct-b state %+v, want its rejection and enabled check kept and stamped no later than now", st)
		}
	}
}

func TestFailedAccountStateWriteStopsTheRouterUntilReopen(t *testing.T) {
	h := newDurable(t, accts(a, 1.0)...)
	h.r.ObserveOverage(a, OverageDisabled, t0)
	if err := os.Chmod(h.dir, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(h.dir, 0o700) })

	h.report(t0, a, "claude-sonnet-4-5", ClassAuth)

	if _, err := h.r.Decide(t0, req("conv")); err == nil || !strings.Contains(err.Error(), "reopen") {
		t.Fatalf("decide after a failed account state write: %v, want the store's fail-stop error", err)
	}
}

func TestUnknownResetRecheckBeyondAWeekIsRefused(t *testing.T) {
	cfg := cfgWith(ResetAware)
	cfg.UnknownResetRecheck = Weekly.Period() + time.Second
	if err := cfg.Validate(); err == nil {
		t.Fatal("an unknown-reset recheck longer than the weekly pruning horizon validated")
	}
	cfg.UnknownResetRecheck = Weekly.Period()
	if err := cfg.Validate(); err != nil {
		t.Fatalf("a one-week recheck: %v", err)
	}
}

func TestAnAccountLeftOutOfTheConfigurationKeepsItsStateUntilItReturns(t *testing.T) {
	h := newDurable(t, accts(a, 1.0, b, 1.0)...)
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.r.ObserveOverage(b, OverageDisabled, t0)
	reset := t0.Add(3 * time.Hour)
	h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, reset))
	both := h.accounts

	h.accounts = accts(b, 1.0)
	h.restartDurable(t0.Add(time.Minute))
	h.r.ObserveOverage(b, OverageEnabled, t0.Add(2*time.Minute))
	h.accounts = both
	later := t0.Add(10 * time.Minute)
	h.restartDurable(later)
	h.r.ObserveOverage(a, OverageDisabled, later)
	d := h.decide(later, "new")

	if d.Kind != Wait || !d.Until.Equal(reset) {
		t.Fatalf("a new conversation after acct-a returned got %+v, want a wait until acct-a's reset %v", d, reset)
	}
}
