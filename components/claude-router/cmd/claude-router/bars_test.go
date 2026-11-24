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

func weeklyOnlyReport() report {
	r := healthyReport()
	work, alumni := &r.Status.Accounts[0], &r.Status.Accounts[1]
	work.Capacity = 3
	work.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		usageWindow(router.Weekly, "", 0.25, 2, 20, -4)}}
	alumni.Usage = claude.UsageReading{At: at(0, 14, 16), Windows: []claude.UsageWindow{
		usageWindow(router.Weekly, "", 0.75, 8, 20, -2)}}
	return r
}

var weeklyOnlyPool = "pool    wk " + strings.Repeat("█", 35) + "│" + strings.Repeat("█", 14) + strings.Repeat("╱", 30) + "  63%"

func TestThePoolLineWeighsEachAccountByItsCapacity(t *testing.T) {
	got := render(onScreen(weeklyOnlyReport(), 96, false))

	if lines := strings.Split(got, "\n"); lines[1] != weeklyOnlyPool {
		t.Fatalf("status\n%s\nwant under the router line an 80-column bar with 50 columns left, (3×0.75 + 1×0.25) / 4 = 62.5%%, "+
			"and its tick in the 36th, (3×84h/168h + 1×42h/168h) / 4 = 43.75%%\n%s", got, weeklyOnlyPool)
	}
}

func TestAnAccountWithoutAReadingStaysOutOfThePool(t *testing.T) {
	r := weeklyOnlyReport()
	side := disabledAt(at(0, 14, 16))
	side.ID, side.Capacity = "side", 5
	side.Quota = router.Observation{At: at(0, 14, 15), Windows: []router.Window{{Kind: router.Weekly}}}
	r.Status.Accounts = append(r.Status.Accounts, side)

	got := render(onScreen(r, 96, false))

	if lines := strings.Split(got, "\n"); lines[1] != weeklyOnlyPool {
		t.Fatalf("status\n%s\nwant the pool of work and alumni alone\n%s", got, weeklyOnlyPool)
	}
}

func TestAnUnusedFiveHourWindowCountsAsOnPaceInThePool(t *testing.T) {
	r := weeklyOnlyReport()
	work, alumni := &r.Status.Accounts[0], &r.Status.Accounts[1]
	work.Usage.Windows = append(work.Usage.Windows, claude.UsageWindow{Kind: router.FiveHour, Used: util(0)})
	alumni.Usage.Windows = append(alumni.Usage.Windows, usageWindow(router.FiveHour, "", 0.5, 16, 50, 0))
	want := "pool    5h " + strings.Repeat("█", 29) + "▊│╱╱╱  88%   wk "

	got := render(onScreen(r, 96, false))

	if lines := strings.Split(got, "\n"); !strings.HasPrefix(lines[1], want) {
		t.Fatalf("status\n%s\nwant a 34-column 5-hour bar with 29.75 columns left, (3×1 + 1×0.5) / 4 = 87.5%%, "+
			"and its tick at the same share, (3×1 + 1×2.5h/5h) / 4, because work's unused window has not started\n%s", got, want)
	}
}

func TestAModelNamedLikeAnAccountWideWindowGetsItsOwnPoolBar(t *testing.T) {
	r := weeklyOnlyReport()
	alumni := &r.Status.Accounts[1]
	alumni.Usage.Windows = append(alumni.Usage.Windows, usageWindow(router.Weekly, "wk", 0.5, 8, 20, -2))
	want := "pool    wk " + strings.Repeat("█", 15) + "│" + strings.Repeat("█", 5) + "▎" + strings.Repeat("╱", 12) + "  63%" +
		"   wk " + strings.Repeat("█", 9) + "│" + strings.Repeat("█", 7) + strings.Repeat("╱", 17) + "  50%"

	got := render(onScreen(r, 96, false))

	if lines := strings.Split(got, "\n"); lines[1] != want {
		t.Fatalf("status\n%s\nwant two 34-column bars: the weekly window of work and alumni, 62.5%% left with its tick at 43.75%%, "+
			"then alumni's model wk, 50%% left with its tick at 42h/168h = 25%%\n%s", got, want)
	}
}

func TestAReadingFromBeforeItsWindowResetCountsAsOnPaceInThePool(t *testing.T) {
	r := weeklyOnlyReport()
	r.Status.Accounts[0].Usage.Windows = []claude.UsageWindow{usageWindow(router.Weekly, "", 0.25, 14, 18, 0)}
	want := "pool    wk " + strings.Repeat("█", 50) + "│" + strings.Repeat("╱", 29) + "  63%"

	got := render(onScreen(r, 96, false))

	if lines := strings.Split(got, "\n"); lines[1] != want {
		t.Fatalf("status at 14:20\n%s\nwant an 80-column bar with 50 columns left, (3×0.75 + 1×0.25) / 4 = 62.5%%, "+
			"and its tick in the 51st, (3×0.75 + 1×42h/168h) / 4 = 62.5%%, because work's 14:16 reading names a reset at 14:18\n%s", got, want)
	}
}

func TestEachPoolBarNeedsTenColumns(t *testing.T) {
	r := withUsage(healthyReport())

	if got := render(onScreen(r, 71, false)); utf8.RuneCountInString(strings.Split(got, "\n")[1]) != 71 {
		t.Fatalf("status at 71 columns\n%s\nwant a pool line of 41 fixed columns and three 10-column bars", got)
	}
	if got := render(onScreen(r, 70, false)); strings.Contains(got, "pool") || !strings.Contains(got, "╭─ work") {
		t.Fatalf("status at 70 columns\n%s\nwant the boxes and no pool line: three bars would get (70 - 41) / 3 = 9 columns", got)
	}
}

func TestThePoolLineAppearsOnlyWithTheBoxes(t *testing.T) {
	r := weeklyOnlyReport()

	if got := render(onScreen(r, 52, false)); !strings.Contains(got, "\npool    wk ") || !strings.Contains(got, "╭─ work") {
		t.Fatalf("status at 52 columns\n%s\nwant the boxes, whose bars get 52 - 42 = 10 columns, under a pool line", got)
	}
	if got := render(onScreen(r, 51, false)); got != render(r) {
		t.Fatalf("status at 51 columns\n%s\nwant the lines and no pool line: the boxes' bars would get 9 columns, "+
			"although the pool bar would get 35\n%s", got, render(r))
	}
}

func TestASingleAccountGetsNoPoolLine(t *testing.T) {
	r := withUsage(healthyReport())
	if got := render(onScreen(r, 100, false)); !strings.Contains(got, "\npool    5h ") {
		t.Fatalf("status of two accounts\n%s\nwant a pool line", got)
	}

	r.Status.Accounts = r.Status.Accounts[:1]

	if got := render(onScreen(r, 100, false)); strings.Contains(got, "pool") {
		t.Fatalf("status of one account\n%s\nwant no pool line", got)
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
