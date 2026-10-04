package main

import (
	"bytes"
	"encoding/json"
	"os"
	"testing"

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
	if checks != 116 {
		t.Fatalf("ran %d checks across the scripts, want 116", checks)
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
	if m.SubagentRequests == 0 || m.SubagentNotInherited != 0 {
		t.Fatalf("%d subagent requests with %d not inherited, want requests and all inherited", m.SubagentRequests, m.SubagentNotInherited)
	}
}
