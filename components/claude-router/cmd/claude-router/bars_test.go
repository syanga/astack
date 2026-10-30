package main

import (
	"bytes"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

func usageWindow(kind router.WindowKind, model string, used float64, h, m, daysAgo int) claude.UsageWindow {
	return claude.UsageWindow{Kind: kind, Model: model, Used: util(used), ResetsAt: at(daysAgo, h, m)}
}

func withUsage(r report) report {
	work, alumni := &r.Status.Accounts[0], &r.Status.Accounts[1]
	work.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		usageWindow(router.FiveHour, "", headerUsed(*work, router.FiveHour), 16, 40, 0),
		usageWindow(router.Weekly, "", headerUsed(*work, router.Weekly), 9, 0, -3),
		usageWindow(router.Weekly, "Fable", 0.12, 9, 0, -3)}}
	work.Resets = claude.ResetsReading{At: at(0, 14, 0), Grants: []claude.ResetGrant{{Type: claude.ResetWeekly, Left: 2}}}
	alumni.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		usageWindow(router.Weekly, "Fable", 0.40, 6, 0, -2)}}
	return r
}

func headerUsed(a claude.AccountStatus, kind router.WindowKind) float64 {
	if w, ok := quotaWindow(a, kind); ok && w.Utilization != nil {
		return *w.Utilization
	}
	return 1
}

func onScreen(r report, width int, color bool) report {
	r.Screen = &screen{width: width, color: color}
	return r
}

func render(r report) string {
	var out bytes.Buffer
	writeStatus(&out, r, false)
	return out.String()
}

var sgr = regexp.MustCompile("\x1b\\[[0-9;]*m")

func TestStatusDrawsLimitBarsOnATerminal(t *testing.T) {
	waiting := withUsage(waitingReport())
	waiting.Status.Accounts[0].Resets.Grants = []claude.ResetGrant{{Type: claude.ResetSession, Left: 1}, {Type: claude.ResetWeekly, Left: 2}}
	for _, tc := range []struct {
		fixture string
		r       report
	}{
		{"bars-healthy", onScreen(withUsage(healthyReport()), 120, false)},
		{"bars-waiting-80", onScreen(waiting, 80, false)},
	} {
		t.Run(tc.fixture, func(t *testing.T) {
			want, err := os.ReadFile(filepath.Join("testdata", "status", tc.fixture+".txt"))
			if err != nil {
				t.Fatal(err)
			}
			if got := render(tc.r); got != string(want) {
				t.Fatalf("status output differs from %s.txt\n--- got\n%s--- want\n%s", tc.fixture, got, want)
			}
		})
	}
}

func TestColorChangesOnlyTheStyleOfTheBars(t *testing.T) {
	r := withUsage(waitingReport())
	plain := render(onScreen(r, 100, false))
	colored := render(onScreen(r, 100, true))
	if colored == plain {
		t.Fatal("colored output has no color")
	}
	if got := sgr.ReplaceAllString(colored, ""); got != plain {
		t.Fatalf("colored output without its escapes differs from the plain output\n--- colored\n%s--- plain\n%s", got, plain)
	}
}

func TestEveryBoxLineSpansTheBoxAtEveryTerminalWidth(t *testing.T) {
	r := withUsage(waitingReport())
	for width := 60; width <= 140; width++ {
		want := min(width, maxBoxWidth)
		for _, line := range strings.Split(render(onScreen(r, width, false)), "\n") {
			if strings.HasPrefix(line, "╭") || strings.HasPrefix(line, "│") || strings.HasPrefix(line, "╰") {
				if n := utf8.RuneCountInString(line); n != want {
					t.Fatalf("at width %d, a box line of %d columns, want %d: %q", width, n, want, line)
				}
			}
		}
	}
}

func TestATerminalTooNarrowForBarsGetsTheStatusLines(t *testing.T) {
	r := withUsage(healthyReport())
	plain := render(r)
	if got := render(onScreen(r, 57, true)); got != plain {
		t.Fatalf("status on a 57-column terminal\n%s\nwant the lines\n%s", got, plain)
	}
}

func TestStatusShowsTheNewerOfTheHeaderAndUsageReadings(t *testing.T) {
	r := healthyReport()
	work := &r.Status.Accounts[0]
	work.Quota.At = at(0, 13, 50)
	work.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		usageWindow(router.FiveHour, "", 0.45, 16, 40, 0), usageWindow(router.Weekly, "", 0.22, 9, 0, -3)}}

	got := render(r)

	if !strings.Contains(got, "work    ready        5h 45% (16:40)  wk 22% (Sun)") {
		t.Fatalf("status\n%s\nwant work's 14:16 usage reading, not its 13:50 header reading", got)
	}
	if strings.Contains(got, "stale") {
		t.Fatalf("status\n%s\nwant no stale line: the usage reading is 4 minutes old", got)
	}
}

func TestStatusHidesResetsFromAReadingOverAnHourOld(t *testing.T) {
	r := withUsage(healthyReport())
	work := &r.Status.Accounts[0]

	work.Resets.At = at(0, 13, 21)
	if got := render(onScreen(r, 100, false)); !strings.Contains(got, "╰─ 2 weekly resets ─") {
		t.Fatalf("status\n%s\nwant the reset count from a reading 59 minutes old", got)
	}
	work.Resets.At = at(0, 13, 19)
	if got := render(onScreen(r, 100, false)); strings.Contains(got, "reset") {
		t.Fatalf("status\n%s\nwant no reset count from a reading 61 minutes old", got)
	}
}

func TestABoxShowsAWindowWithNoReadingAsDashes(t *testing.T) {
	r := healthyReport()
	work := &r.Status.Accounts[0]
	work.Quota = router.Observation{At: at(0, 14, 15), Windows: []router.Window{
		{Kind: router.Weekly, Utilization: util(0.25)}}}

	got := render(onScreen(r, 80, false))

	for _, want := range []string{
		"│ 5-hour " + strings.Repeat(" ", 38) + "         --                     │",
		"│ weekly " + strings.Repeat("█", 28) + "▌" + strings.Repeat("╱", 9) + "   75% left                     │",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("status\n%s\nwant the line\n%s", got, want)
		}
	}
}

func TestAPlanWithoutAFiveHourWindowShowsNoFiveHourLimit(t *testing.T) {
	r := healthyReport()
	work := &r.Status.Accounts[0]
	work.Quota = router.Observation{At: at(0, 14, 15), Windows: []router.Window{window(router.Weekly, 0.25, at(-3, 9, 0))}}
	work.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		usageWindow(router.Weekly, "", 0.25, 9, 0, -3)}}
	alumni := &r.Status.Accounts[1]
	alumni.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		{Kind: router.FiveHour, Used: util(0)}, usageWindow(router.Weekly, "", 0.64, 6, 0, -2)}}

	lines := render(r)
	var verbose bytes.Buffer
	writeStatus(&verbose, r, true)
	boxes := render(onScreen(r, 80, false))

	for _, want := range []struct{ out, line string }{
		{lines, "work    ready        5h none         wk 25% (Sun)"},
		{lines, "alumni  ready        5h  0%          wk 64% (Sat)"},
		{verbose.String(), "5-hour       no limit on this plan                           seen 14:16 (4m)"},
		{boxes, "cache 93% ─╮\n│ weekly " + strings.Repeat("█", 15) + "│" + strings.Repeat("█", 12) + "▌" + strings.Repeat("╱", 9) + "   75% left  Sun 09:00 (2d 18h) │\n╰"},
	} {
		if !strings.Contains(want.out, want.line) {
			t.Fatalf("status\n%s\nwant the line\n%s", want.out, want.line)
		}
	}
	if n := strings.Count(boxes, "│ 5-hour "); n != 1 {
		t.Fatalf("status\n%s\nwant a 5-hour bar for alumni only, got %d", boxes, n)
	}
}

func TestANewerHeaderReadingShowsTheFiveHourWindowAgain(t *testing.T) {
	r := healthyReport()
	work := &r.Status.Accounts[0]
	work.Usage = claude.UsageReading{At: at(0, 14, 10), Windows: []claude.UsageWindow{
		usageWindow(router.Weekly, "", 0.21, 9, 0, -3)}}

	got := render(r)

	if !strings.Contains(got, "work    ready        5h 38% (16:40)") {
		t.Fatalf("status\n%s\nwant work's 14:15 header reading of the 5-hour window", got)
	}

	work.Quota.Windows[0] = router.Window{Kind: router.FiveHour, ResetsAt: at(0, 16, 40)}

	got = render(r)

	if !strings.Contains(got, "work    ready        5h --         ") {
		t.Fatalf("status\n%s\nwant work's 5-hour window, reported at 14:15 without a share used, as no reading", got)
	}
}

func TestAnOldModelReadingShowsTheStaleLine(t *testing.T) {
	r := healthyReport()
	work := &r.Status.Accounts[0]
	work.Usage = claude.UsageReading{At: at(0, 12, 0), Windows: []claude.UsageWindow{
		usageWindow(router.Weekly, "Fable", 0.12, 9, 0, -3)}}

	got := render(r)

	if !strings.Contains(got, "stale   work    quota seen 12:00 (2h20m ago)") {
		t.Fatalf("status\n%s\nwant a stale line for work's 12:00 Fable reading", got)
	}
}

func TestStatusCountsOnlyResetGrantsThatHaveNotEnded(t *testing.T) {
	r := withUsage(healthyReport())
	r.Status.Accounts[0].Resets.Grants = []claude.ResetGrant{
		{Type: claude.ResetWeekly, Left: 2, EndsAt: at(0, 14, 10)},
		{Type: claude.ResetSession, Left: 1, EndsAt: at(0, 15, 0)},
	}

	got := render(onScreen(r, 100, false))

	if !strings.Contains(got, "╰─ 1 session reset ─") || strings.Contains(got, "weekly reset") {
		t.Fatalf("status at 14:20\n%s\nwant the session reset that ends at 15:00 and not the weekly resets that ended at 14:10", got)
	}
}
