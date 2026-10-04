package router

import (
	"bytes"
	"errors"
	"fmt"
	"math"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"sync"
	"testing"
	"time"
)

var t0 = time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)

const (
	a AccountID = "acct-a"
	b AccountID = "acct-b"
	c AccountID = "acct-c"
)

func cfgWith(rule Rule) Config {
	return Config{
		Rule:                rule,
		ResetBias:           2,
		FreshFor:            15 * time.Minute,
		ActiveFor:           time.Hour,
		UnknownResetRecheck: 5 * time.Minute,
		OverageFreshFor:     time.Hour,
		MaxAttempts:         3,
	}
}

type harness struct {
	t             *testing.T
	manualOverage bool
	dir           string
	cfg           Config
	accounts      []Account
	store         *Store
	r             *Router
}

func newHarness(t *testing.T, cfg Config, accounts ...Account) *harness {
	t.Helper()
	h := &harness{t: t, dir: t.TempDir(), cfg: cfg, accounts: accounts}
	h.open()
	t.Cleanup(func() {
		if h.store != nil {
			h.store.Close()
		}
	})
	return h
}

func (h *harness) open() {
	h.t.Helper()
	s, err := OpenStore(h.dir)
	if err != nil {
		h.t.Fatalf("open store: %v", err)
	}
	r, err := New(h.cfg, h.accounts, s)
	if err != nil {
		h.t.Fatalf("new router: %v", err)
	}
	h.store, h.r = s, r
}

func (h *harness) restart() {
	h.t.Helper()
	if err := h.store.Close(); err != nil {
		h.t.Fatal(err)
	}
	h.store = nil
	h.open()
}

func (h *harness) route(now time.Time, req Request) Decision {
	h.t.Helper()
	if !h.manualOverage {
		for _, acct := range h.accounts {
			h.r.ObserveOverage(acct.ID, OverageDisabled, now)
		}
	}
	d, err := h.r.Route(now, req)
	if err != nil {
		h.t.Fatalf("route %s: %v", req.Conversation, err)
	}
	return d
}

func (h *harness) report(at time.Time, acct AccountID, model string, class Class, ws ...Window) Class {
	return h.r.Report(Failure{Account: acct, Model: model, Class: class, AttemptStart: at,
		Observation: Observation{At: at, Windows: ws}})
}

// observe records response headers whose attempt started when they were
// observed.
func (h *harness) observe(acct AccountID, obs Observation) {
	h.r.Observe(acct, obs.At, obs)
}

func (h *harness) bound(conv ConversationID) AccountID {
	h.t.Helper()
	bnd, ok, err := h.r.Lookup(conv)
	if err != nil {
		h.t.Fatalf("lookup %s: %v", conv, err)
	}
	if !ok {
		return ""
	}
	return bnd.Account
}

func req(conv string) Request {
	return Request{Conversation: ConversationID(conv), Model: "claude-sonnet-4-5", Attempt: 1}
}

func ptr(f float64) *float64 { return &f }

func usage(kind WindowKind, used float64, resets time.Time) Window {
	return Window{Kind: kind, Utilization: ptr(used), ResetsAt: resets}
}

func rejected(kind WindowKind, resets time.Time) Window {
	return Window{Kind: kind, Utilization: ptr(1), Rejected: true, ResetsAt: resets}
}

func accts(spec ...any) []Account {
	var out []Account
	for i := 0; i < len(spec); i += 2 {
		out = append(out, Account{ID: spec[i].(AccountID), Capacity: spec[i+1].(float64)})
	}
	return out
}

func TestNewConversationsFollowCapacityRatioUnderEqualQuota(t *testing.T) {
	for _, rule := range []Rule{CapacityOnly, ResetAware} {
		t.Run(string(rule), func(t *testing.T) {
			h := newHarness(t, cfgWith(rule), accts(a, 1.0, b, 2.0, c, 1.0)...)
			var got []AccountID
			for i := range 8 {
				d := h.route(t0.Add(time.Duration(i)*time.Minute), req(fmt.Sprintf("conv-%d", i)))
				got = append(got, d.Account)
			}

			want := []AccountID{b, a, b, c, b, a, b, c}
			if !slices.Equal(got, want) {
				t.Fatalf("placements = %v, want %v", got, want)
			}
		})
	}
}

func TestIdleConversationsStopCountingAsWorkload(t *testing.T) {
	h := newHarness(t, cfgWith(CapacityOnly), accts(a, 1.0, b, 1.0)...)
	for _, conv := range []ConversationID{"old-1", "old-2", "old-3"} {
		if _, err := h.r.CommitAssignment(t0, conv, a, ReasonManual); err != nil {
			t.Fatal(err)
		}
	}

	whileActive := h.route(t0.Add(30*time.Minute), req("mid"))
	afterIdle := h.route(t0.Add(75*time.Minute), req("late"))

	if whileActive.Account != b {
		t.Fatalf("with three active conversations on a, mid went to %s, want %s", whileActive.Account, b)
	}
	if afterIdle.Account != a {
		t.Fatalf("with a's conversations idle past ActiveFor and mid active on b, late went to %s, want %s", afterIdle.Account, a)
	}
}

func TestResetAwarePlacementFavorsUnusedAllowanceNearResetWithoutMovingHealthyWork(t *testing.T) {
	cases := []struct {
		rule Rule
		want []AccountID
	}{
		{CapacityOnly, []AccountID{a, b, a, b, a, b}},
		{ResetAware, []AccountID{a, a, a, a, b, a}},
	}
	for _, tc := range cases {
		t.Run(string(tc.rule), func(t *testing.T) {
			h := newHarness(t, cfgWith(tc.rule), accts(a, 1.0, b, 1.0)...)
			h.route(t0.Add(-10*time.Minute), req("old-1"))
			h.route(t0.Add(-10*time.Minute), req("old-2"))
			h.observe(a, Observation{At: t0, Windows: []Window{
				usage(FiveHour, 0.1, t0.Add(30*time.Minute)),
				usage(Weekly, 0.3, t0.Add(84*time.Hour)),
			}})
			h.observe(b, Observation{At: t0, Windows: []Window{
				usage(FiveHour, 0.5, t0.Add(150*time.Minute)),
				usage(Weekly, 0.5, t0.Add(84*time.Hour)),
			}})

			var got []AccountID
			for i := range 6 {
				got = append(got, h.route(t0, req(fmt.Sprintf("new-%d", i))).Account)
			}
			healthy := h.route(t0, req("old-2"))

			if !slices.Equal(got, tc.want) {
				t.Fatalf("placements = %v, want %v", got, tc.want)
			}
			if healthy.Kind != Dispatch || healthy.Account != b {
				t.Fatalf("existing conversation on b got %+v, want dispatch on %s", healthy, b)
			}
		})
	}
}

func TestPlacementReportsWhenResetPreferenceChangedTheChoice(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.observe(a, Observation{At: t0, Windows: []Window{usage(FiveHour, 0.1, t0.Add(30*time.Minute))}})

	first := h.route(t0, req("n1"))
	second := h.route(t0, req("n2"))

	if first.Account != a || first.Reason != ReasonCapacity || first.Observation != Fresh {
		t.Fatalf("first placement %+v, want %s by capacity with a fresh observation", first, a)
	}
	if second.Account != a || second.Reason != ReasonResetPreference {
		t.Fatalf("second placement %+v, want %s by reset preference", second, a)
	}
}

func TestNearFiveHourResetDoesNotMakeWeeklyExhaustedAccountEligible(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0, c, 1.0)...)
	h.route(t0, req("x-a"))
	h.route(t0, req("x-b"))
	h.route(t0, req("x-c"))
	h.observe(a, Observation{At: t0, Windows: []Window{
		usage(FiveHour, 0.1, t0.Add(20*time.Minute)),
		rejected(Weekly, t0.Add(48*time.Hour)),
	}})
	h.observe(c, Observation{At: t0, Windows: []Window{
		rejected(FiveHour, t0.Add(time.Hour)),
		usage(Weekly, 0.4, t0.Add(100*time.Hour)),
	}})

	fresh := h.route(t0, req("n1"))
	moved := h.route(t0, req("x-c"))

	if fresh.Kind != Place || fresh.Account != b {
		t.Fatalf("new conversation got %+v, want placement on %s", fresh, b)
	}
	if moved.Kind != Migrate || moved.From != c || moved.Account != b || moved.Reason != ReasonExhausted {
		t.Fatalf("exhausted conversation got %+v, want migration %s to %s", moved, c, b)
	}

	h.observe(b, Observation{At: t0, Windows: []Window{rejected(Weekly, t0.Add(72*time.Hour))}})
	h.observe(a, Observation{At: t0, Windows: []Window{
		rejected(FiveHour, t0.Add(20*time.Minute)),
		rejected(Weekly, t0.Add(48*time.Hour)),
	}})
	waiting := h.route(t0, req("x-c"))
	boundWhileWaiting := h.bound("x-c")
	resumed := h.route(t0.Add(time.Hour+time.Second), req("x-c"))

	if waiting.Kind != Wait || !waiting.Until.Equal(t0.Add(time.Hour)) || !waiting.ResetKnown {
		t.Fatalf("all blocked got %+v, want a wait until c's five-hour reset at %s", waiting, t0.Add(time.Hour))
	}
	if boundWhileWaiting != b {
		t.Fatalf("waiting moved x-c to %s, want it kept on %s", boundWhileWaiting, b)
	}
	if resumed.Kind != Migrate || resumed.Account != c {
		t.Fatalf("after c's reset got %+v, want migration to %s", resumed, c)
	}
}

func TestStaleOrMissingObservationsFallBackToCapacityWeights(t *testing.T) {
	nearReset := func(at time.Time) Observation {
		return Observation{At: at, Windows: []Window{usage(FiveHour, 0.1, t0.Add(30*time.Minute))}}
	}
	cases := []struct {
		name      string
		observe   func(r *Router)
		want      []AccountID
		freshness Freshness
	}{
		{"fresh", func(r *Router) { r.Observe(a, t0.Add(-5*time.Minute), nearReset(t0.Add(-5*time.Minute))) }, []AccountID{a, a, b, a}, Fresh},
		{"stale", func(r *Router) { r.Observe(a, t0.Add(-30*time.Minute), nearReset(t0.Add(-30*time.Minute))) }, []AccountID{a, b, a, b}, Stale},
		{"missing", func(r *Router) {}, []AccountID{a, b, a, b}, Absent},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
			tc.observe(h.r)

			var got []AccountID
			var first Decision
			for i := range 4 {
				d := h.route(t0, req(fmt.Sprintf("n%d", i)))
				if i == 0 {
					first = d
				}
				got = append(got, d.Account)
			}

			if !slices.Equal(got, tc.want) {
				t.Fatalf("placements = %v, want %v", got, tc.want)
			}
			if first.Observation != tc.freshness {
				t.Fatalf("first placement observation = %q, want %q", first.Observation, tc.freshness)
			}
		})
	}
}

func TestStaleRejectionStillBlocksUntilItsReset(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.observe(a, Observation{At: t0.Add(-3 * time.Hour), Windows: []Window{rejected(Weekly, t0.Add(24*time.Hour))}})

	before := h.route(t0, req("n1"))
	afterReset := h.route(t0.Add(24*time.Hour), req("n2"))

	if before.Account != b {
		t.Fatalf("with a's stale weekly rejection unexpired, n1 went to %s, want %s", before.Account, b)
	}
	if afterReset.Account != a {
		t.Fatalf("after a's weekly reset, n2 went to %s, want %s", afterReset.Account, a)
	}
}

func TestSubagentsInheritTheConversationAccountThroughMigration(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	parent := h.route(t0, req("session-1"))
	child := req("session-1")
	child.Agent = "agent-1"
	nested := req("session-1")
	nested.Agent, nested.ParentAgent = "agent-2", "agent-1"
	h.observe(b, Observation{At: t0, Windows: []Window{usage(FiveHour, 0, t0.Add(10*time.Minute))}})

	childDecision := h.route(t0, child)
	nestedDecision := h.route(t0, nested)

	if parent.Account != a {
		t.Fatalf("parent placed on %s, want %s", parent.Account, a)
	}
	if childDecision.Kind != Dispatch || childDecision.Account != a || nestedDecision.Kind != Dispatch || nestedDecision.Account != a {
		t.Fatalf("child %+v and nested %+v, want both dispatched on the parent's %s", childDecision, nestedDecision, a)
	}

	h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(2*time.Hour)))
	migrated := h.route(t0, req("session-1"))
	childAfter := h.route(t0.Add(time.Minute), child)
	nestedAfter := h.route(t0.Add(time.Minute), nested)

	if migrated.Kind != Migrate || migrated.Account != b {
		t.Fatalf("parent after exhaustion got %+v, want migration to %s", migrated, b)
	}
	if childAfter.Kind != Dispatch || childAfter.Account != b || nestedAfter.Kind != Dispatch || nestedAfter.Account != b {
		t.Fatalf("child %+v and nested %+v after migration, want dispatch on %s", childAfter, nestedAfter, b)
	}
}

func TestConcurrentFirstRequestsReceiveOneDurableAccount(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0, c, 1.0)...)
	const n = 16
	got := make([]Decision, n)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			r := req("shared")
			r.Agent = fmt.Sprintf("agent-%d", i)
			got[i] = h.route(t0, r)
		}()
	}
	close(start)
	wg.Wait()
	h.restart()

	for i, d := range got {
		if d.Account != a {
			t.Fatalf("request %d got %+v, want %s", i, d, a)
		}
	}
	if h.bound("shared") != a {
		t.Fatalf("after restart shared is bound to %q, want %s", h.bound("shared"), a)
	}
}

func TestConcurrentCommitsWithDifferentProposalsAgree(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0, c, 1.0)...)
	proposals := []AccountID{a, b, c, a, b, c, a, b}
	got := make([]AccountID, len(proposals))
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i, p := range proposals {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			acct, err := h.r.CommitAssignment(t0, "raced", p, ReasonCapacity)
			if err != nil {
				t.Error(err)
			}
			got[i] = acct
		}()
	}
	close(start)
	wg.Wait()
	h.restart()

	winner := h.bound("raced")
	if !slices.Contains([]AccountID{a, b, c}, winner) {
		t.Fatalf("recovered binding %q is not one of the proposals", winner)
	}
	for i, acct := range got {
		if acct != winner {
			t.Fatalf("commit %d returned %s, durable binding is %s", i, acct, winner)
		}
	}
}

func TestAssignmentSurvivesIdleModelChangeAndRestart(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	first := h.route(t0, req("conv"))
	later := t0.Add(3 * time.Hour)
	h.observe(b, Observation{At: later, Windows: []Window{usage(FiveHour, 0, later.Add(10*time.Minute))}})
	opus := req("conv")
	opus.Model = "claude-opus-4-5"

	afterIdle := h.route(later, opus)
	h.restart()
	afterRestart := h.route(later.Add(time.Minute), req("conv"))
	newcomer := h.route(later.Add(time.Minute), req("other"))

	if first.Account != a {
		t.Fatalf("first placement on %s, want %s", first.Account, a)
	}
	if afterIdle.Kind != Dispatch || afterIdle.Account != a {
		t.Fatalf("after 3h idle and a model change got %+v, want dispatch on %s", afterIdle, a)
	}
	if afterRestart.Kind != Dispatch || afterRestart.Account != a {
		t.Fatalf("after restart got %+v, want dispatch on %s", afterRestart, a)
	}
	if newcomer.Account != b {
		t.Fatalf("a new conversation went to %s, want %s, so conv's stay on %s was not a coincidence", newcomer.Account, b, a)
	}
}

func TestOnlyExhaustionMigratesAndTheConversationDoesNotReturn(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))

	var retries []Decision
	for attempt, class := range []Class{ClassTransient, ClassThrottle, ClassTransient} {
		h.report(t0, a, "claude-sonnet-4-5", class)
		r := req("conv")
		r.Attempt, r.LastFailure = attempt+2, class
		retries = append(retries, h.route(t0, r))
	}

	for i, d := range retries[:2] {
		if d.Kind != Retry || d.Account != a {
			t.Fatalf("retry %d got %+v, want retry on %s", i, d, a)
		}
	}
	if retries[2].Kind != Fail || retries[2].Account != a {
		t.Fatalf("fourth attempt got %+v, want fail on %s", retries[2], a)
	}
	if h.bound("conv") != a {
		t.Fatalf("after transient failures conv is bound to %s, want %s", h.bound("conv"), a)
	}

	h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(time.Hour)))
	r := req("conv")
	r.Attempt, r.LastFailure = 2, ClassExhausted
	moved := h.route(t0, r)
	later := t0.Add(2 * time.Hour)
	h.observe(a, Observation{At: later, Windows: []Window{usage(FiveHour, 0, later.Add(10*time.Minute))}})
	afterRecovery := h.route(later, req("conv"))
	h.restart()
	afterRestart := h.route(later, req("conv"))

	if moved.Kind != Migrate || moved.From != a || moved.Account != b || moved.Reason != ReasonExhausted {
		t.Fatalf("after exhaustion got %+v, want migration %s to %s", moved, a, b)
	}
	if afterRecovery.Kind != Dispatch || afterRecovery.Account != b {
		t.Fatalf("after a recovered got %+v, want dispatch on %s", afterRecovery, b)
	}
	if afterRestart.Kind != Dispatch || afterRestart.Account != b {
		t.Fatalf("after restart got %+v, want dispatch on %s", afterRestart, b)
	}
}

func TestManualOverrideMovesAndPersists(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))

	errUnknown := h.r.Move(t0, "conv", "acct-z")
	errUnassigned := h.r.Move(t0, "nobody", b)
	errMove := h.r.Move(t0, "conv", b)
	h.restart()
	after := h.route(t0.Add(time.Minute), req("conv"))
	bnd, _, _ := h.r.Lookup("conv")

	if errUnknown == nil || !strings.Contains(errUnknown.Error(), "not enrolled") {
		t.Fatalf("move to an unenrolled account: %v, want a not-enrolled error", errUnknown)
	}
	if !errors.Is(errUnassigned, ErrUnassigned) {
		t.Fatalf("move of an unassigned conversation: %v, want ErrUnassigned", errUnassigned)
	}
	if errMove != nil {
		t.Fatal(errMove)
	}
	if after.Kind != Dispatch || after.Account != b || bnd.Reason != ReasonManual {
		t.Fatalf("after manual move and restart got %+v with binding %+v, want dispatch on %s by manual", after, bnd, b)
	}
}

func TestAuthFailureExcludesNewPlacementButKeepsAssignments(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("existing"))
	h.route(t0, req("balance"))
	if err := h.r.Served(t0, "existing", a); err != nil {
		t.Fatal(err)
	}
	h.report(t0, a, "claude-sonnet-4-5", ClassAuth)

	newcomer := h.route(t0, req("newcomer"))
	existing := h.route(t0, req("existing"))

	if newcomer.Kind != Place || newcomer.Account != b {
		t.Fatalf("newcomer got %+v, want placement on %s", newcomer, b)
	}
	if existing.Kind != Reauth || existing.Account != a || h.bound("existing") != a {
		t.Fatalf("existing got %+v bound to %s, want reauth on %s with the binding kept", existing, h.bound("existing"), a)
	}

	h.report(t0, b, "claude-sonnet-4-5", ClassAuth)
	none := h.route(t0, req("stranded"))
	if err := h.r.Relogin(a); err != nil {
		t.Fatal(err)
	}
	resumed := h.route(t0, req("existing"))

	if none.Kind != Unavailable {
		t.Fatalf("with every account logged out got %+v, want unavailable", none)
	}
	if h.bound("stranded") != "" {
		t.Fatal("an unavailable decision created an assignment")
	}
	if resumed.Kind != Dispatch || resumed.Account != a {
		t.Fatalf("after relogin got %+v, want dispatch on %s", resumed, a)
	}
}

func TestExhaustionWithoutResetWaitsInexactlyThenRechecks(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0)...)
	h.route(t0, req("conv"))
	h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, Window{Kind: Unspecified, Rejected: true})

	waiting := h.route(t0.Add(time.Minute), req("conv"))
	recheck := h.route(t0.Add(5*time.Minute), req("conv"))

	if waiting.Kind != Wait || waiting.ResetKnown || !waiting.Until.Equal(t0.Add(5*time.Minute)) {
		t.Fatalf("unknown reset got %+v, want an inexact wait until %s", waiting, t0.Add(5*time.Minute))
	}
	if recheck.Kind != Dispatch || recheck.Account != a {
		t.Fatalf("at the recheck time got %+v, want dispatch on %s", recheck, a)
	}
}

func TestModelLimitBlocksOnlyThatModel(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	h.report(t0, a, "claude-opus-4-5", ClassModelLimit, Window{Kind: Unspecified, Models: []string{"claude-opus-4-5"}, Rejected: true})
	opus := req("conv")
	opus.Model = "claude-opus-4-5"

	sonnet := h.route(t0, req("conv"))
	moved := h.route(t0, opus)

	if sonnet.Kind != Dispatch || sonnet.Account != a {
		t.Fatalf("sonnet request got %+v, want dispatch on %s", sonnet, a)
	}
	if moved.Kind != Migrate || moved.Account != b {
		t.Fatalf("opus request got %+v, want migration to %s", moved, b)
	}
}

func TestMissingIdentityIsRejectedWithoutAssignment(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0)...)

	rejectedReq := h.route(t0, req(""))
	accepted := h.route(t0, req("conv"))

	if rejectedReq.Kind != Reject || rejectedReq.Reason != ReasonMissingIdentity {
		t.Fatalf("request without identity got %+v, want reject", rejectedReq)
	}
	bindings, _ := h.store.Bindings()
	if len(bindings) != 1 || accepted.Account != a {
		t.Fatalf("bindings %v and accepted %+v, want only conv on %s", bindings, accepted, a)
	}
}

func TestCrashBeforeCommitLeavesNoAssignmentAndAfterCommitKeepsIt(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("other"))
	decided, err := h.r.Decide(t0, req("before"))
	if err != nil {
		t.Fatal(err)
	}
	h.restart()
	survivedUncommitted := h.bound("before") != ""
	replaced := h.route(t0, req("before"))

	if decided.Kind != Place || decided.Account != b {
		t.Fatalf("decision before the crash %+v, want placement on %s", decided, b)
	}
	if survivedUncommitted {
		t.Fatal("an uncommitted decision survived the restart")
	}
	if replaced.Kind != Place || replaced.Account != b {
		t.Fatalf("after restart got %+v, want one placement on %s", replaced, b)
	}

	h.report(t0, b, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(time.Hour)))
	if _, err := h.r.Route(t0, req("before")); err != nil {
		t.Fatal(err)
	}
	h.restart()

	if got := h.bound("before"); got != a {
		t.Fatalf("after a committed migration and restart, bound to %s, want %s", got, a)
	}
	if got := h.bound("other"); got != a {
		t.Fatalf("other is bound to %s after restart, want %s", got, a)
	}
}

func TestTornMigrationRecordIsDiscardedOnRecovery(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	if err := h.store.Close(); err != nil {
		t.Fatal(err)
	}
	h.store = nil
	f, err := os.OpenFile(filepath.Join(h.dir, journalName), os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	f.WriteString(`{"op":"migrate","c":"conv","a":"acct-b","from":"acct-a","wh`)
	f.Close()

	h.open()
	before := h.bound("conv")
	h.route(t0, req("next"))
	h.restart()

	if before != a {
		t.Fatalf("after a torn migration record conv is bound to %s, want %s", before, a)
	}
	if h.bound("next") != b || h.bound("conv") != a {
		t.Fatalf("after appending past the truncated tail: conv=%s next=%s, want %s and %s", h.bound("conv"), h.bound("next"), a, b)
	}
}

func TestCorruptInteriorRecordRefusesToOpen(t *testing.T) {
	dir := t.TempDir()
	content := `{"op":"assign","c":"c1","a":"acct-a","why":"capacity","t":"2026-10-05T09:00:00Z"}` + "\nnot json\n" +
		`{"op":"assign","c":"c2","a":"acct-b","why":"capacity","t":"2026-10-05T09:00:00Z"}` + "\n"
	if err := os.WriteFile(filepath.Join(dir, journalName), []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	_, err := OpenStore(dir)

	if err == nil || !strings.Contains(err.Error(), "corrupt journal record at byte 82") {
		t.Fatalf("open = %v, want a corrupt-record error at byte 82", err)
	}
}

func TestSecondOpenerIsRefused(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0)...)
	h.route(t0, req("conv"))

	_, err := OpenStore(h.dir)
	h.restart()

	if !errors.Is(err, ErrLocked) {
		t.Fatalf("second open = %v, want ErrLocked", err)
	}
	if h.bound("conv") != a {
		t.Fatalf("after the refused open conv is bound to %q, want %s", h.bound("conv"), a)
	}
}

func TestExhaustionNeedsEvidenceFromTheFailedAttempt(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	attempt := t0.Add(10 * time.Minute)
	staleSnapshot := Observation{At: t0, Windows: []Window{rejected(FiveHour, t0.Add(time.Hour))}}

	class := h.r.Report(Failure{Account: a, Model: "claude-sonnet-4-5", Class: ClassExhausted, AttemptStart: attempt, Observation: staleSnapshot})
	r := req("conv")
	r.Attempt, r.LastFailure = 2, class
	retried := h.route(attempt, r)

	if class != ClassThrottle {
		t.Fatalf("429 with a snapshot older than the attempt classified as %q, want %q", class, ClassThrottle)
	}
	if retried.Kind != Retry || retried.Account != a {
		t.Fatalf("after an unconfirmed 429 got %+v, want retry on %s", retried, a)
	}

	class = h.report(attempt, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, attempt.Add(time.Hour)))
	r.Attempt, r.LastFailure = 3, class
	moved := h.route(attempt, r)

	if class != ClassExhausted || moved.Kind != Migrate || moved.Account != b {
		t.Fatalf("with this attempt's rejected window: class %q, decision %+v; want exhausted and migration to %s", class, moved, b)
	}
}

func TestUnverifiedOverageWithholdsDispatch(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.manualOverage = true

	unknown := h.route(t0, req("first"))
	refusedBound := h.bound("first")
	h.r.ObserveOverage(b, OverageDisabled, t0)
	placed := h.route(t0, req("first"))

	if unknown.Kind != Refuse || unknown.Reason != ReasonNoVerified || !unknown.RecheckOverage {
		t.Fatalf("with no account verified got %+v, want refuse with a recheck", unknown)
	}
	if refusedBound != "" {
		t.Fatalf("a refusal bound first to %s", refusedBound)
	}
	if placed.Kind != Place || placed.Account != b {
		t.Fatalf("with only b verified got %+v, want placement on %s", placed, b)
	}

	stale := h.route(t0.Add(61*time.Minute), req("first"))
	h.r.ObserveOverage(b, OverageDisabled, t0.Add(62*time.Minute))
	fresh := h.route(t0.Add(62*time.Minute), req("first"))

	if stale.Kind != Refuse || stale.Account != b || stale.Reason != ReasonOverageStale || !stale.RecheckOverage || h.bound("first") != b {
		t.Fatalf("with b's check 61 minutes old got %+v bound to %s, want refuse on %s with a recheck and the binding kept", stale, h.bound("first"), b)
	}
	if fresh.Kind != Dispatch || fresh.Account != b || fresh.RecheckOverage {
		t.Fatalf("after a fresh check got %+v, want dispatch on %s", fresh, b)
	}
}

func TestStaleOverageCheckRefusesWithoutMigrating(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.r.ObserveOverage(b, OverageDisabled, t0)
	h.route(t0, req("on-a"))
	later := t0.Add(61 * time.Minute)
	h.r.ObserveOverage(b, OverageDisabled, later)

	refused := h.route(later, req("on-a"))
	newcomer := h.route(later, req("newcomer"))

	if refused.Kind != Refuse || refused.Reason != ReasonOverageStale || !refused.RecheckOverage || h.bound("on-a") != a {
		t.Fatalf("with a's check stale and b verified got %+v bound to %s, want refuse with a recheck and the binding kept on %s", refused, h.bound("on-a"), a)
	}
	if newcomer.Kind != Place || newcomer.Account != b {
		t.Fatalf("newcomer got %+v, want placement on %s", newcomer, b)
	}
}

func TestObservedPaidUseMigratesAndDoesNotReturn(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.r.ObserveOverage(b, OverageDisabled, t0)
	h.route(t0, req("on-a"))
	h.r.ObserveOverage(a, OveragePaidUse, t0.Add(time.Minute))

	moved := h.route(t0.Add(2*time.Minute), req("on-a"))
	newcomer := h.route(t0.Add(2*time.Minute), req("newcomer"))
	h.r.ObserveOverage(a, OverageDisabled, t0.Add(3*time.Minute))
	h.r.ObserveOverage(b, OverageDisabled, t0.Add(3*time.Minute))
	afterClear := h.route(t0.Add(3*time.Minute), req("on-a"))

	if moved.Kind != Migrate || moved.From != a || moved.Account != b || moved.Reason != ReasonOverageObserved {
		t.Fatalf("after paid use on a got %+v, want migration %s to %s for overage_observed", moved, a, b)
	}
	if newcomer.Kind != Place || newcomer.Account != b {
		t.Fatalf("newcomer got %+v, want placement on %s", newcomer, b)
	}
	if afterClear.Kind != Dispatch || afterClear.Account != b {
		t.Fatalf("after a's overflow is disabled again got %+v, want dispatch on %s", afterClear, b)
	}
}

func TestObservedPaidUseWithoutADestinationRefusesAndKeepsTheBinding(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.route(t0, req("on-a"))
	h.r.ObserveOverage(a, OveragePaidUse, t0.Add(time.Minute))

	refused := h.route(t0.Add(2*time.Minute), req("on-a"))
	h.r.ObserveOverage(a, OverageDisabled, t0.Add(3*time.Minute))
	resumed := h.route(t0.Add(3*time.Minute), req("on-a"))

	if refused.Kind != Refuse || refused.Account != a || refused.Reason != ReasonPaidUse || h.bound("on-a") != a {
		t.Fatalf("with no other account got %+v bound to %s, want refuse on %s for paid use", refused, h.bound("on-a"), a)
	}
	if resumed.Kind != Dispatch || resumed.Account != a {
		t.Fatalf("after a later disabled check got %+v, want dispatch on %s", resumed, a)
	}
}

func TestNeverServedConversationIsPlacedAgainWhenItsAccountNeedsLogin(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("unserved"))
	h.route(t0, req("other"))
	h.route(t0, req("served"))
	if err := h.r.Served(t0, "served", a); err != nil {
		t.Fatal(err)
	}
	h.restart()
	h.report(t0.Add(time.Minute), a, "claude-sonnet-4-5", ClassAuth)

	replaced := h.route(t0.Add(time.Minute), req("unserved"))
	kept := h.route(t0.Add(time.Minute), req("served"))

	if replaced.Kind != Migrate || replaced.From != a || replaced.Account != b || replaced.Reason != ReasonNeverServed {
		t.Fatalf("never-served conversation got %+v, want migration %s to %s for never_served_relogin", replaced, a, b)
	}
	if kept.Kind != Reauth || kept.Account != a || h.bound("served") != a {
		t.Fatalf("served conversation got %+v bound to %s, want reauth with the binding kept on %s across the restart", kept, h.bound("served"), a)
	}

	h.report(t0.Add(2*time.Minute), b, "claude-sonnet-4-5", ClassAuth)
	stranded := h.route(t0.Add(2*time.Minute), req("unserved"))

	if stranded.Kind != Reauth || stranded.Account != b || h.bound("unserved") != b {
		t.Fatalf("with every account logged out got %+v bound to %s, want reauth on %s", stranded, h.bound("unserved"), b)
	}
}

func TestServedMarkBelongsToOneBinding(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	if err := h.r.Served(t0, "conv", a); err != nil {
		t.Fatal(err)
	}
	if err := h.r.Move(t0, "conv", b); err != nil {
		t.Fatal(err)
	}
	staleMark := h.r.Served(t0, "conv", a)
	h.restart()
	h.report(t0.Add(time.Minute), b, "claude-sonnet-4-5", ClassAuth)

	moved := h.route(t0.Add(time.Minute), req("conv"))

	if staleMark != nil {
		t.Fatal(staleMark)
	}
	if moved.Kind != Migrate || moved.From != b || moved.Account != a || moved.Reason != ReasonNeverServed {
		t.Fatalf("a binding that never served on b got %+v, want migration %s to %s for never_served_relogin", moved, b, a)
	}
}

func TestRequestScopedFailureIsReportedNotRetried(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	r := req("conv")
	r.Attempt, r.LastFailure = 2, ClassRequestScoped

	d := h.route(t0, r)

	if d.Kind != Fail || d.Account != a || d.Reason != ReasonRequestScoped || h.bound("conv") != a {
		t.Fatalf("request-scoped failure got %+v bound to %s, want fail on %s without moving", d, h.bound("conv"), a)
	}
}

type faultyFile struct {
	journalFile
	failSync   bool
	shortWrite bool
}

func (f *faultyFile) Write(p []byte) (int, error) {
	if f.shortWrite {
		n, _ := f.journalFile.Write(p[:len(p)/2])
		return n, errors.New("injected short write")
	}
	return f.journalFile.Write(p)
}

func (f *faultyFile) Sync() error {
	if f.failSync {
		return errors.New("injected sync failure")
	}
	return f.journalFile.Sync()
}

func TestFailedSyncStopsAcknowledgmentsUntilReopen(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Assign("c1", a, ReasonCapacity, t0); err != nil {
		t.Fatal(err)
	}
	faulty := &faultyFile{journalFile: s.file, failSync: true}
	s.file = faulty

	_, errFirst := s.Assign("c2", a, ReasonCapacity, t0)
	faulty.failSync = false
	_, errSecond := s.Assign("c2", b, ReasonCapacity, t0)
	s.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	recovered, _, _ := s.Lookup("c2")
	afterReopen, errAfter := s.Assign("c3", b, ReasonCapacity, t0)

	if errFirst == nil || !errors.Is(errSecond, ErrFailed) {
		t.Fatalf("failed sync returned %v, next assign returned %v; want an error, then ErrFailed", errFirst, errSecond)
	}
	if recovered.Account == b {
		t.Fatal("replay returned the account that was never acknowledged")
	}
	if errAfter != nil || afterReopen.Account != b {
		t.Fatalf("after reopen assign returned %+v, %v; want %s", afterReopen, errAfter, b)
	}
}

func TestShortWriteLeavesATornTailNotInteriorCorruption(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Assign("c1", a, ReasonCapacity, t0); err != nil {
		t.Fatal(err)
	}
	faulty := &faultyFile{journalFile: s.file, shortWrite: true}
	s.file = faulty

	_, errShort := s.Assign("c2", a, ReasonCapacity, t0)
	faulty.shortWrite = false
	_, errNext := s.Assign("c3", b, ReasonCapacity, t0)
	s.Close()
	s, errOpen := OpenStore(dir)
	if errOpen != nil {
		t.Fatalf("reopen after a short write: %v", errOpen)
	}
	defer s.Close()
	_, c2, _ := s.Lookup("c2")
	c1, _, _ := s.Lookup("c1")
	c3, errC3 := s.Assign("c3", b, ReasonCapacity, t0)

	if errShort == nil || !errors.Is(errNext, ErrFailed) {
		t.Fatalf("short write returned %v, next assign %v; want an error, then ErrFailed", errShort, errNext)
	}
	if c2 || c1.Account != a {
		t.Fatalf("after recovery c2 present=%v and c1=%s, want c2 absent and c1 on %s", c2, c1.Account, a)
	}
	if errC3 != nil || c3.Account != b {
		t.Fatalf("after recovery assign c3 = %+v, %v; want %s", c3, errC3, b)
	}
}

func TestIncompleteRecordsAreRefusedOnReplay(t *testing.T) {
	for name, line := range map[string]string{
		"empty":                    `{}`,
		"migration without source": `{"op":"migrate","c":"c1","a":"acct-b","why":"exhausted","t":"2026-10-05T09:00:00Z"}`,
	} {
		t.Run(name, func(t *testing.T) {
			dir := t.TempDir()
			valid := `{"op":"assign","c":"c1","a":"acct-a","why":"capacity","t":"2026-10-05T09:00:00Z"}`
			if err := os.WriteFile(filepath.Join(dir, journalName), []byte(valid+"\n"+line+"\n"), 0o600); err != nil {
				t.Fatal(err)
			}

			_, err := OpenStore(dir)

			if err == nil || !strings.Contains(err.Error(), "journal record at byte 82") {
				t.Fatalf("open = %v, want a refusal of the record at byte 82", err)
			}
		})
	}
}

func TestOpenCreatesMissingStateDirectories(t *testing.T) {
	dir := filepath.Join(t.TempDir(), "state", "router")

	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	_, errAssign := s.Assign("c1", a, ReasonCapacity, t0)
	s.Close()
	s, err = OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	got, _, _ := s.Lookup("c1")

	if errAssign != nil || got.Account != a {
		t.Fatalf("after reopening a created directory c1 = %+v (assign error %v), want %s", got, errAssign, a)
	}
}

func TestEmptyFieldsAreRefusedBeforeAnyWrite(t *testing.T) {
	dir := t.TempDir()
	s, err := OpenStore(dir)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := s.Assign("c1", a, ReasonCapacity, t0); err != nil {
		t.Fatal(err)
	}
	before, _ := os.ReadFile(filepath.Join(dir, journalName))

	_, errConv := s.Assign("", a, ReasonCapacity, t0)
	_, errAccount := s.Assign("c2", "", ReasonCapacity, t0)
	_, errMigrate := s.Migrate("c1", a, "", ReasonManual, t0)
	after, _ := os.ReadFile(filepath.Join(dir, journalName))
	next, errNext := s.Assign("c3", b, ReasonCapacity, t0)
	s.Close()
	s, errOpen := OpenStore(dir)
	if errOpen != nil {
		t.Fatalf("reopen after refused records: %v", errOpen)
	}
	defer s.Close()
	c3, _, _ := s.Lookup("c3")
	_, c2, _ := s.Lookup("c2")

	for name, err := range map[string]error{"empty conversation": errConv, "empty account": errAccount, "migration to an empty account": errMigrate} {
		if !errors.Is(err, ErrIncomplete) {
			t.Errorf("%s: %v, want ErrIncomplete", name, err)
		}
	}
	if !bytes.Equal(before, after) {
		t.Fatalf("refused records changed the journal from %q to %q", before, after)
	}
	if errNext != nil || next.Account != b || c3.Account != b || c2 {
		t.Fatalf("after refusals: assign c3 = %+v, %v; recovered c3 = %s, c2 present = %v; want c3 on %s and no c2", next, errNext, c3.Account, c2, b)
	}
}

func TestFailedMigrationSyncStopsEveryAnswerUntilReopen(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	h.route(t0, req("other"))
	if err := h.r.Served(t0, "conv", a); err != nil {
		t.Fatal(err)
	}
	h.report(t0.Add(time.Minute), a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(30*time.Minute)))
	faulty := &faultyFile{journalFile: h.store.file, failSync: true}
	h.store.file = faulty
	for _, acct := range h.accounts {
		h.r.ObserveOverage(acct.ID, OverageDisabled, t0.Add(2*time.Minute))
	}

	_, errMigrate := h.r.Route(t0.Add(2*time.Minute), req("conv"))
	faulty.failSync = false
	later := t0.Add(31 * time.Minute)
	for _, acct := range h.accounts {
		h.r.ObserveOverage(acct.ID, OverageDisabled, later)
	}
	afterReset, errRoute := h.r.Route(later, req("conv"))
	_, errDecide := h.r.Decide(later, req("conv"))
	_, _, errLookup := h.r.Lookup("conv")
	_, errExisting := h.r.CommitAssignment(later, "other", b, ReasonManual)
	errServed := h.r.Served(later, "other", b)
	errMove := h.r.Move(later, "other", b)
	errRelogin := h.r.Relogin(a)
	h.restart()
	recovered := h.bound("conv")
	resumed := h.route(later, req("conv"))

	if errMigrate == nil {
		t.Fatal("the migration commit with a failed sync returned no error")
	}
	for name, err := range map[string]error{
		"Route": errRoute, "Decide": errDecide, "Lookup": errLookup, "CommitAssignment on a bound conversation": errExisting,
		"Served": errServed, "Move to the current account": errMove, "Relogin": errRelogin,
	} {
		if !errors.Is(err, ErrFailed) {
			t.Errorf("%s after the failed sync: %v, want ErrFailed", name, err)
		}
	}
	if afterReset.Account != "" {
		t.Fatalf("after the failed sync Route answered %+v, want no account", afterReset)
	}
	if resumed.Kind != Dispatch || resumed.Account != recovered {
		t.Fatalf("after reopen the conversation is bound to %s and Route answered %+v, want dispatch on the recovered account", recovered, resumed)
	}
}

func TestNewerObservationWithoutARejectedWindowKeepsItBlocking(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.observe(a, Observation{At: t0, Windows: []Window{
		{Kind: Weekly, Models: []string{"claude-opus"}, Utilization: ptr(1), Rejected: true, ResetsAt: t0.Add(48 * time.Hour)},
	}})
	opus := func(conv string) Request {
		return Request{Conversation: ConversationID(conv), Model: "claude-opus-4-5", Attempt: 1}
	}

	first := h.route(t0.Add(time.Minute), opus("opus-1"))
	h.observe(a, Observation{At: t0.Add(2 * time.Minute), Windows: []Window{usage(FiveHour, 0.2, t0.Add(3*time.Hour))}})
	second := h.route(t0.Add(3*time.Minute), opus("opus-2"))
	sonnet := h.route(t0.Add(3*time.Minute), req("sonnet-1"))
	afterReset := h.route(t0.Add(48*time.Hour), opus("opus-3"))

	if first.Account != b || second.Account != b {
		t.Fatalf("opus placements %s then %s, want both on %s while a's opus weekly window is rejected", first.Account, second.Account, b)
	}
	if sonnet.Account != a {
		t.Fatalf("sonnet placement went to %s, want %s, which the opus window does not block", sonnet.Account, a)
	}
	if afterReset.Account != a {
		t.Fatalf("after the opus weekly reset the placement went to %s, want %s", afterReset.Account, a)
	}
}

func TestNewerObservationReportingTheWindowAllowedClearsTheRejection(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.observe(a, Observation{At: t0, Windows: []Window{rejected(FiveHour, t0.Add(2*time.Hour))}})

	blocked := h.route(t0, req("n1"))
	h.observe(a, Observation{At: t0.Add(time.Minute), Windows: []Window{usage(FiveHour, 0.1, t0.Add(5*time.Hour))}})
	cleared := h.route(t0.Add(time.Minute), req("n2"))

	if blocked.Account != b || cleared.Account != a {
		t.Fatalf("placements %s then %s, want %s while rejected and %s after a newer report of the same window", blocked.Account, cleared.Account, b, a)
	}
}

func TestObservationFromBeforeTheAttemptIsIgnored(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	snapshot := Observation{At: t0, Windows: []Window{rejected(FiveHour, t0.Add(2*time.Hour))}}

	h.r.Observe(a, t0.Add(time.Minute), snapshot)
	stale := h.route(t0.Add(time.Minute), req("n1"))
	h.r.Observe(a, t0, snapshot)
	fresh := h.route(t0.Add(time.Minute), req("n2"))

	if stale.Account != a {
		t.Fatalf("with only pre-attempt evidence of a rejection, n1 went to %s, want %s", stale.Account, a)
	}
	if fresh.Account != b {
		t.Fatalf("with the attempt's own rejection, n2 went to %s, want %s", fresh.Account, b)
	}
}

func TestPaidUseClearsOnlyOnAStrictlyLaterDisabledCheck(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.r.ObserveOverage(b, OverageDisabled, t0)
	h.route(t0, req("conv"))
	at := t0.Add(time.Minute)

	h.r.ObserveOverage(a, OveragePaidUse, at)
	h.r.ObserveOverage(a, OverageDisabled, at)
	sameInstant := h.route(at, req("conv"))
	h.r.ObserveOverage(a, OverageDisabled, at.Add(time.Second))
	h.r.ObserveOverage(b, OverageDisabled, at.Add(time.Second))
	later := h.route(at.Add(time.Second), req("newcomer"))

	if sameInstant.Kind != Migrate || sameInstant.From != a || sameInstant.Account != b || sameInstant.Reason != ReasonOverageObserved {
		t.Fatalf("after paid use and a disabled check at the same instant got %+v, want migration %s to %s for overage_observed", sameInstant, a, b)
	}
	if later.Kind != Place || later.Account != a {
		t.Fatalf("after a strictly later disabled check got %+v, want placement on %s", later, a)
	}
}

func TestConfirmedExhaustionMigratesDespiteAStaleSourceCheck(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.r.ObserveOverage(b, OverageDisabled, t0)
	h.route(t0, req("conv"))
	later := t0.Add(61 * time.Minute)
	h.report(later, a, "claude-sonnet-4-5", ClassExhausted, rejected(Weekly, later.Add(24*time.Hour)))

	unverified := h.route(later, req("conv"))
	boundWhileUnverified := h.bound("conv")
	h.r.ObserveOverage(b, OverageDisabled, later)
	moved := h.route(later, req("conv"))

	if unverified.Kind != Refuse || unverified.Reason != ReasonNoVerified || !unverified.RecheckOverage || boundWhileUnverified != a {
		t.Fatalf("with both checks stale got %+v bound to %s, want refuse with a recheck and no move from %s", unverified, boundWhileUnverified, a)
	}
	if moved.Kind != Migrate || moved.From != a || moved.Account != b || moved.Reason != ReasonExhausted {
		t.Fatalf("with a's check stale and b verified got %+v, want migration %s to %s for exhausted", moved, a, b)
	}
}

func TestWaitRequestsARecheckWhenAnUncheckedAccountCouldServe(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.manualOverage = true
	h.r.ObserveOverage(a, OverageDisabled, t0)
	h.observe(a, Observation{At: t0, Windows: []Window{rejected(FiveHour, t0.Add(2*time.Hour))}})

	unchecked := h.route(t0.Add(time.Minute), req("n1"))
	h.observe(b, Observation{At: t0, Windows: []Window{rejected(Weekly, t0.Add(48*time.Hour))}})
	blockedAnyway := h.route(t0.Add(time.Minute), req("n2"))

	if unchecked.Kind != Wait || !unchecked.Until.Equal(t0.Add(2*time.Hour)) || !unchecked.RecheckOverage {
		t.Fatalf("with b never checked got %+v, want a wait until a's reset with a recheck", unchecked)
	}
	if blockedAnyway.Kind != Wait || blockedAnyway.RecheckOverage {
		t.Fatalf("with b also rejected got %+v, want a wait without a recheck", blockedAnyway)
	}
}

func TestRequestScopedFailureEndsTheRequestBeforeAnyMigration(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("conv"))
	h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(time.Hour)))
	r := req("conv")
	r.Attempt, r.LastFailure = 2, ClassRequestScoped

	ended := h.route(t0, r)
	next := h.route(t0, req("conv"))

	if ended.Kind != Fail || ended.Account != a || ended.Reason != ReasonRequestScoped {
		t.Fatalf("request-scoped failure on an exhausted account got %+v, want fail on %s", ended, a)
	}
	if next.Kind != Migrate || next.Account != b {
		t.Fatalf("the next request got %+v, want migration to %s", next, b)
	}
}

func TestCommitHonorsServedAndReloginThatLandAfterTheDecision(t *testing.T) {
	setup := func(t *testing.T) (*harness, Decision) {
		h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
		h.route(t0, req("conv"))
		h.report(t0, a, "claude-sonnet-4-5", ClassAuth)
		d, err := h.r.Decide(t0, req("conv"))
		if err != nil {
			t.Fatal(err)
		}
		if d.Kind != Migrate || d.Reason != ReasonNeverServed {
			t.Fatalf("setup decision %+v, want a never_served_relogin migration", d)
		}
		return h, d
	}
	t.Run("served", func(t *testing.T) {
		h, d := setup(t)
		if err := h.r.Served(t0, "conv", a); err != nil {
			t.Fatal(err)
		}

		got, err := h.r.Commit(t0, req("conv"), d)

		if err != nil || got.Kind != Reauth || got.Account != a || h.bound("conv") != a {
			t.Fatalf("commit after Served got %+v, %v, bound to %s; want reauth with the binding kept on %s", got, err, h.bound("conv"), a)
		}
	})
	t.Run("relogin", func(t *testing.T) {
		h, d := setup(t)
		if err := h.r.Relogin(a); err != nil {
			t.Fatal(err)
		}

		got, err := h.r.Commit(t0, req("conv"), d)

		if err != nil || got.Kind != Dispatch || got.Account != a || h.bound("conv") != a {
			t.Fatalf("commit after Relogin got %+v, %v, bound to %s; want dispatch with the binding kept on %s", got, err, h.bound("conv"), a)
		}
	})
}

func TestUnconfirmedExhaustionOnAnUnenrolledAccountIsThrottling(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0)...)

	unknown := h.report(t0, "acct-z", "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(time.Hour)))
	known := h.report(t0, a, "claude-sonnet-4-5", ClassExhausted, rejected(FiveHour, t0.Add(time.Hour)))

	if unknown != ClassThrottle || known != ClassExhausted {
		t.Fatalf("classes %q for an unenrolled account and %q for an enrolled one, want throttle and exhausted", unknown, known)
	}
}

func TestNewRejectsInvalidCapacityAndConfig(t *testing.T) {
	st, err := OpenStore(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	defer st.Close()
	badBias := DefaultConfig()
	badBias.ResetBias = math.NaN()
	noAttempts := DefaultConfig()
	noAttempts.MaxAttempts = 0
	cases := map[string]struct {
		cfg      Config
		accounts []Account
	}{
		"NaN capacity":      {DefaultConfig(), []Account{{ID: a, Capacity: math.NaN()}}},
		"infinite capacity": {DefaultConfig(), []Account{{ID: a, Capacity: math.Inf(1)}}},
		"NaN reset bias":    {badBias, []Account{{ID: a, Capacity: 1}}},
		"zero max attempts": {noAttempts, []Account{{ID: a, Capacity: 1}}},
		"unknown rule":      {Config{Rule: "fastest"}, []Account{{ID: a, Capacity: 1}}},
	}

	_, errValid := New(DefaultConfig(), []Account{{ID: a, Capacity: 1}}, st)

	if errValid != nil {
		t.Fatalf("a valid configuration was refused: %v", errValid)
	}
	for name, tc := range cases {
		if _, err := New(tc.cfg, tc.accounts, st); err == nil {
			t.Errorf("%s: New accepted it", name)
		}
	}
}
