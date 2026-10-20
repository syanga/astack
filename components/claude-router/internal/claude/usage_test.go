package claude

import (
	"reflect"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func accountStatus(t *testing.T, s *Service, id router.AccountID) AccountStatus {
	t.Helper()
	for _, a := range s.Status().Accounts {
		if a.ID == id {
			return a
		}
	}
	t.Fatalf("status has no account %s", id)
	return AccountStatus{}
}

func TestASettingsReadRecordsTheLimitWindowsForStatus(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
		"five_hour": map[string]any{"utilization": 38.0, "resets_at": "2026-10-04T05:00:00.5+00:00"},
		"seven_day": map[string]any{"utilization": 21.0, "resets_at": "2026-10-09T16:00:00Z"},
		"limits": []any{
			map[string]any{"kind": "session", "percent": 38, "resets_at": "2026-10-04T05:00:00.5+00:00", "scope": nil},
			map[string]any{"kind": "weekly_all", "percent": 21, "resets_at": "2026-10-09T16:00:00Z", "scope": nil},
			map[string]any{"kind": "weekly_scoped", "percent": 12, "resets_at": "2026-10-09T16:00:00Z",
				"scope": map[string]any{"model": map[string]any{"id": nil, "display_name": "Fable"}, "surface": nil}},
		},
	}})
	s := e.start()

	got := accountStatus(t, s, "acct-a")

	weekly := time.Date(2026, 10, 9, 16, 0, 0, 0, time.UTC)
	want := []UsageWindow{
		{Kind: router.FiveHour, Used: ptr(0.38), ResetsAt: time.Date(2026, 10, 4, 5, 0, 0, 5e8, time.UTC)},
		{Kind: router.Weekly, Used: ptr(0.21), ResetsAt: weekly},
		{Kind: router.Weekly, Model: "Fable", Used: ptr(0.12), ResetsAt: weekly},
	}
	if !sameWindows(got.Usage.Windows, want) || got.Usage.At.IsZero() {
		t.Fatalf("usage reading %+v, want the five-hour, weekly, and Fable windows as fractions", got.Usage)
	}
	if got.Overage.State != router.OverageDisabled {
		t.Fatalf("overage %q, want disabled", got.Overage.State)
	}
}

func TestMalformedLimitFieldsLeaveThePaidOverflowReadingIntact(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
		"five_hour": "full",
		"seven_day": map[string]any{"utilization": "high"},
		"limits":    map[string]any{"kind": "weekly_scoped"},
	}})
	s := e.start()

	r := e.send(msg{Session: sessionID(1)})

	if r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want acct-a to serve on its disabled reading", r.Status, r.Text)
	}
	if got := accountStatus(t, s, "acct-a"); len(got.Usage.Windows) != 0 {
		t.Fatalf("usage windows %+v, want none from fields of the wrong shape", got.Usage.Windows)
	}
}

func TestTheServiceReadsResetsAtStartWithoutCountingThemAsSettingsReads(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.upstream.setResets("acct-a", `{"eligible":true,"grants":[
		{"id":"g1","resets_left":2,"type":"weekly","clears":["seven_day"]},
		{"id":"g2","resets_left":1,"clears":["five_hour"]},
		{"id":"g3","resets_left":0,"type":"session"},
		{"id":"g4","resets_left":3,"type":"full","ends_at":"2020-01-01T00:00:00Z"},
		{"id":"g5","resets_left":4,"type":"monthly"}]}`)
	s := e.start()

	got := waitForResets(t, s, "acct-a")

	want := []ResetGrant{{Type: ResetWeekly, Left: 2}, {Type: ResetSession, Left: 1}}
	if !reflect.DeepEqual(got.Grants, want) {
		t.Fatalf("grants %+v, want 2 weekly and 1 session: no spent, ended, or unknown grant", got.Grants)
	}
	if n := len(e.upstream.usageReads()); n != 1 {
		t.Fatalf("settings reads %d, want the start read only", n)
	}
}

func TestAFailedResetsReadLeavesTheAccountServing(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.upstream.setResets("acct-a", `"unavailable"`)
	s := e.start()

	waitForResetsRead(t, e)
	r := e.send(msg{Session: sessionID(1)})

	if r.Text != served("acct-a") {
		t.Fatalf("got %d %q, want acct-a to serve", r.Status, r.Text)
	}
	if got := accountStatus(t, s, "acct-a"); !got.Resets.At.IsZero() {
		t.Fatalf("resets %+v, want no reading from an unreadable block", got.Resets)
	}
}

func waitForResets(t *testing.T, s *Service, id router.AccountID) ResetsReading {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if got := accountStatus(t, s, id); !got.Resets.At.IsZero() {
			return got.Resets
		}
		time.Sleep(10 * time.Millisecond)
	}
	t.Fatal("no resets reading within 5 s of start")
	return ResetsReading{}
}

func waitForResetsRead(t *testing.T, e *env) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for len(e.upstream.resetsReads()) == 0 {
		if time.Now().After(deadline) {
			t.Fatal("no resets read within 5 s of start")
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func ptr(f float64) *float64 { return &f }

func sameWindows(got, want []UsageWindow) bool {
	if len(got) != len(want) {
		return false
	}
	for i := range got {
		g, w := got[i], want[i]
		if g.Kind != w.Kind || g.Model != w.Model || !g.ResetsAt.Equal(w.ResetsAt) || (g.Used == nil) != (w.Used == nil) {
			return false
		}
		if g.Used != nil && *g.Used != *w.Used {
			return false
		}
	}
	return true
}

func TestASettingsReadWithoutReadableLimitFieldsClearsTheLimitReading(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.OverageCheckEvery = Duration(50 * time.Millisecond)
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false), Fields: map[string]any{
		"five_hour": map[string]any{"utilization": 38.0, "resets_at": "2026-10-04T05:00:00Z"},
	}})
	s := e.start()
	if got := accountStatus(t, s, "acct-a"); len(got.Usage.Windows) != 1 {
		t.Fatalf("usage windows %+v after the start read, want the five-hour window", got.Usage.Windows)
	}

	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false), Fields: map[string]any{"five_hour": "full"}})
	reads := len(e.upstream.usageReads())
	deadline := time.Now().Add(5 * time.Second)
	for len(e.upstream.usageReads()) < reads+2 {
		if time.Now().After(deadline) {
			t.Fatal("no later settings reads")
		}
		time.Sleep(10 * time.Millisecond)
	}

	if got := accountStatus(t, s, "acct-a"); len(got.Usage.Windows) != 0 || got.Overage.State != router.OverageDisabled {
		t.Fatalf("usage %+v, overage %q, want no limit windows from the malformed read and the disabled reading", got.Usage, got.Overage.State)
	}
}
