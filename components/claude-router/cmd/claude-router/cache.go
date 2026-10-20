package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

type cacheView struct {
	Now      time.Time
	Window   time.Duration
	Accounts []router.AccountID
	Cache    cacheWindow
}

func cacheCommand(args []string) error {
	fs := flag.NewFlagSet("cache", flag.ExitOnError)
	state := stateFlag(fs)
	window := fs.Duration("since", statusCacheWindow, "how far back to count")
	_ = fs.Parse(args)
	if *window <= 0 {
		return errors.New("-since must be positive")
	}
	if err := checkState(*state); err != nil {
		return err
	}
	v, err := cacheReport(*state, time.Now(), *window)
	if err != nil {
		return err
	}
	writeCache(os.Stdout, v)
	return nil
}

func cacheReport(state string, now time.Time, window time.Duration) (cacheView, error) {
	cfg, err := claude.LoadConfig(filepath.Join(state, "config.json"))
	if err != nil {
		return cacheView{}, err
	}
	d, err := readDigest(filepath.Join(state, "events.jsonl"), now.Add(-window))
	if err != nil {
		return cacheView{}, err
	}
	v := cacheView{Now: now, Window: window, Cache: d.Cache}
	enrolled := map[router.AccountID]bool{}
	for _, a := range cfg.Accounts {
		v.Accounts = append(v.Accounts, a.ID)
		enrolled[a.ID] = true
	}
	var others []router.AccountID
	for id := range d.Cache.Accounts {
		if !enrolled[id] {
			others = append(others, id)
		}
	}
	sort.Slice(others, func(i, j int) bool { return others[i] < others[j] })
	v.Accounts = append(v.Accounts, others...)
	return v, nil
}

func writeCache(w io.Writer, v cacheView) {
	label := 10
	for _, id := range v.Accounts {
		label = max(label, len(id)+2)
	}
	fmt.Fprintf(w, "%-*slast %s, %s to now\n", label, "window", windowLength(v.Window), sinceLabel(v.Now, v.Now.Add(-v.Window)))

	type row struct {
		hit, input, write, read string
		unknown                 int
	}
	rows := make([]row, len(v.Accounts))
	var hitW, inputW, writeW, readW int
	for i, id := range v.Accounts {
		u := v.Cache.Accounts[id]
		if u == nil {
			u = &usageTotals{}
		}
		r := row{hit: "--", input: tokens(u.Input), write: tokens(u.Write), read: tokens(u.Read), unknown: u.Unknown}
		if s, ok := u.readShare(); ok {
			r.hit = fmt.Sprintf("%d%%", s)
		}
		rows[i] = r
		hitW, inputW = max(hitW, len(r.hit)), max(inputW, len(r.input))
		writeW, readW = max(writeW, len(r.write)), max(readW, len(r.read))
	}
	for i, id := range v.Accounts {
		r := rows[i]
		line := fmt.Sprintf("%-*shit %*s  input %*s  write %*s  read %*s", label, id, hitW, r.hit, inputW, r.input, writeW, r.write, readW, r.read)
		if r.unknown > 0 {
			line += fmt.Sprintf("  + %d unknown", r.unknown)
		}
		fmt.Fprintln(w, line)
	}

	cost, unknown := v.Cache.moveCost()
	writes := v.Cache.writes()
	costText, shareText := tokens(cost), share(cost, writes)
	if unknown > 0 {
		costText += fmt.Sprintf(" + %d unknown", unknown)
		if writes > 0 {
			shareText += " or more"
		}
	}
	fmt.Fprintf(w, "%-*s%d  cost %s  of %s writes  share %s\n", label, "moves", len(v.Cache.Moves), costText, tokens(writes), shareText)
	if len(v.Cache.Moves) > 0 {
		fmt.Fprintf(w, "%-*s%s\n", label, "reasons", reasonCounts(v.Cache.Moves))
	}
}

func reasonCounts(moves []accountMove) string {
	counts := map[string]int{}
	for _, m := range moves {
		counts[m.Reason]++
	}
	reasons := make([]string, 0, len(counts))
	for r := range counts {
		reasons = append(reasons, r)
	}
	sort.Slice(reasons, func(i, j int) bool {
		if counts[reasons[i]] != counts[reasons[j]] {
			return counts[reasons[i]] > counts[reasons[j]]
		}
		return reasons[i] < reasons[j]
	})
	parts := make([]string, len(reasons))
	for i, r := range reasons {
		parts[i] = fmt.Sprintf("%s %d", r, counts[r])
	}
	return strings.Join(parts, ", ")
}

func sinceLabel(now, t time.Time) string {
	t = t.In(now.Location())
	y, m, d := t.Date()
	switch {
	case sameDay(now, y, m, d):
		return t.Format("15:04 MST")
	case sameDay(now.AddDate(0, 0, -1), y, m, d):
		return t.Format("15:04 MST") + " yesterday"
	}
	return t.Format("Jan 02 15:04 MST")
}

func sameDay(t time.Time, y int, m time.Month, d int) bool {
	ty, tm, td := t.Date()
	return ty == y && tm == m && td == d
}
