package router

import (
	"errors"
	"fmt"
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
		MaxAttempts:         3,
	}
}

type harness struct {
	t        *testing.T
	dir      string
	cfg      Config
	accounts []Account
	store    *Store
	r        *Router
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
	d, err := h.r.Route(now, req)
	if err != nil {
		h.t.Fatalf("route %s: %v", req.Conversation, err)
	}
	return d
}

func (h *harness) bound(conv ConversationID) AccountID {
	h.t.Helper()
	bnd, ok := h.r.Lookup(conv)
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
			h.r.Observe(a, Observation{At: t0, Windows: []Window{
				usage(FiveHour, 0.1, t0.Add(30*time.Minute)),
				usage(Weekly, 0.3, t0.Add(84*time.Hour)),
			}})
			h.r.Observe(b, Observation{At: t0, Windows: []Window{
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
	h.r.Observe(a, Observation{At: t0, Windows: []Window{usage(FiveHour, 0.1, t0.Add(30*time.Minute))}})

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
	h.r.Observe(a, Observation{At: t0, Windows: []Window{
		usage(FiveHour, 0.1, t0.Add(20*time.Minute)),
		rejected(Weekly, t0.Add(48*time.Hour)),
	}})
	h.r.Observe(c, Observation{At: t0, Windows: []Window{
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

	h.r.Observe(b, Observation{At: t0, Windows: []Window{rejected(Weekly, t0.Add(72*time.Hour))}})
	h.r.Observe(a, Observation{At: t0, Windows: []Window{
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
		{"fresh", func(r *Router) { r.Observe(a, nearReset(t0.Add(-5*time.Minute))) }, []AccountID{a, a, b, a}, Fresh},
		{"stale", func(r *Router) { r.Observe(a, nearReset(t0.Add(-30*time.Minute))) }, []AccountID{a, b, a, b}, Stale},
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
	h.r.Observe(a, Observation{At: t0.Add(-3 * time.Hour), Windows: []Window{rejected(Weekly, t0.Add(24*time.Hour))}})

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
	h.r.Observe(b, Observation{At: t0, Windows: []Window{usage(FiveHour, 0, t0.Add(10*time.Minute))}})

	childDecision := h.route(t0, child)
	nestedDecision := h.route(t0, nested)

	if parent.Account != a {
		t.Fatalf("parent placed on %s, want %s", parent.Account, a)
	}
	if childDecision.Kind != Dispatch || childDecision.Account != a || nestedDecision.Kind != Dispatch || nestedDecision.Account != a {
		t.Fatalf("child %+v and nested %+v, want both dispatched on the parent's %s", childDecision, nestedDecision, a)
	}

	h.r.Report(t0, a, "claude-sonnet-4-5", ClassExhausted, Observation{Windows: []Window{rejected(FiveHour, t0.Add(2*time.Hour))}})
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
	h.r.Observe(b, Observation{At: later, Windows: []Window{usage(FiveHour, 0, later.Add(10*time.Minute))}})
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
		h.r.Report(t0, a, "claude-sonnet-4-5", class, Observation{})
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

	h.r.Report(t0, a, "claude-sonnet-4-5", ClassExhausted, Observation{Windows: []Window{rejected(FiveHour, t0.Add(time.Hour))}})
	r := req("conv")
	r.Attempt, r.LastFailure = 2, ClassExhausted
	moved := h.route(t0, r)
	later := t0.Add(2 * time.Hour)
	h.r.Observe(a, Observation{At: later, Windows: []Window{usage(FiveHour, 0, later.Add(10*time.Minute))}})
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
	bnd, _ := h.r.Lookup("conv")

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
	h.r.Report(t0, a, "claude-sonnet-4-5", ClassAuth, Observation{})

	newcomer := h.route(t0, req("newcomer"))
	existing := h.route(t0, req("existing"))

	if newcomer.Kind != Place || newcomer.Account != b {
		t.Fatalf("newcomer got %+v, want placement on %s", newcomer, b)
	}
	if existing.Kind != Reauth || existing.Account != a || h.bound("existing") != a {
		t.Fatalf("existing got %+v bound to %s, want reauth on %s with the binding kept", existing, h.bound("existing"), a)
	}

	h.r.Report(t0, b, "claude-sonnet-4-5", ClassAuth, Observation{})
	none := h.route(t0, req("stranded"))
	h.r.Relogin(a)
	resumed := h.route(t0, req("existing"))

	if none.Kind != Unavailable {
		t.Fatalf("with every account logged out got %+v, want unavailable", none)
	}
	if _, ok := h.r.Lookup("stranded"); ok {
		t.Fatal("an unavailable decision created an assignment")
	}
	if resumed.Kind != Dispatch || resumed.Account != a {
		t.Fatalf("after relogin got %+v, want dispatch on %s", resumed, a)
	}
}

func TestExhaustionWithoutResetWaitsInexactlyThenRechecks(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0)...)
	h.route(t0, req("conv"))
	h.r.Report(t0, a, "claude-sonnet-4-5", ClassExhausted, Observation{})

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
	h.r.Report(t0, a, "claude-opus-4-5", ClassModelLimit, Observation{})
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
	if len(h.store.Bindings()) != 1 || accepted.Account != a {
		t.Fatalf("bindings %v and accepted %+v, want only conv on %s", h.store.Bindings(), accepted, a)
	}
}

func TestCrashBeforeCommitLeavesNoAssignmentAndAfterCommitKeepsIt(t *testing.T) {
	h := newHarness(t, cfgWith(ResetAware), accts(a, 1.0, b, 1.0)...)
	h.route(t0, req("other"))
	decided := h.r.Decide(t0, req("before"))
	h.restart()
	_, survivedUncommitted := h.r.Lookup("before")
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

	h.r.Report(t0, b, "claude-sonnet-4-5", ClassExhausted, Observation{Windows: []Window{rejected(FiveHour, t0.Add(time.Hour))}})
	if _, err := h.r.CommitMigration(t0, "before", b, a, ReasonExhausted); err != nil {
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
