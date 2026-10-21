package claude

import (
	"context"
	"fmt"
	"slices"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func TestWeeklyResetGrantsFromUsageReadsChooseNewConversations(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	e.clock.set(now)
	for account, used := range map[string]float64{"acct-a": 50, "acct-b": 90} {
		e.upstream.setUsage(account, usageReply{Enabled: boolPtr(false), Fields: map[string]any{
			"seven_day": map[string]any{"utilization": used, "resets_at": now.Add(84 * time.Hour).Format(time.RFC3339)},
		}})
	}
	e.upstream.setResets("acct-b", `{"eligible":true,"grants":[{"type":"weekly","resets_left":1}]}`)
	s := e.start()
	waitForResets(t, s, "acct-b")
	var got []string
	for i := range 8 {
		r := e.send(msg{Session: sessionID(i + 1)})
		got = append(got, r.Text)
	}
	want := []string{served("acct-b"), served("acct-b"), served("acct-b"), served("acct-b"), served("acct-b"), served("acct-a"), served("acct-b"), served("acct-b")}
	if !slices.Equal(got, want) {
		t.Fatalf("new conversations = %v, want %v", got, want)
	}
	e.clock.set(now.Add(time.Minute))
	e.upstream.setResets("acct-b", `{"eligible":true,"grants":[]}`)
	s.overage.readResets(context.Background(), "acct-b")
	if r := e.send(msg{Session: sessionID(9)}); r.Text != served("acct-a") {
		t.Fatalf("after grants were cleared got %d %q, want acct-a", r.Status, r.Text)
	}
	if r := e.send(msg{Session: sessionID(1)}); r.Text != served("acct-b") {
		t.Fatalf("bound conversation got %d %q, want acct-b", r.Status, r.Text)
	}
}

func TestFreshWeeklyUsageBodiesPreserveExhaustionAndMigrationAffinity(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	e.clock.set(now)
	e.upstream.setUsage("acct-b", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
		"seven_day": map[string]any{"utilization": 90, "resets_at": now.Add(84 * time.Hour).Format(time.RFC3339)},
	}})
	e.upstream.setResets("acct-b", `{"eligible":true,"grants":[{"type":"weekly","resets_left":2}]}`)
	s := e.start()
	waitForResets(t, s, "acct-b")
	if r := e.send(msg{Session: sessionID(1)}); r.Text != served("acct-b") {
		t.Fatalf("first placement = %d %q, want acct-b", r.Status, r.Text)
	}
	e.clock.set(now.Add(time.Minute))
	e.upstream.script("acct-b", reply{Status: 429, Header: map[string]string{
		"anthropic-ratelimit-unified-7d-status": "rejected",
		"anthropic-ratelimit-unified-7d-reset":  fmt.Sprint(now.Add(24 * time.Hour).Unix()),
	}})
	if r := e.send(msg{Session: sessionID(1)}); r.Text != served("acct-a") {
		t.Fatalf("exhausted conversation = %d %q, want migration to acct-a", r.Status, r.Text)
	}
	e.clock.set(now.Add(2 * time.Minute))
	e.upstream.setUsage("acct-b", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
		"seven_day": map[string]any{"utilization": 1, "resets_at": now.Add(84 * time.Hour).Format(time.RFC3339)},
	}})
	s.overage.read(context.Background(), "acct-b")
	if reason := s.router.Admit(s.clock.instant(), "acct-b", "claude-sonnet-4-5"); reason != router.ReasonExhausted {
		t.Fatalf("admission after fresh usage = %s, want exhausted", reason)
	}
	for _, session := range []int{1, 2} {
		if r := e.send(msg{Session: sessionID(session)}); r.Text != served("acct-a") {
			t.Fatalf("session %d after fresh usage = %d %q, want acct-a", session, r.Status, r.Text)
		}
	}
}

func TestResetGrantsDoNotBoostBodyFiveHourOrScopedWeeklyWindows(t *testing.T) {
	for _, kind := range []string{"five_hour", "scoped weekly", "missing"} {
		t.Run(kind, func(t *testing.T) {
			e := newEnv(t, "acct-a", "acct-b")
			now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
			e.clock.set(now)
			fields := map[string]any{}
			switch kind {
			case "five_hour":
				fields["five_hour"] = map[string]any{"utilization": 0, "resets_at": now.Add(time.Minute).Format(time.RFC3339)}
			case "scoped weekly":
				fields["limits"] = []any{map[string]any{
					"kind": "weekly_scoped", "percent": 0, "resets_at": now.Add(time.Minute).Format(time.RFC3339),
					"scope": map[string]any{"model": map[string]any{"display_name": "Sonnet"}},
				}}
			}
			e.upstream.setUsage("acct-b", usageReply{Enabled: boolPtr(false), Fields: fields})
			e.upstream.setResets("acct-b", `{"eligible":true,"grants":[{"type":"full","resets_left":100}]}`)
			s := e.start()
			waitForResets(t, s, "acct-b")
			var got []string
			for i := range 4 {
				got = append(got, e.send(msg{Session: sessionID(i + 1)}).Text)
			}
			want := []string{served("acct-a"), served("acct-b"), served("acct-a"), served("acct-b")}
			if !slices.Equal(got, want) {
				t.Fatalf("placements = %v, want %v without account-wide weekly usage", got, want)
			}
		})
	}
}

func TestResetReadFailuresDoNotRenewCreditsAndIneligibilityClearsThem(t *testing.T) {
	for _, result := range []string{"failure", "ineligible", "ineligible malformed grants", "malformed grant"} {
		t.Run(result, func(t *testing.T) {
			e := newEnv(t, "acct-a", "acct-b")
			now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
			e.clock.set(now)
			e.upstream.setUsage("acct-b", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
				"seven_day": map[string]any{"utilization": 90, "resets_at": now.Add(84 * time.Hour).Format(time.RFC3339)},
			}})
			e.upstream.setResets("acct-b", `{"eligible":true,"grants":[{"type":"weekly","resets_left":1}]}`)
			s := e.start()
			waitForResets(t, s, "acct-b")
			if r := e.send(msg{Session: sessionID(1)}); r.Text != served("acct-b") {
				t.Fatalf("initial placement = %d %q, want acct-b", r.Status, r.Text)
			}
			e.clock.set(now.Add(30 * time.Minute))
			block := `"unavailable"`
			switch result {
			case "ineligible":
				block = `{"eligible":false,"grants":[{"type":"weekly","resets_left":10}]}`
			case "ineligible malformed grants":
				block = `{"eligible":false,"grants":"unavailable"}`
			case "malformed grant":
				block = `{"eligible":true,"grants":[{"type":"weekly","resets_left":10,"ends_at":42}]}`
			}
			e.upstream.setResets("acct-b", block)
			s.overage.readResets(context.Background(), "acct-b")
			s.overage.read(context.Background(), "acct-b")
			want := "acct-a"
			if result == "failure" {
				want = "acct-b"
			}
			if r := e.send(msg{Session: sessionID(2)}); r.Text != served(want) {
				t.Fatalf("after %s got %d %q, want %s", result, r.Status, r.Text, want)
			}
			if result != "failure" {
				return
			}
			e.clock.set(now.Add(time.Hour + time.Nanosecond))
			s.overage.read(context.Background(), "acct-b")
			if r := e.send(msg{Session: sessionID(3)}); r.Text != served("acct-a") {
				t.Fatalf("after credit freshness ended got %d %q, want acct-a", r.Status, r.Text)
			}
		})
	}
}

func TestWeeklyPreferencesExpireAcrossAServiceClockHold(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	e.clock.set(now)
	e.upstream.setUsage("acct-b", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
		"seven_day": map[string]any{"utilization": 90, "resets_at": now.Add(84 * time.Hour).Format(time.RFC3339)},
	}})
	e.upstream.setResets("acct-b", `{"eligible":true,"grants":[{"type":"weekly","resets_left":1}]}`)
	s := e.start()
	waitForResets(t, s, "acct-b")
	check := func(session int, want string) {
		t.Helper()
		if r := e.send(msg{Session: sessionID(session)}); r.Text != served(want) {
			t.Fatalf("session %d got %d %q, want %s", session, r.Status, r.Text, want)
		}
	}
	check(1, "acct-b")
	e.clock.set(now.Add(-time.Hour))
	for _, id := range []router.AccountID{"acct-a", "acct-b"} {
		s.overage.read(context.Background(), id)
		s.overage.readResets(context.Background(), id)
	}
	check(2, "acct-a")
	e.upstream.script("acct-b", reply{Header: map[string]string{
		"anthropic-ratelimit-unified-7d-utilization": "0",
		"anthropic-ratelimit-unified-7d-reset":       fmt.Sprint(now.Add(84 * time.Hour).Unix()),
	}})
	check(1, "acct-b")
	e.clock.set(now.Add(time.Minute))
	for _, id := range []router.AccountID{"acct-a", "acct-b"} {
		s.overage.read(context.Background(), id)
	}
	check(3, "acct-a")
	s.overage.readResets(context.Background(), "acct-b")
	check(4, "acct-b")
}

func TestServiceWeeklyPlacementUsesNewestValidHeaderOrUsageBody(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
	e.clock.set(now)
	reset := now.Add(84 * time.Hour)
	setBody := func(account string, used any) {
		e.upstream.setUsage(account, usageReply{Enabled: boolPtr(false), Fields: map[string]any{
			"seven_day": map[string]any{"utilization": used, "resets_at": reset.Format(time.RFC3339)},
		}})
	}
	setBody("acct-a", 20)
	setBody("acct-b", 10)
	s := e.start()
	if r := e.send(msg{Session: sessionID(1)}); r.Text != served("acct-b") {
		t.Fatalf("body-backed placement = %d %q, want acct-b", r.Status, r.Text)
	}
	e.clock.set(now.Add(time.Minute))
	setBody("acct-b", 80)
	s.overage.read(context.Background(), "acct-b")
	if r := e.send(msg{Session: sessionID(2)}); r.Text != served("acct-a") {
		t.Fatalf("after newer body got %d %q, want acct-a", r.Status, r.Text)
	}
	check := func(want router.AccountID) {
		t.Helper()
		d, err := s.router.Decide(s.clock.instant(), router.Request{Conversation: "new", Model: "claude-sonnet-4-5"})
		if err != nil || d.Kind != router.Place || d.Account != want {
			t.Fatalf("got %+v, %v; want new placement on %s", d, err, want)
		}
	}
	header := func(minute int, values map[string]string, want router.AccountID) {
		t.Helper()
		e.clock.set(now.Add(time.Duration(minute) * time.Minute))
		e.upstream.script("acct-b", reply{Header: values})
		if r := e.send(msg{Session: sessionID(1)}); r.Text != served("acct-b") {
			t.Fatalf("existing binding got %d %q, want acct-b", r.Status, r.Text)
		}
		check(want)
	}
	header(2, map[string]string{
		"anthropic-ratelimit-unified-7d-utilization": "0.1",
		"anthropic-ratelimit-unified-7d-reset":       fmt.Sprint(reset.Unix()),
	}, "acct-b")
	e.clock.set(now.Add(3 * time.Minute))
	s.overage.read(context.Background(), "acct-b")
	check("acct-a")
	header(4, map[string]string{
		"anthropic-ratelimit-unified-7d-utilization": "NaN",
		"anthropic-ratelimit-unified-7d-reset":       fmt.Sprint(reset.Unix()),
	}, "acct-a")
	e.clock.set(now.Add(5 * time.Minute))
	setBody("acct-b", 10)
	s.overage.read(context.Background(), "acct-b")
	check("acct-b")
	header(6, map[string]string{"anthropic-ratelimit-unified-5h-utilization": "0.9"}, "acct-b")
	e.clock.set(now.Add(7 * time.Minute))
	setBody("acct-b", "missing")
	s.overage.read(context.Background(), "acct-b")
	check("acct-b")
	e.clock.set(now.Add(23 * time.Minute))
	setBody("acct-b", 10)
	for _, id := range []router.AccountID{"acct-a", "acct-b"} {
		s.overage.read(context.Background(), id)
	}
	check("acct-b")
}

func TestOnlyEligibleValidWeeklyResetGrantsInfluencePlacement(t *testing.T) {
	for _, tc := range []struct {
		name, block, want string
	}{
		{"weekly", `{"eligible":true,"grants":[{"type":"weekly","resets_left":1}]}`, "acct-b"},
		{"full", `{"eligible":true,"grants":[{"type":"full","resets_left":1}]}`, "acct-b"},
		{"clears weekly", `{"eligible":true,"grants":[{"clears":["seven_day"],"resets_left":1}]}`, "acct-b"},
		{"clears full", `{"eligible":true,"grants":[{"clears":["five_hour","seven_day"],"resets_left":1}]}`, "acct-b"},
		{"session", `{"eligible":true,"grants":[{"type":"session","resets_left":9}]}`, "acct-a"},
		{"unknown", `{"eligible":true,"grants":[{"type":"monthly","resets_left":9}]}`, "acct-a"},
		{"spent", `{"eligible":true,"grants":[{"type":"weekly","resets_left":0}]}`, "acct-a"},
		{"negative", `{"eligible":true,"grants":[{"type":"weekly","resets_left":-1}]}`, "acct-a"},
		{"ineligible", `{"eligible":false,"grants":[{"type":"weekly","resets_left":9}]}`, "acct-a"},
		{"missing eligibility", `{"grants":[{"type":"weekly","resets_left":9}]}`, "acct-a"},
		{"malformed expiry", `{"eligible":true,"grants":[{"type":"weekly","resets_left":9,"ends_at":"later"}]}`, "acct-a"},
		{"empty expiry", `{"eligible":true,"grants":[{"type":"weekly","resets_left":9,"ends_at":""}]}`, "acct-a"},
		{"nonstring expiry", `{"eligible":true,"grants":[{"type":"weekly","resets_left":9,"ends_at":42}]}`, "acct-a"},
		{"malformed alongside valid", `{"eligible":true,"grants":[{"type":"weekly","resets_left":9,"ends_at":42},{"type":"weekly","resets_left":1}]}`, "acct-b"},
		{"expired", `{"eligible":true,"grants":[{"type":"weekly","resets_left":9,"ends_at":"2026-10-05T09:00:00Z"}]}`, "acct-a"},
		{"valid expiry", `{"eligible":true,"grants":[{"type":"weekly","resets_left":1,"ends_at":"2026-10-05T09:00:01Z"}]}`, "acct-b"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t, "acct-a", "acct-b")
			now := time.Date(2026, 10, 5, 9, 0, 0, 0, time.UTC)
			e.clock.set(now)
			for account, used := range map[string]float64{"acct-a": 50, "acct-b": 90} {
				e.upstream.setUsage(account, usageReply{Enabled: boolPtr(false), Fields: map[string]any{
					"seven_day": map[string]any{"utilization": used, "resets_at": now.Add(84 * time.Hour).Format(time.RFC3339)},
				}})
			}
			e.upstream.setResets("acct-b", tc.block)
			s := e.start()
			waitForResets(t, s, "acct-b")
			if r := e.send(msg{Session: sessionID(1)}); r.Text != served(tc.want) {
				t.Fatalf("got %d %q, want %s", r.Status, r.Text, tc.want)
			}
		})
	}
}
