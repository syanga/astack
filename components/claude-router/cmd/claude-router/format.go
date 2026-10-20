package main

import (
	"fmt"
	"io"
	"slices"
	"sort"
	"strconv"
	"strings"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

const staleAfter = 15 * time.Minute

const recentMove = 15 * time.Minute

type report struct {
	Now             time.Time
	Manager         string
	Listen          string
	StateShown      string
	Running         bool
	Status          claude.Status
	Sup             supervision
	Digest          digest
	ConfigErr       string
	SaveErr         string
	MoveWarnPercent int
	Screen          *screen
}

const statusCacheWindow = 24 * time.Hour

func writeStatus(w io.Writer, r report, verbose bool) {
	r.Now = r.Now.Round(0)
	clock := r.Now.Format("15:04 MST")
	warning := moveCacheWarning(r)
	switch {
	case !r.Running && r.SaveErr != "":
		fmt.Fprintf(w, "%-69s%s\n", fmt.Sprintf("router  stopped  %s  %s", r.Listen, r.Manager), clock)
		fmt.Fprintf(w, "        last error: %s\n", r.SaveErr)
		fmt.Fprintln(w, "next    fix the error, then claude-router start")
		return
	case !r.Running && !r.Sup.GaveUpAt.IsZero():
		head := fmt.Sprintf("router  failed  %d starts in %s, gave up %s  %s", crashLimit, shortDuration(crashWindow), when(r.Now, r.Sup.GaveUpAt), r.Manager)
		fmt.Fprintf(w, "%-69s%s\n", head, clock)
		if r.Sup.LastError != "" {
			fmt.Fprintf(w, "        last error: %s\n", r.Sup.LastError)
		}
		fmt.Fprintln(w, "next    fix the error, then claude-router start")
		return
	case r.ConfigErr != "":
		state, next := "stopped", "fix the configuration, then claude-router start"
		if r.Running {
			state, next = "running", "fix the configuration; the running router keeps the one it started with"
		}
		fmt.Fprintf(w, "%-69s%s\n", fmt.Sprintf("router  %s  %s", state, r.Manager), clock)
		fmt.Fprintf(w, "        config error: %s\n", r.ConfigErr)
		fmt.Fprintf(w, "next    %s\n", next)
		return
	case !r.Running:
		fmt.Fprintf(w, "%-69s%s\n", fmt.Sprintf("router  stopped  %s  %s", r.Listen, r.Manager), clock)
		if r.Sup.LastError != "" && r.Sup.LastErrorAt.After(lastStart(r.Sup)) {
			fmt.Fprintf(w, "        last error: %s\n", r.Sup.LastError)
		}
		if warning != "" && !verbose {
			fmt.Fprintln(w, warning)
		}
		fmt.Fprintln(w, "next    claude-router start")
		return
	}
	st := r.Status
	journal := "ok"
	if st.Journal != "ok" {
		journal = "failed"
	}
	head := fmt.Sprintf("router  running %s  %s  %s  journal %s", shortDuration(r.Now.Sub(st.Started)), st.Listen, r.Manager, journal)
	fmt.Fprintf(w, "%-69s%s\n", head, clock)
	if verbose {
		restarts := max(r.Sup.Runs-1, 0)
		fmt.Fprintf(w, "        pid %d, %d restarts, binary %s, sdk %s, %s\n", st.PID, restarts, st.Binary, st.SDK, r.StateShown)
	}
	if st.Journal != "ok" {
		fmt.Fprintf(w, "        journal error: %s\n", st.Journal)
	}
	width := 8
	for _, a := range st.Accounts {
		width = max(width, len(a.ID)+2)
	}
	if verbose {
		for _, a := range st.Accounts {
			fmt.Fprintln(w)
			writeAccountDetail(w, r, a, width)
		}
		if len(st.Bindings) > 0 {
			fmt.Fprintln(w)
			writeConversations(w, r)
		}
		if len(r.Digest.Migrations) > 0 {
			fmt.Fprintln(w)
			writeMigrations(w, r)
		}
		if next := nextStep(r, false); next != "" {
			fmt.Fprintln(w)
			fmt.Fprintf(w, "next    %s\n", next)
		}
		return
	}
	var tail []string
	for _, a := range st.Accounts {
		if s := staleNote(r, a); s != "" {
			tail = append(tail, fmt.Sprintf("stale   %-*s%s", width, a.ID, s))
		}
	}
	tail = append(tail, problemLines(r)...)
	if warning != "" {
		tail = append(tail, warning)
	}
	if next := nextStep(r, warning != ""); next != "" {
		tail = append(tail, "next    "+next)
	}
	if boxes, ok := accountBoxes(r); ok {
		for _, line := range boxes {
			fmt.Fprintln(w)
			fmt.Fprint(w, line)
		}
		if len(tail) > 0 {
			fmt.Fprintln(w)
		}
	} else {
		for _, a := range st.Accounts {
			writeAccountLine(w, r, a, width)
		}
	}
	for _, line := range tail {
		fmt.Fprintln(w, line)
	}
}

func lastStart(s supervision) time.Time {
	if len(s.Unclean) == 0 {
		return time.Time{}
	}
	return s.Unclean[len(s.Unclean)-1]
}

func accountState(r report, a claude.AccountStatus) string {
	switch {
	case a.NeedsLogin:
		return "needs login"
	case exhausted(r.Now, a):
		return "exhausted"
	case a.Overage.State == router.OveragePaidUse:
		return "paid use"
	case a.Overage.State == router.OverageEnabled:
		return "overflow on"
	case a.Overage.State == "":
		return "not checked"
	case r.Now.Sub(a.Overage.At) > overageFreshFor(r):
		return "check stale"
	}
	return "ready"
}

func overageFreshFor(r report) time.Duration {
	if r.Status.OverageFreshFor > 0 {
		return time.Duration(r.Status.OverageFreshFor)
	}
	return 30 * time.Minute
}

func rejectionEnd(rj router.Rejection) time.Time {
	if rj.ResetsAt.IsZero() {
		return rj.At.Add(router.DefaultConfig().UnknownResetRecheck)
	}
	return rj.ResetsAt
}

func activeRejection(now time.Time, a claude.AccountStatus, kind router.WindowKind) (router.Rejection, bool) {
	for _, rj := range a.Rejections {
		if rj.Kind == kind && rj.Rejected && rejectionEnd(rj).After(now) {
			return rj, true
		}
	}
	return router.Rejection{}, false
}

func exhausted(now time.Time, a claude.AccountStatus) bool {
	for _, rj := range a.Rejections {
		if rj.Rejected && len(rj.Models) == 0 && rejectionEnd(rj).After(now) {
			return true
		}
	}
	return false
}

func quotaWindow(a claude.AccountStatus, kind router.WindowKind) (router.Window, bool) {
	for _, w := range a.Quota.Windows {
		if w.Kind == kind && len(w.Models) == 0 {
			return w, true
		}
	}
	return router.Window{}, false
}

type shownReading struct {
	used     float64
	resetsAt time.Time
	seen     time.Time
}

func shownWindow(a claude.AccountStatus, kind router.WindowKind) (shownReading, bool) {
	var shown shownReading
	found := false
	if w, ok := quotaWindow(a, kind); ok && w.Utilization != nil {
		shown, found = shownReading{used: *w.Utilization, resetsAt: w.ResetsAt, seen: a.Quota.At}, true
	}
	for _, w := range a.Usage.Windows {
		if w.Kind == kind && w.Model == "" && w.Used != nil && (!found || a.Usage.At.After(shown.seen)) {
			shown, found = shownReading{used: *w.Used, resetsAt: w.ResetsAt, seen: a.Usage.At}, true
		}
	}
	return shown, found
}

func oldestShownAt(a claude.AccountStatus) time.Time {
	var oldest time.Time
	for _, kind := range []router.WindowKind{router.FiveHour, router.Weekly} {
		if w, ok := shownWindow(a, kind); ok && (oldest.IsZero() || w.seen.Before(oldest)) {
			oldest = w.seen
		}
	}
	if oldest.IsZero() {
		return a.Quota.At
	}
	return oldest
}

func compactWindow(r report, a claude.AccountStatus, kind router.WindowKind, label string, width int) string {
	var s string
	if rj, ok := activeRejection(r.Now, a, kind); ok {
		s = fmt.Sprintf("%s out (%s)", label, resetShort(r.Now, rejectionEnd(rj)))
	} else if w, ok := shownWindow(a, kind); ok {
		s = fmt.Sprintf("%s %2d%%", label, percent(w.used))
		if !w.resetsAt.IsZero() {
			s += " (" + resetShort(r.Now, w.resetsAt) + ")"
		}
	} else {
		s = label + " --"
	}
	return fmt.Sprintf("%-*s", width, s)
}

func writeAccountLine(w io.Writer, r report, a claude.AccountStatus, width int) {
	fmt.Fprintf(w, "%-*s%-13s%s  %s  %s\n", width, a.ID, accountState(r, a),
		compactWindow(r, a, router.FiveHour, "5h", 14), compactWindow(r, a, router.Weekly, "wk", 12), accountSummary(r, a))
}

func accountSummary(r report, a claude.AccountStatus) string {
	cache := "cache --"
	if u := r.Digest.Accounts[a.ID]; u != nil {
		if share, ok := u.readShare(); ok {
			cache = fmt.Sprintf("cache %d%%", share)
		}
	}
	return fmt.Sprintf("cap %s  %d/%d active  %s", number(a.Capacity), a.Active, a.Bound, cache)
}

func staleNote(r report, a claude.AccountStatus) string {
	var parts []string
	if seen := oldestShownAt(a); !seen.IsZero() && r.Now.Sub(seen) > staleAfter {
		parts = append(parts, fmt.Sprintf("quota seen %s (%s ago)", when(r.Now, seen), shortDuration(r.Now.Sub(seen))))
	}
	if !a.Overage.At.IsZero() && r.Now.Sub(a.Overage.At) > staleAfter {
		s := fmt.Sprintf("overflow read %s (%s ago)", when(r.Now, a.Overage.At), shortDuration(r.Now.Sub(a.Overage.At)))
		if a.Overage.Failure != "" {
			s += ", last read failed: " + a.Overage.Failure
		}
		parts = append(parts, s)
	}
	return strings.Join(parts, ", ")
}

var refuseWords = map[string]string{
	string(router.ReasonOverageEnabled): "paid overflow on",
	string(router.ReasonOverageUnknown): "paid overflow not checked",
	string(router.ReasonOverageStale):   "paid overflow check stale",
	string(router.ReasonPaidUse):        "paid use seen",
	string(router.ReasonNoVerified):     "no account verified included-only",
}

var moveWords = map[string]string{
	string(router.ReasonNeverServed):     "before its first reply",
	string(router.ReasonExhausted):       "after exhaustion",
	string(router.ReasonManual):          "manual",
	string(router.ReasonOverageObserved): "after paid overflow was seen",
}

func words(table map[string]string, code string) string {
	if s, ok := table[code]; ok {
		return s
	}
	return code
}

func unreachable(c *conversationDigest) bool {
	return c.Decision == router.Fail && c.Reason == string(router.ReasonUnreachable)
}

func problemLines(r report) []string {
	var lines, moves []string
	flagged := map[string]bool{}
	activeFor := time.Duration(r.Status.ActiveFor)
	for _, b := range orderedBindings(r) {
		c := r.Digest.Conversations[b.Conversation]
		if c == nil {
			continue
		}
		prefix := fmt.Sprintf("%s  %-8s", b.Conversation, b.Account)
		switch {
		case c.Decision == router.Wait && (c.Until.IsZero() && r.Now.Sub(c.Last) < activeFor || c.Until.After(r.Now)):
			resend := "resends when an account can serve"
			if !c.Until.IsZero() {
				resend = "resends at " + when(r.Now, c.Until)
			}
			lines = append(lines, fmt.Sprintf("wait    %ssince %s, %s", prefix, when(r.Now, c.Since), resend))
		case c.Decision == router.Refuse && r.Now.Sub(c.Last) < activeFor:
			lines = append(lines, fmt.Sprintf("refuse  %ssince %s, %s", prefix, when(r.Now, c.Since), words(refuseWords, c.Reason)))
		case c.Decision == router.Reauth && r.Now.Sub(c.Last) < activeFor:
			lines = append(lines, fmt.Sprintf("held    %ssince %s, needs login", prefix, when(r.Now, c.Since)))
		case unreachable(c) && r.Now.Sub(c.Last) < activeFor:
			lines = append(lines, fmt.Sprintf("failed  %ssince %s, provider unreachable", prefix, when(r.Now, c.Since)))
		default:
			continue
		}
		flagged[b.Conversation] = true
	}
	for _, m := range r.Digest.Migrations {
		if flagged[m.Conversation] || r.Now.Sub(m.At) > recentMove {
			continue
		}
		moves = append(moves, fmt.Sprintf("moved   %s  %s to %s %s, %s", m.Conversation, m.From, m.To, when(r.Now, m.At), words(moveWords, m.Reason)))
	}
	return append(lines, moves...)
}

func moveCacheWarning(r report) string {
	cost, unknown := r.Digest.Cache.moveCost()
	writes := r.Digest.Cache.writes()
	limit := int64(r.MoveWarnPercent)
	if limit == 0 || writes == 0 || cost*100 <= limit*writes {
		return ""
	}
	moved := share(cost, writes)
	if unknown > 0 {
		moved += " or more"
	}
	return fmt.Sprintf("cache   account moves wrote %s of all cache writes in %s, over %d%%",
		moved, windowLength(statusCacheWindow), limit)
}

func share(part, whole int64) string {
	if whole == 0 {
		return "--"
	}
	return fmt.Sprintf("%.1f%%", float64(part)*100/float64(whole))
}

func windowLength(d time.Duration) string {
	if d%time.Hour == 0 {
		return fmt.Sprintf("%dh", int(d.Hours()))
	}
	return shortDuration(d)
}

func nextStep(r report, cacheWarned bool) string {
	if step := problemStep(r); step != "" || !cacheWarned {
		return step
	}
	return "claude-router cache"
}

func problemStep(r report) string {
	st := r.Status
	if st.Journal != "ok" {
		return "claude-router stop; claude-router start"
	}
	for _, a := range st.Accounts {
		if a.NeedsLogin {
			return fmt.Sprintf("claude-router stop; claude-router login -account %s; start", a.ID)
		}
	}
	var earliest time.Time
	var earliestOn router.AccountID
	waiting := false
	for _, b := range st.Bindings {
		if c := r.Digest.Conversations[b.Conversation]; c != nil && c.Decision == router.Wait && c.Until.After(r.Now) {
			waiting = true
		}
	}
	if !waiting {
		return ""
	}
	var overflow []string
	for _, a := range st.Accounts {
		if a.NeedsLogin {
			continue
		}
		if exhausted(r.Now, a) {
			for _, rj := range a.Rejections {
				if end := rejectionEnd(rj); rj.Rejected && len(rj.Models) == 0 && end.After(r.Now) && (earliest.IsZero() || end.Before(earliest)) {
					earliest, earliestOn = end, a.ID
				}
			}
			continue
		}
		if a.Overage.State == router.OverageEnabled {
			overflow = append(overflow, fmt.Sprintf("or now on %s once its paid overflow is off", a.ID))
		}
	}
	parts := []string{}
	if !earliest.IsZero() {
		parts = append(parts, fmt.Sprintf("%s on %s", when(r.Now, earliest), earliestOn))
	}
	parts = append(parts, overflow...)
	return strings.Join(parts, ", ")
}

func writeAccountDetail(w io.Writer, r report, a claude.AccountStatus, width int) {
	pad := strings.Repeat(" ", width)
	fmt.Fprintf(w, "%-*s%-13scapacity %s, %d of %d conversations active\n", width, a.ID, accountState(r, a), number(a.Capacity), a.Active, a.Bound)
	fmt.Fprintf(w, "%s%-13s%s\n", pad, "overflow", overflowDetail(r, a))
	if a.NeedsLogin {
		fmt.Fprintf(w, "%s%-13ssince %s\n", pad, "login", when(r.Now, a.NeedsLoginAt))
	}
	reading := func(label, value string, seen time.Time) {
		if seen.IsZero() {
			fmt.Fprintf(w, "%s%-13s%s\n", pad, label, value)
			return
		}
		age := shortDuration(r.Now.Sub(seen))
		if r.Now.Sub(seen) > staleAfter {
			age += ", stale"
		}
		fmt.Fprintf(w, "%s%-13s%-48sseen %s (%s)\n", pad, label, value, when(r.Now, seen), age)
	}
	used := func(u float64, resetsAt time.Time) string {
		value := fmt.Sprintf("%d%% used", percent(u))
		if !resetsAt.IsZero() {
			value += ", resets " + resetLong(r.Now, resetsAt)
		}
		return value
	}
	for _, win := range []struct {
		kind  router.WindowKind
		label string
	}{{router.FiveHour, "5-hour"}, {router.Weekly, "weekly"}} {
		value, seen := "no reading yet", time.Time{}
		if rj, ok := activeRejection(r.Now, a, win.kind); ok {
			value, seen = fmt.Sprintf("rejected at %s, resets %s", when(r.Now, rj.At), resetLong(r.Now, rejectionEnd(rj))), rj.At
			if rj.ResetsAt.IsZero() {
				value = fmt.Sprintf("rejected at %s, reset unknown, recheck %s", when(r.Now, rj.At), when(r.Now, rejectionEnd(rj)))
			}
		} else if q, ok := shownWindow(a, win.kind); ok {
			value, seen = used(q.used, q.resetsAt), q.seen
		}
		reading(win.label, value, seen)
	}
	for _, m := range modelWindows(a) {
		if m.Used != nil {
			reading("weekly "+m.Model, used(*m.Used, m.ResetsAt), a.Usage.At)
		}
	}
	if counts := resetCounts(r, a); len(counts) > 0 {
		fmt.Fprintf(w, "%s%-13s%s\n", pad, "resets", strings.Join(counts, ", "))
	}
	if u := r.Digest.Accounts[a.ID]; u != nil {
		share := "--"
		if s, ok := u.readShare(); ok {
			share = strconv.Itoa(s) + "%"
		}
		fmt.Fprintf(w, "%s%-13s%d requests, input %s, write %s, read %s, output %s, read %s\n", pad, "cache",
			u.Requests, tokens(u.Input), tokens(u.Write), tokens(u.Read), tokens(u.Output), share)
	} else {
		fmt.Fprintf(w, "%s%-13sno request reported usage\n", pad, "cache")
	}
}

func overflowDetail(r report, a claude.AccountStatus) string {
	o := a.Overage
	source := "settings read"
	if o.Source == "response" {
		source = "response"
	}
	var s string
	switch o.State {
	case router.OverageDisabled:
		s = fmt.Sprintf("off, %s %s", source, when(r.Now, o.At))
		if r.Now.Sub(o.At) > overageFreshFor(r) {
			s += "; too old to dispatch on until the next read"
		}
	case router.OverageEnabled:
		s = fmt.Sprintf("ON, %s %s; not used until a read shows off", source, when(r.Now, o.At))
	case router.OveragePaidUse:
		s = fmt.Sprintf("paid use seen %s; not used until a read shows off", when(r.Now, o.At))
	default:
		s = "not read yet; not used until a read shows off"
	}
	if o.Failure != "" {
		s += "; last read failed: " + o.Failure
	}
	return s
}

func writeConversations(w io.Writer, r report) {
	fmt.Fprintf(w, "%-14s%-9s%-28s%-13s%-23s%7s\n", "conversation", "account", "bound", "last", "state", "context")
	for _, b := range orderedBindings(r) {
		c := r.Digest.Conversations[b.Conversation]
		bound := when(r.Now, b.Since) + " " + string(b.Reason)
		if from := movedFrom(r.Digest, b); from != "" {
			bound += ", from " + from
		}
		last, state, context := "--", "idle", "--"
		if b.Active {
			state = "active"
		}
		lastAt := b.LastRequest
		if c != nil {
			if c.Last.After(lastAt) {
				lastAt = c.Last
			}
			if c.Context > 0 {
				context = tokens(c.Context)
			}
			switch {
			case c.Decision == router.Wait && c.Until.After(r.Now):
				state = "wait until " + when(r.Now, c.Until)
			case c.Decision == router.Refuse && b.Active:
				state = "refuse, " + c.Reason
			case c.Decision == router.Reauth && b.Active:
				state = "held, needs login"
			case unreachable(c) && b.Active:
				state = "provider unreachable"
			}
		}
		if !lastAt.IsZero() {
			last = when(r.Now, lastAt)
		}
		fmt.Fprintf(w, "%-14s%-9s%-28s%-13s%-23s%7s\n", b.Conversation, b.Account, bound, last, state, context)
	}
}

func orderedBindings(r report) []claude.BindingStatus {
	rank := func(b claude.BindingStatus) int {
		c := r.Digest.Conversations[b.Conversation]
		switch {
		case c != nil && c.Decision == router.Wait && c.Until.After(r.Now):
			return 0
		case c != nil && c.Decision == router.Refuse && b.Active:
			return 1
		case c != nil && c.Decision == router.Reauth && b.Active:
			return 2
		case b.Active:
			return 3
		}
		return 4
	}
	last := func(b claude.BindingStatus) time.Time {
		if c := r.Digest.Conversations[b.Conversation]; c != nil && c.Last.After(b.LastRequest) {
			return c.Last
		}
		return b.LastRequest
	}
	out := slices.Clone(r.Status.Bindings)
	sort.SliceStable(out, func(i, j int) bool {
		if ri, rj := rank(out[i]), rank(out[j]); ri != rj {
			return ri < rj
		}
		return last(out[i]).After(last(out[j]))
	})
	return out
}

func movedFrom(d digest, b claude.BindingStatus) string {
	for i := len(d.Migrations) - 1; i >= 0; i-- {
		m := d.Migrations[i]
		if m.Conversation == b.Conversation && m.To == string(b.Account) && m.Reason == string(b.Reason) {
			return m.From
		}
	}
	return ""
}

const migrationRows = 20

func writeMigrations(w io.Writer, r report) {
	ms := slices.Clone(r.Digest.Migrations)
	sort.SliceStable(ms, func(i, j int) bool { return ms[i].At.After(ms[j].At) })
	fmt.Fprintf(w, "%-14s%-14s%-9s%-9s%s\n", "migration", "conversation", "from", "to", "reason")
	for i, m := range ms {
		if i == migrationRows {
			fmt.Fprintf(w, "        and %d earlier\n", len(ms)-migrationRows)
			break
		}
		fmt.Fprintf(w, "%-14s%-14s%-9s%-9s%s\n", when(r.Now, m.At), m.Conversation, m.From, m.To, m.Reason)
	}
}

func when(now, t time.Time) string {
	t = t.In(now.Location())
	y1, m1, d1 := now.Date()
	y2, m2, d2 := t.Date()
	switch {
	case y1 == y2 && m1 == m2 && d1 == d2:
		return t.Format("15:04")
	case t.Sub(now).Abs() < 6*24*time.Hour:
		return t.Format("Mon 15:04")
	}
	return t.Format("Jan 02 15:04")
}

func resetShort(now, t time.Time) string {
	t = t.In(now.Location())
	if t.Sub(now) < 24*time.Hour {
		return t.Format("15:04")
	}
	if t.Sub(now) < 6*24*time.Hour {
		return t.Format("Mon")
	}
	return t.Format("Jan 02")
}

func resetLong(now, t time.Time) string {
	t = t.In(now.Location())
	if t.Sub(now) < 24*time.Hour {
		return t.Format("15:04")
	}
	return when(now, t)
}

func shortDuration(d time.Duration) string {
	d = d.Round(time.Minute)
	switch {
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh%dm", int(d.Hours()), int(d.Minutes())%60)
	}
	return fmt.Sprintf("%dd%dh", int(d.Hours())/24, int(d.Hours())%24)
}

func percent(u float64) int { return int(u*100 + 0.5) }

func number(f float64) string { return strconv.FormatFloat(f, 'f', -1, 64) }

func tokens(n int64) string {
	switch {
	case n < 1000:
		return strconv.FormatInt(n, 10)
	case n < 1_000_000:
		return fmt.Sprintf("%dk", (n+500)/1000)
	}
	return fmt.Sprintf("%.2fM", float64(n)/1e6)
}
