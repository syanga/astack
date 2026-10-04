package main

import (
	"container/heap"
	"fmt"
	"math"
	"slices"
	"strings"
	"sync"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// Metrics summarizes one simulated run.
type Metrics struct {
	CompletedTurns          int     `json:"completed_turns"`
	UsefulOutputTokens      int64   `json:"useful_output_tokens"`
	UnfinishedConversations int     `json:"unfinished_conversations"`
	Interrupted             int     `json:"interrupted_responses"`
	Cancelled               int     `json:"cancelled_responses"`
	FailedTurns             int     `json:"failed_after_retry_budget"`
	Waits                   int     `json:"waits"`
	WaitMinutes             float64 `json:"wait_minutes"`
	ReauthStalls            int     `json:"reauth_stalls"`
	Refusals                int     `json:"included_only_refusals"`
	ExhaustedAttempts       int     `json:"upstream_attempts_rejected_for_quota"`
	Placements              []Count `json:"placements"`
	ExhaustionMigrations    int     `json:"exhaustion_migrations"`
	OverageMigrations       int     `json:"overage_migrations"`
	NeverServedMigrations   int     `json:"never_served_relogin_migrations"`
	ManualMigrations        int     `json:"manual_migrations"`
	HealthyAutoMigrations   int     `json:"healthy_automatic_migrations"`
	// UnusedFiveHour and UnusedWeekly sum allowance left unused at each reset
	// inside the run, in allowance units; the Share fields divide by the
	// allowance that reset.
	UnusedFiveHour        float64 `json:"unused_five_hour_units"`
	UnusedFiveHourShare   float64 `json:"unused_five_hour_share"`
	UnusedWeekly          float64 `json:"unused_weekly_units"`
	UnusedWeeklyShare     float64 `json:"unused_weekly_share"`
	CacheReadTokens       int64   `json:"cache_read_tokens"`
	OrdinaryWriteTokens   int64   `json:"cache_write_tokens_ordinary"`
	MigrationWriteTokens  int64   `json:"cache_write_tokens_from_migration"`
	Restarts              int     `json:"restarts"`
	RecoveredBindings     int     `json:"recovered_bindings"`
	RecoveryMismatches    int     `json:"recovery_mismatches"`
	ParallelFirstGroups   int     `json:"parallel_first_groups"`
	ParallelDisagreements int     `json:"parallel_first_disagreements"`
	SubagentRequests      int     `json:"subagent_requests"`
	SubagentNotInherited  int     `json:"subagent_requests_not_inherited"`
}

// Count is one account's tally.
type Count struct {
	Account router.AccountID `json:"account"`
	N       int              `json:"n"`
}

// Event is one line of the routing trace. Plain dispatches on an existing
// assignment are counted, not traced.
type Event struct {
	At           time.Time             `json:"at"`
	Event        string                `json:"event"`
	Conversation router.ConversationID `json:"conversation,omitempty"`
	Agent        string                `json:"agent,omitempty"`
	Model        string                `json:"model,omitempty"`
	Account      router.AccountID      `json:"account,omitempty"`
	From         router.AccountID      `json:"from,omitempty"`
	Reason       router.Reason         `json:"reason,omitempty"`
	Observation  router.Freshness      `json:"observation,omitempty"`
	Until        time.Time             `json:"until,omitzero"`
	ResetKnown   *bool                 `json:"reset_known,omitempty"`
	Parallel     int                   `json:"parallel,omitempty"`
	Bindings     int                   `json:"bindings,omitempty"`
}

type window struct {
	kind      router.WindowKind
	models    []string
	allowance float64
	used      float64
	external  float64
	last      time.Time
	next      time.Time
	period    time.Duration
}

func (w *window) appliesTo(model string) bool {
	return len(w.models) == 0 || slices.ContainsFunc(w.models, func(p string) bool { return strings.HasPrefix(model, p) })
}

type quota struct {
	windows   []*window
	loggedOut bool
	hide      bool
}

type conversation struct {
	idx         int
	id          router.ConversationID
	turns       int
	turn        int
	model       string
	attempt     int
	lastFailure router.Class
	sends       int
	agents      int
	done        bool
	cacheKey    map[string]cacheState
}

type cacheState struct {
	account router.AccountID
	tokens  int
	expires time.Time
}

type evKind int

const (
	evTurn evKind = iota
	evCrash
	evAuthFault
	evRelogin
	evMove
	evPaidUse
	evPaidUseCleared
)

type event struct {
	at   time.Time
	seq  int
	kind evKind
	conv int
	arg  int
}

type queue []event

func (q queue) Len() int { return len(q) }
func (q queue) Less(i, j int) bool {
	if !q[i].at.Equal(q[j].at) {
		return q[i].at.Before(q[j].at)
	}
	return q[i].seq < q[j].seq
}
func (q queue) Swap(i, j int) { q[i], q[j] = q[j], q[i] }
func (q *queue) Push(x any)   { *q = append(*q, x.(event)) }
func (q *queue) Pop() any {
	old := *q
	e := old[len(old)-1]
	*q = old[:len(old)-1]
	return e
}

type sim struct {
	fx                     Fixture
	seed                   uint64
	cfg                    router.Config
	dir                    string
	accounts               []router.Account
	store                  *router.Store
	r                      *router.Router
	quotas                 map[router.AccountID]*quota
	convs                  []*conversation
	q                      queue
	seq                    int
	end                    time.Time
	m                      Metrics
	resetFive, resetWeekly float64
	placed                 map[router.AccountID]int
	trace                  []Event
	overageChecked         time.Time
	paidUse                map[router.AccountID]bool
}

// Simulate runs the fixture through the production router with durable
// state in dir. Every random draw is a hash of the seed and the draw's
// position in the workload, so variants see the same workload.
func Simulate(fx Fixture, seed uint64, cfg router.Config, dir string) (Metrics, []Event, error) {
	s := &sim{fx: fx, seed: seed, cfg: cfg, dir: dir, quotas: map[router.AccountID]*quota{}, placed: map[router.AccountID]int{},
		paidUse: map[router.AccountID]bool{}}
	s.end = fx.Start.Add(hours(fx.Hours))
	for _, a := range fx.Accounts {
		s.accounts = append(s.accounts, router.Account{ID: a.ID, Capacity: a.Capacity})
		s.quotas[a.ID] = s.newQuota(a)
	}
	if err := s.open(); err != nil {
		return Metrics{}, nil, err
	}
	defer func() {
		if s.store != nil {
			s.store.Close()
		}
	}()
	s.seedWorkload()
	for _, h := range fx.CrashHours {
		s.push(fx.Start.Add(hours(h)), evCrash, -1, 0)
	}
	for i, f := range fx.AuthFaults {
		s.push(fx.Start.Add(hours(f.AtHours)), evAuthFault, -1, i)
		if f.ReloginHours > 0 {
			s.push(fx.Start.Add(hours(f.ReloginHours)), evRelogin, -1, i)
		}
	}
	for i, p := range fx.PaidUse {
		s.push(fx.Start.Add(hours(p.AtHours)), evPaidUse, -1, i)
		if p.ClearedHours > 0 {
			s.push(fx.Start.Add(hours(p.ClearedHours)), evPaidUseCleared, -1, i)
		}
	}
	for s.q.Len() > 0 {
		e := heap.Pop(&s.q).(event)
		if e.at.After(s.end) {
			break
		}
		if err := s.handle(e); err != nil {
			return Metrics{}, nil, err
		}
	}
	for _, a := range s.accounts {
		s.advance(s.quotas[a.ID], s.end)
	}
	for _, c := range s.convs {
		if !c.done {
			s.m.UnfinishedConversations++
		}
	}
	for _, a := range s.accounts {
		s.m.Placements = append(s.m.Placements, Count{Account: a.ID, N: s.placed[a.ID]})
	}
	s.m.UnusedFiveHourShare = ratio(s.m.UnusedFiveHour, s.resetFive)
	s.m.UnusedWeeklyShare = ratio(s.m.UnusedWeekly, s.resetWeekly)
	s.m.WaitMinutes = math.Round(s.m.WaitMinutes*1000) / 1000
	s.m.UnusedFiveHour = math.Round(s.m.UnusedFiveHour*1000) / 1000
	s.m.UnusedWeekly = math.Round(s.m.UnusedWeekly*1000) / 1000
	return s.m, s.trace, nil
}

func ratio(a, b float64) float64 {
	if b == 0 {
		return 0
	}
	return math.Round(a/b*10000) / 10000
}

func (s *sim) open() error {
	st, err := router.OpenStore(s.dir)
	if err != nil {
		return err
	}
	r, err := router.New(s.cfg, s.accounts, st)
	if err != nil {
		st.Close()
		return err
	}
	s.store, s.r = st, r
	s.overageChecked = time.Time{}
	return nil
}

func (s *sim) checkOverage(at time.Time) {
	if !s.overageChecked.IsZero() && at.Sub(s.overageChecked) < minutes(s.fx.OverageCheckMinutes) {
		return
	}
	s.overageChecked = at
	for _, a := range s.accounts {
		state := router.OverageDisabled
		if s.paidUse[a.ID] {
			state = router.OveragePaidUse
		}
		s.r.ObserveOverage(a.ID, state, at)
	}
}

func (s *sim) newQuota(a SimAccount) *quota {
	five := s.fx.FiveHourUnits * a.Capacity
	week := s.fx.WeeklyUnits * a.Capacity
	q := &quota{hide: a.HideHeaders}
	q.windows = append(q.windows,
		&window{kind: router.FiveHour, allowance: five, used: a.FiveHourUsed * five, external: a.ExternalPerHour * five,
			last: s.fx.Start, next: s.fx.Start.Add(hours(a.FiveHourResetHours)), period: 5 * time.Hour},
		&window{kind: router.Weekly, allowance: week, used: a.WeeklyUsed * week, external: a.ExternalPerHour * five,
			last: s.fx.Start, next: s.fx.Start.Add(hours(a.WeeklyResetHours)), period: 7 * 24 * time.Hour},
	)
	for _, mw := range a.ModelWindows {
		allowance := mw.Share * week
		q.windows = append(q.windows, &window{kind: router.Weekly, models: mw.Models, allowance: allowance, used: mw.Used * allowance,
			last: s.fx.Start, next: s.fx.Start.Add(hours(mw.ResetHours)), period: 7 * 24 * time.Hour})
	}
	return q
}

func (s *sim) advance(q *quota, to time.Time) {
	for _, w := range q.windows {
		for !w.next.After(to) {
			w.used += w.external * w.next.Sub(w.last).Hours()
			if !w.next.After(s.end) && len(w.models) == 0 {
				unused := max(0, w.allowance-w.used)
				if w.kind == router.FiveHour {
					s.m.UnusedFiveHour += unused
					s.resetFive += w.allowance
				} else {
					s.m.UnusedWeekly += unused
					s.resetWeekly += w.allowance
				}
			}
			w.used, w.last = 0, w.next
			w.next = w.next.Add(w.period)
		}
		if to.After(w.last) {
			w.used += w.external * to.Sub(w.last).Hours()
			w.last = to
		}
	}
}

func (s *sim) blocked(q *quota, model string) bool {
	for _, w := range q.windows {
		if w.appliesTo(model) && w.used >= w.allowance {
			return true
		}
	}
	return false
}

func (s *sim) observation(q *quota, at time.Time) router.Observation {
	obs := router.Observation{At: at}
	for _, w := range q.windows {
		u := min(1, w.used/w.allowance)
		obs.Windows = append(obs.Windows, router.Window{
			Kind: w.kind, Models: w.models, Utilization: &u, Rejected: w.used >= w.allowance, ResetsAt: w.next,
		})
	}
	return obs
}

func (s *sim) push(at time.Time, kind evKind, conv, arg int) {
	s.seq++
	heap.Push(&s.q, event{at: at, seq: s.seq, kind: kind, conv: conv, arg: arg})
}

func (s *sim) roll(coords ...int) float64 {
	x := s.seed ^ 0x9e3779b97f4a7c15
	for _, c := range coords {
		x ^= uint64(c) + 0x9e3779b97f4a7c15 + (x << 6) + (x >> 2)
		x = splitmix(x)
	}
	return float64(x>>11) / (1 << 53)
}

func splitmix(x uint64) uint64 {
	x += 0x9e3779b97f4a7c15
	x = (x ^ (x >> 30)) * 0xbf58476d1ce4e5b9
	x = (x ^ (x >> 27)) * 0x94d049bb133111eb
	return x ^ (x >> 31)
}

const (
	drawArrival = iota + 1
	drawTurns
	drawModel
	drawGap
	drawIdle
	drawModelChange
	drawSubagents
	drawNested
	drawTransient
	drawOutcome
)

func expDraw(u, mean float64) float64 { return -mean * math.Log(1-u) }

func (s *sim) seedWorkload() {
	w := s.fx.Workload
	at := s.fx.Start
	for i := range w.Conversations {
		at = at.Add(minutes(expDraw(s.roll(drawArrival, i), w.ArrivalMeanMinutes)))
		turns := w.TurnsMin + int(s.roll(drawTurns, i)*float64(w.TurnsMax-w.TurnsMin+1))
		c := &conversation{
			idx: i, id: router.ConversationID(fmt.Sprintf("conv-%04d", i)), turns: turns,
			model: w.Models[int(s.roll(drawModel, i)*float64(len(w.Models)))], attempt: 1,
			cacheKey: map[string]cacheState{},
		}
		s.convs = append(s.convs, c)
		s.push(at, evTurn, i, 0)
	}
}

func (s *sim) handle(e event) error {
	switch e.kind {
	case evTurn:
		return s.turn(s.convs[e.conv], e.at)
	case evCrash:
		return s.crash(e.at)
	case evAuthFault:
		f := s.fx.AuthFaults[e.arg]
		s.quotas[f.Account].loggedOut = true
		s.trace = append(s.trace, Event{At: e.at, Event: "logged_out", Account: f.Account})
	case evRelogin:
		f := s.fx.AuthFaults[e.arg]
		s.quotas[f.Account].loggedOut = false
		s.r.Relogin(f.Account)
		s.trace = append(s.trace, Event{At: e.at, Event: "relogin", Account: f.Account})
	case evPaidUse:
		p := s.fx.PaidUse[e.arg]
		s.paidUse[p.Account] = true
		s.r.ObserveOverage(p.Account, router.OveragePaidUse, e.at)
		s.trace = append(s.trace, Event{At: e.at, Event: "paid_use_observed", Account: p.Account})
	case evPaidUseCleared:
		p := s.fx.PaidUse[e.arg]
		s.paidUse[p.Account] = false
		s.r.ObserveOverage(p.Account, router.OverageDisabled, e.at)
		s.trace = append(s.trace, Event{At: e.at, Event: "overflow_disabled_again", Account: p.Account})
	case evMove:
		c := s.convs[e.conv]
		to := s.fx.AuthFaults[e.arg].Account
		for _, a := range s.accounts {
			if a.ID != to && !s.quotas[a.ID].loggedOut {
				to = a.ID
				break
			}
		}
		if err := s.r.Move(e.at, c.id, to); err != nil {
			return err
		}
		s.m.ManualMigrations++
		s.trace = append(s.trace, Event{At: e.at, Event: "manual_move", Conversation: c.id, Account: to, Reason: router.ReasonManual})
		return s.turn(c, e.at)
	}
	return nil
}

func (s *sim) crash(at time.Time) error {
	before := s.store.Bindings()
	if err := s.store.Close(); err != nil {
		return err
	}
	s.store = nil
	if err := s.open(); err != nil {
		return err
	}
	after := s.store.Bindings()
	s.m.Restarts++
	s.m.RecoveredBindings += len(after)
	for conv, b := range before {
		if after[conv].Account != b.Account {
			s.m.RecoveryMismatches++
		}
	}
	if len(after) != len(before) {
		s.m.RecoveryMismatches += abs(len(after) - len(before))
	}
	s.trace = append(s.trace, Event{At: at, Event: "restart", Bindings: len(after)})
	return nil
}

func earliest(x, y time.Time) time.Time {
	if x.Before(y) {
		return x
	}
	return y
}

func abs(x int) int {
	if x < 0 {
		return -x
	}
	return x
}

func (s *sim) request(c *conversation) router.Request {
	return router.Request{Conversation: c.id, Model: c.model, Attempt: c.attempt, LastFailure: c.lastFailure}
}

func (s *sim) turn(c *conversation, at time.Time) error {
	s.checkOverage(at)
	req := s.request(c)
	var d router.Decision
	var err error
	_, bound := s.r.Lookup(c.id)
	if !bound && s.fx.Workload.ParallelFirst > 1 {
		d, err = s.parallelFirst(at, req)
	} else {
		d, err = s.r.Route(at, req)
	}
	if err != nil {
		return err
	}
	switch d.Kind {
	case router.Place:
		s.placed[d.Account]++
		s.trace = append(s.trace, Event{At: at, Event: "place", Conversation: c.id, Model: c.model, Account: d.Account,
			Reason: d.Reason, Observation: d.Observation, Parallel: max(0, s.fx.Workload.ParallelFirst)})
		return s.attempt(c, at, d.Account)
	case router.Migrate:
		q := s.quotas[d.From]
		s.advance(q, at)
		justified := false
		switch d.Reason {
		case router.ReasonExhausted:
			justified = s.blocked(q, c.model)
			s.m.ExhaustionMigrations++
		case router.ReasonOverageObserved:
			justified = s.paidUse[d.From]
			s.m.OverageMigrations++
		case router.ReasonNeverServed:
			justified = q.loggedOut
			s.m.NeverServedMigrations++
		}
		if !justified {
			s.m.HealthyAutoMigrations++
		}
		s.trace = append(s.trace, Event{At: at, Event: "migrate", Conversation: c.id, Model: c.model, From: d.From,
			Account: d.Account, Reason: d.Reason, Observation: d.Observation})
		return s.attempt(c, at, d.Account)
	case router.Dispatch:
		return s.attempt(c, at, d.Account)
	case router.Retry:
		s.trace = append(s.trace, Event{At: at, Event: "retry", Conversation: c.id, Account: d.Account, Reason: d.Reason})
		return s.attempt(c, at, d.Account)
	case router.Wait:
		known := d.ResetKnown
		s.m.Waits++
		s.m.WaitMinutes += earliest(d.Until, s.end).Sub(at).Minutes()
		s.trace = append(s.trace, Event{At: at, Event: "wait", Conversation: c.id, Model: c.model, Until: d.Until, ResetKnown: &known, Reason: d.Reason})
		s.resume(c, d.Until)
	case router.Reauth:
		s.m.ReauthStalls++
		s.trace = append(s.trace, Event{At: at, Event: "reauth", Conversation: c.id, Account: d.Account, Reason: d.Reason})
		return s.stall(c, at, d.Account)
	case router.Fail:
		s.m.FailedTurns++
		s.trace = append(s.trace, Event{At: at, Event: "fail", Conversation: c.id, Account: d.Account, Reason: d.Reason})
		s.resume(c, at.Add(time.Minute))
	case router.Refuse:
		s.m.Refusals++
		s.trace = append(s.trace, Event{At: at, Event: "refuse", Conversation: c.id, Account: d.Account, Reason: d.Reason})
		s.resume(c, at.Add(minutes(s.fx.OverageCheckMinutes)))
	case router.Unavailable:
		s.trace = append(s.trace, Event{At: at, Event: "unavailable", Conversation: c.id, Reason: d.Reason})
		s.resume(c, at.Add(10*time.Minute))
	default:
		return fmt.Errorf("unexpected decision %+v", d)
	}
	return nil
}

func (s *sim) resume(c *conversation, at time.Time) {
	c.attempt, c.lastFailure = 1, router.ClassNone
	s.push(at, evTurn, c.idx, 0)
}

func (s *sim) stall(c *conversation, at time.Time, account router.AccountID) error {
	for i, f := range s.fx.AuthFaults {
		if f.Account != account {
			continue
		}
		c.attempt, c.lastFailure = 1, router.ClassNone
		switch {
		case f.MoveAfterMinutes > 0:
			s.push(at.Add(minutes(f.MoveAfterMinutes)), evMove, c.idx, i)
		case f.ReloginHours > 0 && s.fx.Start.Add(hours(f.ReloginHours)).After(at):
			s.push(s.fx.Start.Add(hours(f.ReloginHours)).Add(time.Second), evTurn, c.idx, 0)
		}
		return nil
	}
	return nil
}

func (s *sim) parallelFirst(at time.Time, req router.Request) (router.Decision, error) {
	n := s.fx.Workload.ParallelFirst
	out := make([]router.Decision, n)
	errs := make([]error, n)
	var wg sync.WaitGroup
	start := make(chan struct{})
	for i := range n {
		wg.Add(1)
		go func() {
			defer wg.Done()
			<-start
			r := req
			if i > 0 {
				r.Agent = fmt.Sprintf("parallel-%d", i)
			}
			out[i], errs[i] = s.r.Route(at, r)
		}()
	}
	close(start)
	wg.Wait()
	var chosen router.Decision
	for i, d := range out {
		if errs[i] != nil {
			return router.Decision{}, errs[i]
		}
		if d.Account != out[0].Account {
			s.m.ParallelDisagreements++
		}
		if d.Kind == router.Place || chosen.Kind == "" {
			chosen = d
		}
	}
	if chosen.Kind == router.Place {
		s.m.ParallelFirstGroups++
	}
	return chosen, nil
}

func (s *sim) attempt(c *conversation, at time.Time, account router.AccountID) error {
	q := s.quotas[account]
	s.advance(q, at)
	if q.loggedOut {
		class := s.r.Report(router.Failure{Account: account, Model: c.model, Class: router.ClassAuth, AttemptStart: at})
		c.attempt, c.lastFailure = c.attempt+1, class
		return s.turn(c, at)
	}
	if s.blocked(q, c.model) {
		s.m.ExhaustedAttempts++
		class := s.r.Report(router.Failure{Account: account, Model: c.model, Class: router.ClassExhausted,
			AttemptStart: at, Observation: s.observation(q, at)})
		c.attempt, c.lastFailure = c.attempt+1, class
		return s.turn(c, at)
	}
	w := s.fx.Workload
	c.sends++
	if s.roll(drawTransient, c.idx, c.turn, c.sends) < w.TransientRate {
		c.attempt, c.lastFailure = c.attempt+1, router.ClassTransient
		return s.turn(c, at)
	}
	ctx := w.InitialContext + w.GrowthPerTurn*c.turn
	outcome := s.roll(drawOutcome, c.idx, c.turn, c.sends)
	output := w.OutputPerTurn
	switch {
	case outcome < w.PartialRate:
		output /= 2
	case outcome < w.PartialRate+w.CancelRate:
		output /= 4
	}
	s.spend(q, c, "main", at, account, ctx, output)
	switch {
	case outcome < w.PartialRate:
		s.m.Interrupted++
		s.trace = append(s.trace, Event{At: at, Event: "interrupted", Conversation: c.id, Account: account})
		s.resume(c, at.Add(time.Minute))
		return nil
	case outcome < w.PartialRate+w.CancelRate:
		s.m.Cancelled++
		s.trace = append(s.trace, Event{At: at, Event: "cancelled", Conversation: c.id, Account: account})
	default:
		s.m.CompletedTurns++
		s.m.UsefulOutputTokens += int64(output)
		if err := s.r.Served(at, c.id, account); err != nil {
			return err
		}
		if err := s.subagents(c, at, account); err != nil {
			return err
		}
	}
	return s.next(c, at)
}

func (s *sim) spend(q *quota, c *conversation, key string, at time.Time, account router.AccountID, ctx, output int) {
	cm := s.fx.Cache
	prev := c.cacheKey[key]
	read, write := 0, ctx
	warm := !at.After(prev.expires)
	if warm && prev.account == account {
		read, write = min(prev.tokens, ctx), ctx-min(prev.tokens, ctx)
		s.m.OrdinaryWriteTokens += int64(write)
	} else if warm && prev.account != "" {
		s.m.MigrationWriteTokens += int64(min(prev.tokens, ctx))
		s.m.OrdinaryWriteTokens += int64(ctx - min(prev.tokens, ctx))
	} else {
		s.m.OrdinaryWriteTokens += int64(ctx)
	}
	s.m.CacheReadTokens += int64(read)
	c.cacheKey[key] = cacheState{account: account, tokens: ctx, expires: at.Add(minutes(cm.TTLMinutes))}
	units := (float64(write)*cm.WriteWeight + float64(read)*cm.ReadWeight + float64(output)*cm.OutputWeight) / 1000
	for _, w := range q.windows {
		if w.appliesTo(c.model) {
			w.used += units
		}
	}
	if !q.hide {
		s.r.Observe(account, s.observation(q, at))
	}
}

func (s *sim) subagents(c *conversation, at time.Time, account router.AccountID) error {
	w := s.fx.Workload
	if s.roll(drawSubagents, c.idx, c.turn) >= w.SubagentProbability {
		return nil
	}
	parent := ""
	for depth := range 2 {
		if depth == 1 && s.roll(drawNested, c.idx, c.turn) >= w.NestedProbability {
			break
		}
		c.agents++
		agent := fmt.Sprintf("agent-%d", c.agents)
		req := s.request(c)
		req.Agent, req.ParentAgent, req.Attempt, req.LastFailure = agent, parent, 1, router.ClassNone
		d, err := s.r.Route(at, req)
		if err != nil {
			return err
		}
		s.m.SubagentRequests++
		bnd, _ := s.r.Lookup(c.id)
		if d.Kind == router.Place || bnd.Account != account && d.Kind != router.Migrate {
			s.m.SubagentNotInherited++
		}
		if d.Kind == router.Dispatch {
			q := s.quotas[d.Account]
			if !q.loggedOut && !s.blocked(q, c.model) {
				s.spend(q, c, agent, at, d.Account, w.SubagentContext, w.OutputPerTurn/2)
			}
		}
		parent = agent
	}
	return nil
}

func (s *sim) next(c *conversation, at time.Time) error {
	w := s.fx.Workload
	c.turn++
	c.attempt, c.lastFailure, c.sends = 1, router.ClassNone, 0
	if c.turn >= c.turns {
		c.done = true
		return nil
	}
	if s.roll(drawModelChange, c.idx, c.turn) < w.ModelChange {
		c.model = w.Models[(slices.Index(w.Models, c.model)+1)%len(w.Models)]
	}
	gap := expDraw(s.roll(drawGap, c.idx, c.turn), w.GapMeanMinutes)
	if s.roll(drawIdle, c.idx, c.turn) < w.IdleProbability {
		gap += w.IdleMinutes
	}
	s.push(at.Add(minutes(gap)), evTurn, c.idx, 0)
	return nil
}
