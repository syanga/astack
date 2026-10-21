package router

import (
	"fmt"
	"math"
	"slices"
	"testing"
	"time"
)

func TestWeeklyResetCreditsChangePlacementWithoutMovingHealthyBindings(t *testing.T) {
	for _, tc := range []struct {
		rule Rule
		want []AccountID
	}{
		{ResetAware, []AccountID{b, b, b, b, b, b}},
		{CapacityOnly, []AccountID{a, b, a, b, a, b}},
	} {
		t.Run(string(tc.rule), func(t *testing.T) {
			cfg := DefaultConfig()
			cfg.Rule = tc.rule
			h := newHarness(t, cfg, accts(a, 1.0, b, 1.0)...)
			h.r.ObserveWeeklyUsage(a, WeeklyUsage{At: t0, ResetsAt: t0.Add(84 * time.Hour), Used: 0.5})
			h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0, ResetsAt: t0.Add(84 * time.Hour), Used: 0.9})
			h.r.ObserveWeeklyCredits(b, WeeklyCredits{At: t0, Grants: []WeeklyCredit{{Left: 1}}})
			if _, err := h.r.CommitAssignment(t0, "healthy", a, ReasonManual); err != nil {
				t.Fatal(err)
			}
			if _, err := h.r.CommitAssignment(t0, "other", b, ReasonManual); err != nil {
				t.Fatal(err)
			}
			var got []AccountID
			for i := range tc.want {
				got = append(got, h.route(t0, req(fmt.Sprintf("new-%d", i))).Account)
			}
			if !slices.Equal(got, tc.want) {
				t.Fatalf("placements = %v, want %v", got, tc.want)
			}
			if d := h.route(t0, req("healthy")); d.Kind != Dispatch || d.Account != a {
				t.Fatalf("healthy binding = %+v, want dispatch on acct-a", d)
			}
		})
	}
}

func TestWeeklyResetCreditsStackBeyondOneAllowance(t *testing.T) {
	for _, grants := range [][]WeeklyCredit{{{Left: 2}}, {{Left: 1}, {Left: 1}}} {
		h := newHarness(t, DefaultConfig(), accts(a, 2.0, b, 1.0)...)
		h.r.ObserveWeeklyUsage(a, WeeklyUsage{At: t0, ResetsAt: t0.Add(84 * time.Hour), Used: 0.5})
		h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0, ResetsAt: t0.Add(84 * time.Hour), Used: 0.9})
		h.r.ObserveWeeklyCredits(b, WeeklyCredits{At: t0, Grants: grants})
		grants[0].Left = 0
		var got []AccountID
		for i := range 12 {
			got = append(got, h.route(t0, req(fmt.Sprintf("new-%d", i))).Account)
		}
		want := []AccountID{b, b, b, b, b, b, a, b, b, b, b, b}
		if !slices.Equal(got, want) {
			t.Fatalf("placements = %v, want %v with stacked credit and twice the capacity on acct-a", got, want)
		}
	}
}

func TestWeeklyCreditRequiresFreshUsableOverallUsage(t *testing.T) {
	for _, tc := range []struct {
		name                string
		used                float64
		usageAt, creditsAt  time.Duration
		decisionAt, resetAt time.Duration
		endsAt              time.Duration
		missingUsage        bool
		want                AccountID
	}{
		{name: "fresh", used: 0.9, resetAt: 84 * time.Hour, want: b},
		{name: "missing usage", missingUsage: true, want: a},
		{name: "negative usage", used: -0.1, resetAt: 84 * time.Hour, want: a},
		{name: "usage over one", used: 1.1, resetAt: 84 * time.Hour, want: a},
		{name: "nan usage", used: math.NaN(), resetAt: 84 * time.Hour, want: a},
		{name: "infinite usage", used: math.Inf(1), resetAt: 84 * time.Hour, want: a},
		{name: "unknown reset", used: 0.9, want: a},
		{name: "reset reached", used: 0.9, resetAt: time.Minute, decisionAt: time.Minute, want: a},
		{name: "usage at freshness boundary", used: 0.9, resetAt: 84 * time.Hour, decisionAt: 15 * time.Minute, want: b},
		{name: "usage stale", used: 0.9, resetAt: 84 * time.Hour, decisionAt: 15*time.Minute + time.Nanosecond, want: a},
		{name: "credits at freshness boundary", used: 0.9, resetAt: 84 * time.Hour, usageAt: time.Hour, decisionAt: time.Hour, want: b},
		{name: "credits stale", used: 0.9, resetAt: 84 * time.Hour, usageAt: time.Hour, decisionAt: time.Hour + time.Nanosecond, want: a},
		{name: "full credit just before expiry", used: 0.9, resetAt: 84 * time.Hour, endsAt: time.Minute, decisionAt: time.Minute - time.Nanosecond, want: b},
		{name: "credit at exact expiry", used: 0.9, resetAt: 84 * time.Hour, endsAt: time.Minute, decisionAt: time.Minute, want: a},
	} {
		t.Run(tc.name, func(t *testing.T) {
			h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
			now := t0.Add(tc.decisionAt)
			if !tc.missingUsage {
				reset := time.Time{}
				if tc.resetAt != 0 {
					reset = t0.Add(tc.resetAt)
				}
				h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0.Add(tc.usageAt), ResetsAt: reset, Used: tc.used})
			}
			grant := WeeklyCredit{Left: 1}
			if tc.endsAt != 0 {
				grant.EndsAt = t0.Add(tc.endsAt)
			}
			h.r.ObserveWeeklyCredits(b, WeeklyCredits{At: t0.Add(tc.creditsAt), Grants: []WeeklyCredit{grant}})
			if d := h.route(now, req("next")); d.Kind != Place || d.Account != tc.want {
				t.Fatalf("got %+v, want placement on %s", d, tc.want)
			}
		})
	}
}

func TestWeeklyPreferenceUsesNewestValidHeaderOrBodyDespiteOmission(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	reset := t0.Add(84 * time.Hour)
	h.r.ObserveWeeklyUsage(a, WeeklyUsage{At: t0, ResetsAt: reset, Used: 0.2})
	check := func(at time.Time, want AccountID) {
		t.Helper()
		for _, id := range []AccountID{a, b} {
			h.r.ObserveOverage(id, OverageDisabled, at)
		}
		d, err := h.r.Decide(At(at), req("next"))
		if err != nil || d.Kind != Place || d.Account != want {
			t.Fatalf("at %v got %+v, %v; want placement on %s", at, d, err, want)
		}
	}
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0, ResetsAt: reset, Used: 0.1})
	check(t0, b)
	h.observe(b, Observation{At: t0.Add(time.Minute), Windows: []Window{usage(Weekly, 0.8, reset)}})
	check(t0.Add(time.Minute), a)
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0.Add(2 * time.Minute), ResetsAt: reset, Used: 0.1})
	check(t0.Add(2*time.Minute), b)
	h.observe(b, Observation{At: t0.Add(3 * time.Minute), Windows: []Window{usage(FiveHour, 0.9, t0.Add(5*time.Hour))}})
	check(t0.Add(3*time.Minute), b)
	for _, w := range []Window{
		{Kind: Weekly, ResetsAt: reset},
		usage(Weekly, math.NaN(), reset),
		usage(Weekly, math.Inf(1), reset),
		usage(Weekly, -0.1, reset),
		usage(Weekly, 1.1, reset),
		usage(Weekly, 0.9, time.Time{}),
	} {
		h.observe(b, Observation{At: t0.Add(4 * time.Minute), Windows: []Window{w}})
		check(t0.Add(4*time.Minute), b)
	}
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0.Add(time.Minute), ResetsAt: reset, Used: 0.8})
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0.Add(5 * time.Minute), ResetsAt: reset, Used: math.NaN()})
	check(t0.Add(5*time.Minute), b)
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0.Add(6 * time.Minute), ResetsAt: reset, Used: 0.8})
	check(t0.Add(6*time.Minute), a)
	h.observe(b, Observation{At: t0.Add(7 * time.Minute), Windows: []Window{usage(Weekly, 0.1, reset)}})
	check(t0.Add(7*time.Minute), b)
	h.r.ObserveWeeklyUsage(a, WeeklyUsage{At: t0.Add(23 * time.Minute), ResetsAt: reset, Used: 0.2})
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: t0.Add(23 * time.Minute), ResetsAt: reset, Used: 0.1})
	check(t0.Add(23*time.Minute), b)
}

func TestClockExpirationPreventsWeeklyEvidenceFromReturning(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	reading := WeeklyUsage{At: t0, ResetsAt: t0.Add(84 * time.Hour), Used: 0.9}
	credits := WeeklyCredits{At: t0, Grants: []WeeklyCredit{{Left: 1}}}
	h.r.ObserveWeeklyUsage(b, reading)
	h.r.ObserveWeeklyCredits(b, credits)
	check := func(now Instant, want AccountID) {
		t.Helper()
		for _, id := range []AccountID{a, b} {
			h.r.ObserveOverage(id, OverageDisabled, now.Stamp)
		}
		d, err := h.r.Decide(now, req("next"))
		if err != nil || d.Kind != Place || d.Account != want {
			t.Fatalf("at %+v got %+v, %v; want placement on %s", now, d, err, want)
		}
	}
	check(At(t0), b)
	mark := Instant{Stamp: t0.Add(time.Nanosecond), Wall: t0.Add(-time.Hour)}
	h.r.ExpireOverageChecks(mark)
	check(mark, a)
	reading.At, credits.At = mark.Stamp, mark.Stamp
	h.r.ObserveWeeklyUsage(b, reading)
	h.r.ObserveWeeklyCredits(b, credits)
	check(At(mark.Stamp), a)
	recovered := At(t0.Add(time.Minute))
	h.r.ExpireOverageChecks(recovered)
	h.r.ObserveWeeklyUsage(b, reading)
	h.r.ObserveWeeklyCredits(b, credits)
	check(recovered, a)
	reading.At = recovered.Stamp
	h.r.ObserveWeeklyUsage(b, reading)
	check(recovered, a)
	credits.At = recovered.Stamp
	h.r.ObserveWeeklyCredits(b, credits)
	check(recovered, b)
}

func TestWeeklyCreditsLeaveScopedAndFiveHourHeaderPreferencesUnchanged(t *testing.T) {
	for _, kind := range []WindowKind{FiveHour, Weekly} {
		for _, left := range []int{0, 100} {
			h := newHarness(t, DefaultConfig(), accts(a, 2.0, b, 1.0)...)
			for id, used := range map[AccountID]float64{a: 0.2, b: 0.1} {
				w := usage(kind, used, t0.Add(kind.Period()/2))
				if kind == Weekly {
					w.Models = []string{"claude-sonnet"}
				}
				h.observe(id, Observation{At: t0, Windows: []Window{w}})
			}
			h.r.ObserveWeeklyCredits(b, WeeklyCredits{At: t0, Grants: []WeeklyCredit{{Left: left}}})
			var got []AccountID
			for i := range 6 {
				got = append(got, h.route(t0, req(fmt.Sprintf("new-%d", i))).Account)
			}
			want := []AccountID{a, b, a, a, b, a}
			if !slices.Equal(got, want) {
				t.Fatalf("%s with %d credits placed %v, want %v", kind, left, got, want)
			}
		}
	}
}

func TestWeeklyCreditsOrderIndependentlyAndChooseAMigrationDestination(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0, c, 1.0)...)
	at := t0.Add(2 * time.Minute)
	h.r.ObserveWeeklyUsage(a, WeeklyUsage{At: at, ResetsAt: t0.Add(84 * time.Hour), Used: 0.5})
	h.r.ObserveWeeklyUsage(b, WeeklyUsage{At: at, ResetsAt: t0.Add(84 * time.Hour), Used: 0.9})
	h.r.ObserveWeeklyCredits(b, WeeklyCredits{At: t0.Add(time.Minute), Grants: []WeeklyCredit{{Left: 1}}})
	h.r.ObserveWeeklyCredits(b, WeeklyCredits{At: t0})
	if _, err := h.r.CommitAssignment(t0, "exhausted", c, ReasonManual); err != nil {
		t.Fatal(err)
	}
	h.report(at, c, "claude-sonnet-4-5", ClassExhausted, rejected(Weekly, at.Add(time.Hour)))
	if d := h.route(at, req("exhausted")); d.Kind != Migrate || d.From != c || d.Account != b || d.Reason != ReasonExhausted {
		t.Fatalf("migration = %+v, want acct-c to acct-b after exhaustion", d)
	}
}
