package main

import (
	"bytes"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

// The fixtures in testdata/status are the screens of the selected output
// layout (mock C with its three additions). Each case rebuilds the sample
// state those screens show.

var pacific = mustLocation("America/Los_Angeles")

func mustLocation(name string) *time.Location {
	loc, err := time.LoadLocation(name)
	if err != nil {
		panic(err)
	}
	return loc
}

// at returns a time on the sample day, Thursday 2026-10-15, or daysAgo days
// before it, in Pacific time.
func at(daysAgo, hour, minute int) time.Time {
	return time.Date(2026, 10, 15-daysAgo, hour, minute, 0, 0, pacific)
}

var sampleNow = at(0, 14, 20)

func util(f float64) *float64 { return &f }

func window(kind router.WindowKind, u float64, reset time.Time) router.Window {
	return router.Window{Kind: kind, Utilization: util(u), ResetsAt: reset}
}

func sampleStatus() claude.Status {
	return claude.Status{
		Listen: "127.0.0.1:18787", Now: sampleNow, Started: sampleNow.Add(-(6*time.Hour + 15*time.Minute)),
		PID: 48211, Journal: "ok", Binary: "40937db", SDK: "v8.0.11",
		QuotaFreshFor: claude.Duration(15 * time.Minute), OverageFreshFor: claude.Duration(30 * time.Minute),
		ActiveFor: claude.Duration(time.Hour),
	}
}

func disabledAt(t time.Time) claude.AccountStatus {
	var a claude.AccountStatus
	a.Overage.State, a.Overage.At, a.Overage.Source = router.OverageDisabled, t, "settings"
	return a
}

func binding(hash, account string, reason router.Reason, since, last time.Time, active bool) claude.BindingStatus {
	return claude.BindingStatus{Conversation: hash, Account: router.AccountID(account), Reason: reason,
		Since: since, Served: true, LastRequest: last, Active: active}
}

func cache(input, write, read, output int64, requests int) *usageTotals {
	return &usageTotals{Requests: requests, Input: input, Write: write, Read: read, Output: output}
}

func healthyReport() report {
	st := sampleStatus()
	work := disabledAt(at(0, 14, 16))
	work.ID, work.Capacity, work.Bound, work.Active = "work", 5, 3, 2
	work.Quota = router.Observation{At: at(0, 14, 15), Windows: []router.Window{
		window(router.FiveHour, 0.38, at(0, 16, 40)), window(router.Weekly, 0.21, at(-3, 9, 0))}}
	alumni := disabledAt(at(0, 14, 16))
	alumni.ID, alumni.Capacity, alumni.Bound, alumni.Active = "alumni", 1, 2, 1
	alumni.Quota = router.Observation{At: at(0, 14, 15), Windows: []router.Window{
		window(router.FiveHour, 0.09, at(0, 17, 55)), window(router.Weekly, 0.64, at(-2, 6, 0))}}
	st.Accounts = []claude.AccountStatus{work, alumni}
	return report{Now: sampleNow, Manager: "launchd", Listen: st.Listen, Running: true, Status: st,
		StateShown: "~/.local/state/astack/claude-router", Sup: supervision{Runs: 1},
		Digest: digest{Accounts: map[router.AccountID]*usageTotals{
			"work": cache(20_000, 300_000, 4_300_000, 160_000, 190), "alumni": cache(7_000, 150_000, 1_200_000, 50_000, 70)}}}
}

func waitingReport() report {
	r := healthyReport()
	work := disabledAt(at(0, 14, 16))
	work.ID, work.Capacity, work.Bound, work.Active = "work", 5, 2, 1
	work.Rejections = []router.Rejection{{Window: router.Window{Kind: router.FiveHour, Rejected: true, ResetsAt: at(0, 16, 40)}, At: at(0, 13, 52)}}
	work.Quota = router.Observation{At: at(0, 13, 52), Windows: []router.Window{
		{Kind: router.FiveHour, Rejected: true, ResetsAt: at(0, 16, 40)}, window(router.Weekly, 0.31, at(-3, 9, 0))}}
	var alumni claude.AccountStatus
	alumni.ID, alumni.Capacity, alumni.Bound, alumni.Active = "alumni", 1, 3, 2
	alumni.Overage.State, alumni.Overage.At, alumni.Overage.Source = router.OverageEnabled, at(0, 14, 5), "settings"
	alumni.Quota = router.Observation{At: at(0, 14, 4), Windows: []router.Window{
		window(router.FiveHour, 0.52, at(0, 17, 55)), window(router.Weekly, 0.66, at(-2, 6, 0))}}
	r.Status.Accounts = []claude.AccountStatus{work, alumni}
	r.Status.Bindings = []claude.BindingStatus{
		binding("e91f675b4f8a", "alumni", router.ReasonCapacity, at(0, 14, 2), at(0, 14, 4), true),
		binding("3d79daedf017", "alumni", router.ReasonExhausted, at(0, 13, 52), at(0, 14, 9), true),
		binding("87ef7f9f6285", "work", router.ReasonResetPreference, at(0, 13, 12), at(0, 13, 53), true),
		binding("a833c9b837a0", "work", router.ReasonCapacity, at(1, 22, 10), at(1, 23, 2), false),
		binding("5c2e19b0d4a7", "alumni", router.ReasonManual, at(2, 16, 30), at(2, 17, 44), false),
	}
	r.Digest = digest{
		Accounts: map[router.AccountID]*usageTotals{
			"work": cache(19_000, 318_000, 4_220_000, 161_000, 188), "alumni": cache(7_000, 207_000, 1_240_000, 58_000, 71)},
		Conversations: map[string]*conversationDigest{
			"87ef7f9f6285": {Last: at(0, 13, 53), Since: at(0, 13, 53), Decision: router.Wait, Until: at(0, 16, 40), Context: 118_000},
			"3d79daedf017": {Last: at(0, 14, 9), Since: at(0, 14, 9), Decision: router.Refuse, Reason: string(router.ReasonOverageEnabled), Context: 52_000},
			"e91f675b4f8a": {Last: at(0, 14, 4), Since: at(0, 14, 2), Decision: router.Dispatch, Context: 47_000},
			"a833c9b837a0": {Last: at(1, 23, 2), Since: at(1, 22, 10), Decision: router.Dispatch, Context: 31_000},
			"5c2e19b0d4a7": {Last: at(2, 17, 44), Since: at(2, 16, 30), Decision: router.Dispatch, Context: 12_000},
		},
		Migrations: []migration{
			{At: at(2, 16, 30), Conversation: "5c2e19b0d4a7", From: "work", To: "alumni", Reason: string(router.ReasonManual)},
			{At: at(0, 13, 52), Conversation: "3d79daedf017", From: "work", To: "alumni", Reason: string(router.ReasonExhausted)},
		},
	}
	return r
}

func overflowStaleReport() report {
	r := healthyReport()
	r.Status.Accounts[1].Overage.At = at(0, 13, 58)
	r.Status.Accounts[1].Overage.Failure = "usage endpoint answered 502"
	return r
}

func needsLoginReport() report {
	r := healthyReport()
	work, alumni := &r.Status.Accounts[0], &r.Status.Accounts[1]
	work.Bound, work.Active = 4, 3
	work.Quota.Windows = []router.Window{window(router.FiveHour, 0.44, at(0, 16, 40)), window(router.Weekly, 0.23, at(-3, 9, 0))}
	alumni.NeedsLogin, alumni.NeedsLoginAt = true, at(0, 14, 7)
	alumni.Quota.Windows = []router.Window{window(router.FiveHour, 0.11, at(0, 17, 55)), window(router.Weekly, 0.64, at(-2, 6, 0))}
	r.Status.Bindings = []claude.BindingStatus{
		binding("e91f675b4f8a", "alumni", router.ReasonCapacity, at(0, 14, 2), at(0, 14, 7), true),
		binding("9a41c07e2b13", "work", router.ReasonNeverServed, at(0, 14, 7), at(0, 14, 7), true),
	}
	r.Digest.Conversations = map[string]*conversationDigest{
		"e91f675b4f8a": {Last: at(0, 14, 7), Since: at(0, 14, 7), Decision: router.Reauth, Reason: string(router.ReasonNeedsLogin)},
		"9a41c07e2b13": {Last: at(0, 14, 7), Since: at(0, 14, 7), Decision: router.Migrate},
	}
	r.Digest.Migrations = []migration{{At: at(0, 14, 7), Conversation: "9a41c07e2b13", From: "alumni", To: "work", Reason: string(router.ReasonNeverServed)}}
	return r
}

func TestStatusMatchesTheSelectedLayout(t *testing.T) {
	stopped := report{Now: sampleNow, Manager: "launchd", Listen: "127.0.0.1:18787"}
	gaveUp := stopped
	gaveUp.Sup = supervision{Runs: 5, GaveUpAt: at(0, 14, 12), LastError: "listen tcp 127.0.0.1:18787: bind: address already in use", LastErrorAt: at(0, 14, 11)}
	for _, tc := range []struct {
		fixture string
		r       report
		verbose bool
	}{
		{"healthy", healthyReport(), false},
		{"waiting", waitingReport(), false},
		{"waiting-verbose", waitingReport(), true},
		{"overflow-stale", overflowStaleReport(), false},
		{"needs-login", needsLoginReport(), false},
		{"stopped", stopped, false},
		{"gave-up", gaveUp, false},
	} {
		t.Run(tc.fixture, func(t *testing.T) {
			want, err := os.ReadFile(filepath.Join("testdata", "status", tc.fixture+".txt"))
			if err != nil {
				t.Fatal(err)
			}
			var got bytes.Buffer
			writeStatus(&got, tc.r, tc.verbose)
			if got.String() != string(want) {
				t.Fatalf("status output differs from %s.txt\n--- got\n%s--- want\n%s", tc.fixture, got.String(), want)
			}
		})
	}
}

func TestDefaultStatusFitsEightyColumns(t *testing.T) {
	var got bytes.Buffer
	writeStatus(&got, waitingReport(), false)
	for _, line := range strings.Split(strings.TrimSuffix(got.String(), "\n"), "\n") {
		if len(line) > 80 {
			t.Fatalf("line of %d columns: %q", len(line), line)
		}
	}
}

func TestDigestReadsCountersWaitsAndMigrationsFromEvents(t *testing.T) {
	events := `{"at":"2026-10-15T13:00:00Z","kind":"request","conversation":"aaaaaaaaaaaa","account":"work","decision":"place","usage":{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":90,"cache_read_input_tokens":0}}
{"at":"2026-10-15T13:05:00Z","kind":"request","conversation":"aaaaaaaaaaaa","account":"work","decision":"dispatch","usage":{"input_tokens":10,"output_tokens":5,"cache_creation_input_tokens":10,"cache_read_input_tokens":80}}
{"at":"2026-10-15T13:10:00Z","kind":"migrated","conversation":"aaaaaaaaaaaa","account":"alumni","from":"work","reason":"exhausted"}
{"at":"2026-10-15T13:11:00Z","kind":"request","conversation":"aaaaaaaaaaaa","decision":"wait","until":"2026-10-15T16:40:00Z"}
not json
{"at":"2026-10-15T13:12:00Z","kind":"request","conversation":"aaaaaaaaaaaa","decision":"wait","until":"2026-10-15T16:40:00Z"}
{"at":"2026-10-15T13:13:00Z","kind":"request","conversation":"bbbbbbbbbbbb","account":"alumni","decision":"dispatch","usage":{"input_tokens":null,"output_tokens":null,"cache_creation_input_tokens":null,"cache_read_input_tokens":null}}
`
	d := digest{Accounts: map[router.AccountID]*usageTotals{}, Conversations: map[string]*conversationDigest{}}
	if err := d.read(strings.NewReader(events)); err != nil {
		t.Fatal(err)
	}
	work := d.Accounts["work"]
	if work == nil || work.Requests != 2 || work.Input != 20 || work.Write != 100 || work.Read != 80 || work.Output != 10 {
		t.Fatalf("work totals %+v, want 2 requests, input 20, write 100, read 80, output 10", work)
	}
	if share, ok := work.readShare(); !ok || share != 40 {
		t.Fatalf("work read share %d %v, want 40%%", share, ok)
	}
	if _, ok := d.Accounts["alumni"].readShare(); ok {
		t.Fatal("alumni has a read share, want none: its only request reported null counters")
	}
	c := d.Conversations["aaaaaaaaaaaa"]
	since, until := time.Date(2026, 10, 15, 13, 11, 0, 0, time.UTC), time.Date(2026, 10, 15, 16, 40, 0, 0, time.UTC)
	if c.Decision != router.Wait || !c.Since.Equal(since) || !c.Until.Equal(until) || c.Context != 100 {
		t.Fatalf("conversation %+v, want waiting since 13:11 until 16:40 with context 100", c)
	}
	if len(d.Migrations) != 1 || d.Migrations[0].From != "work" || d.Migrations[0].To != "alumni" {
		t.Fatalf("migrations %+v, want one from work to alumni", d.Migrations)
	}
}
