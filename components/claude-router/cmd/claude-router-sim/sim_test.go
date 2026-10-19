package main

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func TestMain(m *testing.M) {
	if os.Getenv("CLAUDE_ROUTER_SIM_AS_MAIN") == "1" {
		main()
		os.Exit(0)
	}
	os.Exit(m.Run())
}

func TestScriptedFixturesMatchTheirLiteralExpectations(t *testing.T) {
	rep, err := CheckScripts("../../testdata/scripts")
	if err != nil {
		t.Fatal(err)
	}
	checks := 0
	for _, r := range rep.Results {
		for _, c := range r.Checks {
			checks++
			if !c.Pass {
				t.Errorf("%s under %s, step %d: expected %+v, got %+v bound to %s", r.Script, r.Rule, c.Step, c.Expected, c.Got, c.BoundTo)
			}
		}
	}
	if checks != 124 {
		t.Fatalf("ran %d checks across the scripts, want 124", checks)
	}
}

func TestScriptCheckFailsOnAWrongExpectation(t *testing.T) {
	sc := Script{
		Name:     "wrong",
		Start:    serveStart,
		Accounts: []router.Account{{ID: "acct-a", Capacity: 1}, {ID: "acct-b", Capacity: 1}},
		Steps: []Step{
			{Overage: &ScriptOverage{State: router.OverageDisabled}},
			{Route: &router.Request{Conversation: "c", Model: "m", Attempt: 1}, Expect: map[string]Expected{"any": {Account: "acct-b"}}},
		},
	}

	res, err := RunScript(sc, router.DefaultConfig(), t.TempDir())

	if err != nil {
		t.Fatal(err)
	}
	if res.Pass || res.Checks[0].Got.Account != "acct-a" {
		t.Fatalf("a wrong expectation passed or the placement was %s, want a failed check against acct-a", res.Checks[0].Got.Account)
	}
}

func smallFixture(t *testing.T) Fixture {
	t.Helper()
	var fx Fixture
	if err := loadJSON("../../testdata/sim/mixed-48h.json", &fx); err != nil {
		t.Fatal(err)
	}
	fx.Hours = 14
	fx.Workload.Conversations = 40
	fx.Workload.ArrivalMeanMinutes = 15
	return fx
}

func TestSimulationWithTheSameSeedIsByteIdentical(t *testing.T) {
	fx := smallFixture(t)
	encode := func(seed uint64) []byte {
		m, tr, err := Simulate(fx, seed, router.DefaultConfig(), t.TempDir())
		if err != nil {
			t.Fatal(err)
		}
		data, err := json.Marshal(RunResult{Fixture: fx.Name, Seed: seed, Metrics: m, Trace: tr})
		if err != nil {
			t.Fatal(err)
		}
		return data
	}

	first, second, other := encode(7), encode(7), encode(8)

	if !bytes.Equal(first, second) {
		t.Fatal("two runs with seed 7 produced different output")
	}
	if bytes.Equal(first, other) {
		t.Fatal("seeds 7 and 8 produced identical output, so the seed is not reaching the workload")
	}
}

func TestSimulationKeepsHealthyConversationsAndRecoversEveryBinding(t *testing.T) {
	fx := smallFixture(t)

	m, tr, err := Simulate(fx, 3, router.DefaultConfig(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	restarts := 0
	for _, e := range tr {
		if e.Event == "restart" {
			restarts++
		}
	}

	if restarts != 1 || m.Restarts != 1 {
		t.Fatalf("restarts traced %d, counted %d; the fixture crashes once before hour 14", restarts, m.Restarts)
	}
	if m.RecoveredBindings == 0 || m.RecoveryMismatches != 0 {
		t.Fatalf("recovered %d bindings with %d mismatches, want some bindings and no mismatches", m.RecoveredBindings, m.RecoveryMismatches)
	}
	if m.CompletedTurns == 0 || m.HealthyAutoMigrations != 0 {
		t.Fatalf("completed %d turns with %d healthy automatic migrations, want work done and none", m.CompletedTurns, m.HealthyAutoMigrations)
	}
	if m.ParallelFirstGroups == 0 || m.ParallelDisagreements != 0 {
		t.Fatalf("%d parallel first-request groups with %d disagreements, want groups and no disagreement", m.ParallelFirstGroups, m.ParallelDisagreements)
	}
	if m.SubagentRequests == 0 || m.SubagentNotInherited != 0 || m.SubagentServed != m.SubagentRequests {
		t.Fatalf("%d subagent requests with %d not inherited and %d served, want requests, all inherited and all served",
			m.SubagentRequests, m.SubagentNotInherited, m.SubagentServed)
	}
}

func oneConversation(turns int, accounts ...SimAccount) Fixture {
	return Fixture{
		Name: "one-conversation", Start: serveStart, Hours: 3, FiveHourUnits: 10, WeeklyUnits: 1000, OverageCheckMinutes: 15,
		Accounts: accounts,
		Workload: Workload{Conversations: 1, ArrivalMeanMinutes: 0.001, TurnsMin: turns, TurnsMax: turns, GapMeanMinutes: 0.001,
			InitialContext: 1000, SubagentContext: 1000, Models: []string{"claude-sonnet-4-5"}, ParallelFirst: 1},
		Cache: CacheModel{TTLMinutes: 5, WriteWeight: 1},
	}
}

func TestHeaderlessExhaustionWaitsWithoutAKnownReset(t *testing.T) {
	fx := oneConversation(1, SimAccount{ID: "acct-e", Capacity: 1, FiveHourUsed: 1, FiveHourResetHours: 2, WeeklyResetHours: 100, HideHeaders: true})
	cfg := router.DefaultConfig()
	cfg.UnknownResetRecheck = 7 * time.Minute

	m, tr, err := Simulate(fx, 1, cfg, t.TempDir())
	if err != nil {
		t.Fatal(err)
	}

	waits := 0
	for _, e := range tr {
		if e.Event != "wait" {
			continue
		}
		waits++
		if *e.ResetKnown || !e.Until.Equal(e.At.Add(7*time.Minute)) {
			t.Fatalf("wait at %s until %s with reset_known=%v, want an unknown reset rechecked at %s",
				e.At.Format(time.RFC3339), e.Until.Format(time.RFC3339), *e.ResetKnown, e.At.Add(7*time.Minute).Format(time.RFC3339))
		}
	}
	if waits != 18 || m.CompletedTurns != 1 {
		t.Fatalf("%d waits and %d completed turns, want 18 rechecks before the hidden reset at 11:00 and then the turn", waits, m.CompletedTurns)
	}
}

func TestSubagentMigrationServesOnADestinationWhoseWindowHasReset(t *testing.T) {
	fx := oneConversation(2,
		SimAccount{ID: "acct-a", Capacity: 1, FiveHourUsed: 0.7, FiveHourResetHours: 5, WeeklyResetHours: 100},
		SimAccount{ID: "acct-b", Capacity: 0.5, FiveHourUsed: 1, FiveHourResetHours: 0.5, WeeklyResetHours: 100})
	fx.Workload.SubagentProbability = 1
	fx.Workload.IdleProbability, fx.Workload.IdleMinutes = 1, 60

	m, tr, err := Simulate(fx, 1, router.DefaultConfig(), t.TempDir())
	if err != nil {
		t.Fatal(err)
	}

	var moved []string
	for _, e := range tr {
		if e.Event == "migrate" {
			moved = append(moved, string(e.Agent)+":"+string(e.From)+"->"+string(e.Account))
		}
	}
	if len(moved) != 1 || moved[0] != "agent-2:acct-a->acct-b" {
		t.Fatalf("migrations %v, want the second turn's subagent moved from exhausted acct-a to acct-b", moved)
	}
	if m.SubagentRequests != 2 || m.SubagentServed != 2 {
		t.Fatalf("%d of %d subagent requests served, want both, the second on acct-b after its 09:30 reset", m.SubagentServed, m.SubagentRequests)
	}
}

func TestSubagentBlockedAfterItsParentWaitsAndThenRuns(t *testing.T) {
	cfg := router.DefaultConfig()
	cfg.UnknownResetRecheck = 7 * time.Minute
	reset := serveStart.Add(time.Hour)
	for _, tc := range []struct {
		name      string
		hide      bool
		wantKnown bool
	}{
		{"visible exhaustion", false, true},
		{"headerless exhaustion", true, false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			fx := oneConversation(1, SimAccount{ID: "acct-a", Capacity: 1, FiveHourUsed: 0.9, FiveHourResetHours: 1, WeeklyResetHours: 100, HideHeaders: tc.hide})
			fx.Workload.SubagentProbability = 1

			m, tr, err := Simulate(fx, 1, cfg, t.TempDir())
			if err != nil {
				t.Fatal(err)
			}

			var first *Event
			for i, e := range tr {
				if e.Event == "wait" && e.Agent == "agent-1" {
					first = &tr[i]
					break
				}
			}
			if first == nil {
				t.Fatalf("no wait traced for the subagent whose parent exhausted acct-a; trace %+v", tr)
			}
			wantUntil := reset
			if !tc.wantKnown {
				wantUntil = first.At.Add(7 * time.Minute)
			}
			if *first.ResetKnown != tc.wantKnown || !first.Until.Equal(wantUntil) {
				t.Fatalf("subagent waited until %s with reset_known=%v, want %s with reset_known=%v",
					first.Until.Format(time.RFC3339), *first.ResetKnown, wantUntil.Format(time.RFC3339), tc.wantKnown)
			}
			if m.SubagentRequests != 1 || m.SubagentServed != 1 || m.UnfinishedConversations != 0 {
				t.Fatalf("%d of %d subagent requests served with %d unfinished conversations, want the subagent served after the 10:00 reset",
					m.SubagentServed, m.SubagentRequests, m.UnfinishedConversations)
			}
		})
	}
}
