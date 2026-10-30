package main

import (
	"fmt"
	"os"
	"strings"
	"syscall"
	"time"
	"unicode/utf8"
	"unsafe"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

type screen struct {
	width int
	color bool
}

func terminalScreen(f *os.File, getenv func(string) string) *screen {
	if getenv("TERM") == "dumb" {
		return nil
	}
	var size struct{ rows, cols, x, y uint16 }
	_, _, errno := syscall.Syscall(syscall.SYS_IOCTL, f.Fd(), uintptr(syscall.TIOCGWINSZ), uintptr(unsafe.Pointer(&size)))
	if errno != 0 || size.cols == 0 {
		return nil
	}
	return &screen{width: int(size.cols), color: getenv("NO_COLOR") == ""}
}

const (
	maxBoxWidth    = 100
	minBarWidth    = 10
	resetsShownFor = time.Hour
)

// SGR parameters, in the 256-color palette that macOS Terminal also draws.
const (
	sgrLeft      = "38;5;173"
	sgrUsed      = "38;5;239"
	sgrOut       = "38;5;88"
	sgrPace      = "1;97"
	sgrPaceInBar = "38;5;234;48;5;173"
	sgrBorder    = "38;5;240"
	sgrDim       = "38;5;245"
	sgrBold      = "1"
	sgrRed       = "38;5;167"
	sgrGreen     = "38;5;108"
	sgrAmber     = "38;5;179"
)

var eighths = []string{"", "▏", "▎", "▍", "▌", "▋", "▊", "▉", "█"}

type cells struct {
	color bool
	b     strings.Builder
	style string
	run   strings.Builder
	n     int
}

func (c *cells) put(style, s string) {
	if !c.color {
		style = ""
	}
	if style != c.style {
		c.flush()
		c.style = style
	}
	c.run.WriteString(s)
	c.n += utf8.RuneCountInString(s)
}

func (c *cells) flush() {
	if c.run.Len() == 0 {
		return
	}
	if c.style == "" {
		c.b.WriteString(c.run.String())
	} else {
		fmt.Fprintf(&c.b, "\x1b[%sm%s\x1b[0m", c.style, c.run.String())
	}
	c.run.Reset()
}

func (c *cells) padTo(n int) {
	if n > c.n {
		c.put("", strings.Repeat(" ", n-c.n))
	}
}

func (c *cells) String() string {
	c.flush()
	return c.b.String()
}

type limitRow struct {
	label    string
	left     *float64
	out      bool
	resetsAt time.Time
	period   time.Duration
}

var limitWindows = []struct {
	kind  router.WindowKind
	label string
}{{router.FiveHour, "5-hour"}, {router.Weekly, "weekly"}}

func modelLabel(w claude.UsageWindow) string { return "weekly " + w.Model }

func leftOf(used float64) *float64 {
	left := min(max(1-used, 0), 1)
	return &left
}

func limitRows(r report, a claude.AccountStatus) []limitRow {
	var rows []limitRow
	for _, win := range limitWindows {
		row := limitRow{label: win.label, period: win.kind.Period()}
		if rj, ok := activeRejection(r.Now, a, win.kind); ok {
			row.out, row.resetsAt = true, rejectionEnd(rj)
		} else if w, ok := shownWindow(a, win.kind); ok && w.none {
			continue
		} else if ok {
			row.left, row.resetsAt = leftOf(w.used), w.resetsAt
		}
		rows = append(rows, row)
	}
	for _, w := range modelWindows(a) {
		row := limitRow{label: modelLabel(w), resetsAt: w.ResetsAt, period: router.Weekly.Period()}
		if w.Used != nil {
			row.left = leftOf(*w.Used)
		}
		rows = append(rows, row)
	}
	return rows
}

func modelWindows(a claude.AccountStatus) []claude.UsageWindow {
	var out []claude.UsageWindow
	for _, w := range a.Usage.Windows {
		if w.Model != "" {
			out = append(out, w)
		}
	}
	return out
}

func resetCounts(r report, a claude.AccountStatus) []string {
	if a.Resets.At.IsZero() || r.Now.Sub(a.Resets.At) > resetsShownFor {
		return nil
	}
	left := map[claude.ResetKind]int{}
	for _, g := range a.Resets.Grants {
		if g.OpenAt(r.Now) {
			left[g.Type] += g.Left
		}
	}
	var out []string
	for _, kind := range claude.ResetKinds {
		switch n := left[kind]; {
		case n == 1:
			out = append(out, fmt.Sprintf("1 %s reset", kind))
		case n > 1:
			out = append(out, fmt.Sprintf("%d %s resets", n, kind))
		}
	}
	return out
}

func countdown(d time.Duration) string {
	d = max(d, 0).Round(time.Minute)
	switch {
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh%02dm", int(d.Hours()), int(d.Minutes())%60)
	}
	return fmt.Sprintf("%dd %dh", int(d.Hours())/24, int(d.Hours())%24)
}

func resetClock(now, t time.Time) string {
	t = t.In(now.Location())
	if t.Sub(now) < 24*time.Hour {
		return t.Format("15:04")
	}
	return t.Format("Mon 15:04")
}

const (
	percentWidth = len("100% left")
	resetWidth   = len("Sun 09:00 (6d 23h)")
)

func accountBoxes(r report) ([]string, bool) {
	if r.Screen == nil {
		return nil, false
	}
	width := min(r.Screen.width, maxBoxWidth)
	labelWidth := len("weekly")
	for _, a := range r.Status.Accounts {
		for _, row := range limitRows(r, a) {
			labelWidth = max(labelWidth, utf8.RuneCountInString(row.label))
		}
	}
	labelWidth++
	bar := width - 4 - labelWidth - 2 - percentWidth - 2 - resetWidth
	if bar < minBarWidth {
		return nil, false
	}
	var out []string
	for _, a := range r.Status.Accounts {
		box, ok := accountBox(r, a, width, labelWidth, bar)
		if !ok {
			return nil, false
		}
		out = append(out, box)
	}
	return out, true
}

func accountBox(r report, a claude.AccountStatus, width, labelWidth, barWidth int) (string, bool) {
	color := r.Screen.color
	var lines []string

	top := cells{color: color}
	top.put(sgrBorder, "╭─ ")
	top.put(sgrBold, string(a.ID))
	top.put("", "  ")
	state := accountState(r, a)
	top.put(stateStyle(state), state)
	top.put("", " ")
	summary := " " + accountSummary(r, a) + " "
	rule := width - top.n - utf8.RuneCountInString(summary) - 2
	if rule < 1 {
		summary = ""
		rule = width - top.n - 2
	}
	if rule < 1 {
		return "", false
	}
	top.put(sgrBorder, strings.Repeat("─", rule))
	top.put(sgrDim, summary)
	top.put(sgrBorder, "─╮")
	lines = append(lines, top.String())

	for _, row := range limitRows(r, a) {
		c := cells{color: color}
		c.put(sgrBorder, "│ ")
		c.put(sgrDim, fmt.Sprintf("%-*s", labelWidth, row.label))
		drawBar(&c, r.Now, row, barWidth)
		c.put("", "  ")
		switch {
		case row.out:
			c.put(sgrRed+";1", fmt.Sprintf("%*s", percentWidth, "out"))
		case row.left == nil:
			c.put(sgrDim, fmt.Sprintf("%*s", percentWidth, "--"))
		default:
			c.put(sgrBold, fmt.Sprintf("%3d%% left", percent(*row.left)))
		}
		if !row.resetsAt.IsZero() {
			c.put("", "  ")
			c.put(sgrDim, fmt.Sprintf("%s (%s)", resetClock(r.Now, row.resetsAt), countdown(row.resetsAt.Sub(r.Now))))
		}
		c.padTo(width - 2)
		c.put(sgrBorder, " │")
		lines = append(lines, c.String())
	}

	bottom := cells{color: color}
	if counts := resetCounts(r, a); len(counts) > 0 {
		bottom.put(sgrBorder, "╰─ ")
		for i, s := range counts {
			if i > 0 {
				bottom.put(sgrDim, " · ")
			}
			bottom.put(sgrLeft, s)
		}
		bottom.put("", " ")
	} else {
		bottom.put(sgrBorder, "╰")
	}
	if bottom.n > width-2 {
		return "", false
	}
	bottom.put(sgrBorder, strings.Repeat("─", width-1-bottom.n)+"╯")
	lines = append(lines, bottom.String())
	return strings.Join(lines, "\n") + "\n", true
}

func stateStyle(state string) string {
	switch state {
	case "ready":
		return sgrGreen
	case "needs login", "exhausted", "paid use":
		return sgrRed
	}
	return sgrAmber
}

func drawBar(c *cells, now time.Time, row limitRow, n int) {
	if row.out {
		c.put(sgrOut, strings.Repeat("╱", n))
		return
	}
	if row.left == nil {
		c.put("", strings.Repeat(" ", n))
		return
	}
	units := int(*row.left*float64(n*8) + 0.5)
	full, part := units/8, units%8
	evenPaceCell := -1
	if row.period > 0 && row.resetsAt.After(now) {
		leftAtEvenPace := min(float64(row.resetsAt.Sub(now))/float64(row.period), 1)
		evenPaceCell = min(int(leftAtEvenPace*float64(n)+0.5), n-1)
	}
	for i := range n {
		inBar := i < full || (i == full && part > 0)
		switch {
		case i == evenPaceCell && inBar:
			c.put(sgrPaceInBar, "│")
		case i == evenPaceCell:
			c.put(sgrPace, "│")
		case i < full:
			c.put(sgrLeft, "█")
		case i == full && part > 0:
			c.put(sgrLeft, eighths[part])
		default:
			c.put(sgrUsed, "╱")
		}
	}
}
