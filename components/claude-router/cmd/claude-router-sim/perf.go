package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"math"
	"sort"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// PerfResult reports the perf workload. Latencies are microseconds.
// Decide latency excludes the durable commit, which is reported separately.
type PerfResult struct {
	Accounts              int                `json:"accounts"`
	Bindings              int                `json:"bindings"`
	Requests              int                `json:"requests"`
	Served                int                `json:"served"`
	NotServed             map[string]int     `json:"not_served"`
	Placements            int                `json:"placements"`
	ExhaustionMigrations  int                `json:"exhaustion_migrations"`
	HealthyAutoMigrations int                `json:"healthy_automatic_migrations"`
	DecideMicros          Percentiles        `json:"decide_us"`
	CommitMicros          Percentiles        `json:"commit_us"`
	PlaceElapsedSeconds   float64            `json:"place_phase_seconds"`
	RequestElapsedSeconds float64            `json:"request_phase_seconds"`
	ExhaustedAccounts     []router.AccountID `json:"exhausted_accounts"`
}

// Percentiles summarizes a latency sample.
type Percentiles struct {
	N   int     `json:"n"`
	P50 float64 `json:"p50"`
	P95 float64 `json:"p95"`
	P99 float64 `json:"p99"`
	Max float64 `json:"max"`
}

func percentiles(d []time.Duration) Percentiles {
	if len(d) == 0 {
		return Percentiles{}
	}
	s := append([]time.Duration(nil), d...)
	sort.Slice(s, func(i, j int) bool { return s[i] < s[j] })
	q := func(p float64) float64 {
		i := int(math.Ceil(p*float64(len(s)))) - 1
		return float64(s[max(0, i)].Nanoseconds()) / 1000
	}
	return Percentiles{N: len(s), P50: q(0.50), P95: q(0.95), P99: q(0.99), Max: q(1)}
}

func perfAccounts(n int) []router.Account {
	caps := []float64{1, 5, 20, 1, 5}
	out := make([]router.Account, n)
	for i := range out {
		out[i] = router.Account{ID: router.AccountID(fmt.Sprintf("acct-%03d", i)), Capacity: caps[i%len(caps)]}
	}
	return out
}

func cmdPerf(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("perf", flag.ContinueOnError)
	state := fs.String("state", "", "durable state directory, which must be empty")
	nAccounts := fs.Int("accounts", 100, "enrolled accounts")
	nBindings := fs.Int("bindings", 10000, "conversations to place")
	nRequests := fs.Int("requests", 10000, "requests on placed conversations")
	if err := fs.Parse(args); err != nil {
		return err
	}
	if *state == "" {
		return errors.New("perf needs -state")
	}
	accounts := perfAccounts(*nAccounts)
	st, err := router.OpenStore(*state)
	if err != nil {
		return err
	}
	defer st.Close()
	if len(st.Bindings()) != 0 {
		return errors.New("perf needs an empty state directory")
	}
	r, err := router.New(router.DefaultConfig(), accounts, st)
	if err != nil {
		return err
	}
	res := PerfResult{Accounts: len(accounts), Bindings: *nBindings, Requests: *nRequests, NotServed: map[string]int{}}
	now := serveStart
	observe := func(now time.Time) {
		for i, a := range accounts {
			r.ObserveOverage(a.ID, router.OverageDisabled, now)
			used := float64(i%10) / 10
			obs := router.Observation{At: now, Windows: []router.Window{
				{Kind: router.FiveHour, Utilization: &used, ResetsAt: now.Add(time.Duration(i%5+1) * time.Hour)},
				{Kind: router.Weekly, Utilization: &used, ResetsAt: now.Add(time.Duration(i%7+1) * 24 * time.Hour)},
			}}
			switch i % 10 {
			case 7:
				obs.At = now.Add(-2 * time.Hour)
			case 8:
				continue
			case 9:
				obs.Windows[0].Rejected = true
			}
			r.Observe(a.ID, obs)
		}
	}
	exhausted := map[router.AccountID]bool{}
	for i, a := range accounts {
		if i%10 == 9 {
			exhausted[a.ID] = true
		}
	}
	observe(now)
	var decide, commit []time.Duration
	serve := func(now time.Time, req router.Request) error {
		t := time.Now()
		d := r.Decide(now, req)
		decide = append(decide, time.Since(t))
		switch d.Kind {
		case router.Place, router.Migrate:
			t = time.Now()
			var got router.AccountID
			var err error
			if d.Kind == router.Place {
				got, err = r.CommitAssignment(now, req.Conversation, d.Account, d.Reason)
				res.Placements++
			} else {
				got, err = r.CommitMigration(now, req.Conversation, d.From, d.Account, d.Reason)
				res.ExhaustionMigrations++
				if !exhausted[d.From] {
					res.HealthyAutoMigrations++
				}
			}
			commit = append(commit, time.Since(t))
			if err != nil {
				return err
			}
			if got != d.Account {
				return fmt.Errorf("%s committed to %s, decided %s", req.Conversation, got, d.Account)
			}
			res.Served++
		case router.Dispatch:
			res.Served++
		default:
			res.NotServed[string(d.Kind)+":"+string(d.Reason)]++
		}
		return nil
	}
	begin := time.Now()
	step := time.Hour / time.Duration(max(1, *nBindings))
	for i := range *nBindings {
		now = serveStart.Add(time.Duration(i) * step)
		if err := serve(now, router.Request{Conversation: router.ConversationID(fmt.Sprintf("conv-%05d", i)), Model: "claude-sonnet-4-5", Attempt: 1}); err != nil {
			return err
		}
	}
	res.PlaceElapsedSeconds = time.Since(begin).Seconds()
	now = serveStart.Add(time.Hour)
	observe(now)
	for i := 0; i < 5; i++ {
		id := accounts[i*10].ID
		res.ExhaustedAccounts = append(res.ExhaustedAccounts, id)
		exhausted[id] = true
		r.Report(router.Failure{Account: id, Model: "claude-sonnet-4-5", Class: router.ClassExhausted, AttemptStart: now,
			Observation: router.Observation{At: now, Windows: []router.Window{{Kind: router.FiveHour, Rejected: true, ResetsAt: now.Add(3 * time.Hour)}}}})
	}
	begin = time.Now()
	for j := range *nRequests {
		conv := router.ConversationID(fmt.Sprintf("conv-%05d", int(splitmix(uint64(j))%uint64(*nBindings))))
		if err := serve(now.Add(time.Duration(j)*time.Millisecond), router.Request{Conversation: conv, Model: "claude-sonnet-4-5", Attempt: 1}); err != nil {
			return err
		}
	}
	res.RequestElapsedSeconds = time.Since(begin).Seconds()
	res.DecideMicros = percentiles(decide)
	res.CommitMicros = percentiles(commit)
	return writeJSON(stdout, res)
}

func cmdRecover(args []string, stdout io.Writer) error {
	fs := flag.NewFlagSet("recover", flag.ContinueOnError)
	state := fs.String("state", "", "durable state directory")
	nAccounts := fs.Int("accounts", 100, "enrolled accounts")
	if err := fs.Parse(args); err != nil {
		return err
	}
	t := time.Now()
	st, err := router.OpenStore(*state)
	if err != nil {
		return err
	}
	defer st.Close()
	if _, err := router.New(router.DefaultConfig(), perfAccounts(*nAccounts), st); err != nil {
		return err
	}
	open := time.Since(t)
	return writeJSON(stdout, map[string]any{"bindings": len(st.Bindings()), "open_ms": float64(open.Microseconds()) / 1000})
}
