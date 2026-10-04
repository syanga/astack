package router

import (
	"errors"
	"fmt"
	"math"
	"slices"
	"strings"
	"sync"
	"time"
)

// ErrUnsettled reports that Route could not commit a decision because the
// conversation's binding kept changing underneath it.
var ErrUnsettled = errors.New("routing decision did not settle")

const maxRouteAttempts = 3

// Router is the production routing boundary. Decide is the policy, and Commit
// makes a Place or Migrate outcome durable before dispatch. Route composes
// them for one request. Every method takes the current time so the simulator
// can drive the same code with a deterministic clock.
//
// After any journal write or sync failure, every method that answers with an
// account or changes routing state returns ErrFailed until the store is
// reopened.
type Router struct {
	cfg   Config
	store *Store

	// commitMu serializes commits with Served, Relogin, and Move, so a
	// commit re-decides against their effects.
	commitMu sync.Mutex

	mu       sync.RWMutex
	accounts []accountView
	index    map[AccountID]int
	active   map[ConversationID]time.Time
}

// New returns a router over the enrolled accounts, in tie-break order, and an
// open store. Account login and quota state start unknown.
func New(cfg Config, accounts []Account, store *Store) (*Router, error) {
	if err := cfg.Validate(); err != nil {
		return nil, err
	}
	if len(accounts) == 0 {
		return nil, errors.New("no enrolled accounts")
	}
	r := &Router{cfg: cfg, store: store, index: map[AccountID]int{}, active: map[ConversationID]time.Time{}}
	for i, a := range accounts {
		if a.ID == "" || !(a.Capacity > 0) || math.IsInf(a.Capacity, 0) {
			return nil, fmt.Errorf("account %d: need an ID and a finite positive capacity", i)
		}
		if _, dup := r.index[a.ID]; dup {
			return nil, fmt.Errorf("account %s enrolled twice", a.ID)
		}
		r.index[a.ID] = i
		r.accounts = append(r.accounts, accountView{Account: a})
	}
	return r, nil
}

// Config returns the policy parameters.
func (r *Router) Config() Config { return r.cfg }

// Decide returns the policy outcome for a request without changing state.
func (r *Router) Decide(now time.Time, req Request) (Decision, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	b, bound, err := r.store.Lookup(req.Conversation)
	if err != nil {
		return Decision{}, err
	}
	var loadErr error
	v := view{
		accounts: r.accounts,
		index:    r.index,
		binding:  b,
		bound:    bound,
		loads: func() []int {
			l, err := r.loads(now, req.Conversation)
			loadErr = err
			return l
		},
	}
	d := decide(r.cfg, now, v, req)
	if loadErr != nil {
		return Decision{}, loadErr
	}
	return d, nil
}

func (r *Router) loads(now time.Time, skip ConversationID) ([]int, error) {
	loads := make([]int, len(r.accounts))
	horizon := now.Add(-r.cfg.ActiveFor)
	err := r.store.each(func(conv ConversationID, b Binding) {
		if conv == skip {
			return
		}
		last, ok := r.active[conv]
		if !ok || b.Since.After(last) {
			last = b.Since
		}
		if idx, enrolled := r.index[b.Account]; enrolled && !last.Before(horizon) {
			loads[idx]++
		}
	})
	return loads, err
}

// Commit makes a Place or Migrate decision durable. It decides again under
// the commit lock and commits that fresh decision, so a Served or Relogin
// call that landed after d was made is honored. It returns the fresh
// decision, which may differ from d. Any other kind is returned unchanged.
func (r *Router) Commit(now time.Time, req Request, d Decision) (Decision, error) {
	if d.Kind != Place && d.Kind != Migrate {
		return d, nil
	}
	r.commitMu.Lock()
	defer r.commitMu.Unlock()
	fresh, err := r.Decide(now, req)
	if err != nil {
		return Decision{}, err
	}
	var b Binding
	switch fresh.Kind {
	case Place:
		b, err = r.store.Assign(req.Conversation, fresh.Account, fresh.Reason, now)
	case Migrate:
		b, err = r.store.Migrate(req.Conversation, fresh.From, fresh.Account, fresh.Reason, now)
	default:
		return fresh, nil
	}
	if err != nil {
		return Decision{}, err
	}
	if b.Account != fresh.Account {
		return Decision{}, ErrUnsettled
	}
	r.touch(req.Conversation, now)
	return fresh, nil
}

// CommitAssignment durably records a first assignment without consulting the
// policy, and returns the account the conversation is bound to. It is for
// setup and tests; Route uses Commit.
func (r *Router) CommitAssignment(now time.Time, conv ConversationID, account AccountID, reason Reason) (AccountID, error) {
	r.commitMu.Lock()
	defer r.commitMu.Unlock()
	b, err := r.store.Assign(conv, account, reason, now)
	if err != nil {
		return "", err
	}
	r.touch(conv, now)
	return b.Account, nil
}

// Route decides a request and commits a placement or migration before
// returning it. The returned account is the one to pin the request to.
func (r *Router) Route(now time.Time, req Request) (Decision, error) {
	for range maxRouteAttempts {
		d, err := r.Decide(now, req)
		if err != nil {
			return Decision{}, err
		}
		switch d.Kind {
		case Place, Migrate:
			d, err = r.Commit(now, req, d)
			if errors.Is(err, ErrUnsettled) {
				continue
			}
			if err != nil {
				return Decision{}, err
			}
		}
		if d.Kind == Dispatch || d.Kind == Retry {
			r.touch(req.Conversation, now)
		}
		return d, nil
	}
	return Decision{}, ErrUnsettled
}

// Move is the manual override: it binds an assigned conversation to an
// enrolled account, whatever its quota or login state.
func (r *Router) Move(now time.Time, conv ConversationID, to AccountID) error {
	r.mu.RLock()
	_, enrolled := r.index[to]
	r.mu.RUnlock()
	if !enrolled {
		return fmt.Errorf("account %s is not enrolled", to)
	}
	r.commitMu.Lock()
	defer r.commitMu.Unlock()
	b, ok, err := r.store.Lookup(conv)
	if err != nil {
		return err
	}
	if !ok {
		return ErrUnassigned
	}
	if b.Account == to {
		return nil
	}
	b, err = r.store.Migrate(conv, b.Account, to, ReasonManual, now)
	if err != nil {
		return err
	}
	if b.Account != to {
		return ErrUnsettled
	}
	r.touch(conv, now)
	return nil
}

// Served durably marks the conversation's binding on account as having
// served a successful response. Until then, a relogin requirement on the
// account lets Decide place the conversation again.
func (r *Router) Served(now time.Time, conv ConversationID, account AccountID) error {
	r.commitMu.Lock()
	defer r.commitMu.Unlock()
	_, err := r.store.MarkServed(conv, account, now)
	return err
}

// Lookup returns a conversation's durable binding.
func (r *Router) Lookup(conv ConversationID) (Binding, bool, error) { return r.store.Lookup(conv) }

func fromAttempt(attemptStart time.Time, obs Observation) bool {
	return !attemptStart.IsZero() && !obs.At.IsZero() && !obs.At.Before(attemptStart)
}

// Observe records the quota headers of one response to an attempt that
// started at attemptStart. Evidence observed before the attempt started is
// ignored, as in Report. A rejected window is remembered until its reset, or
// until a newer observation reports the same window not rejected; a newer
// observation that omits it does not clear it. Utilization comes from the
// newest observation.
func (r *Router) Observe(account AccountID, attemptStart time.Time, obs Observation) {
	if !fromAttempt(attemptStart, obs) {
		return
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	r.observe(account, obs)
}

func windowKey(w Window) string {
	return string(w.Kind) + "|" + strings.Join(w.Models, ",")
}

func (r *Router) observe(account AccountID, obs Observation) {
	idx, ok := r.index[account]
	if !ok || obs.At.Before(r.accounts[idx].observation.At) {
		return
	}
	a := &r.accounts[idx]
	a.observation = obs
	for _, w := range obs.Windows {
		key := windowKey(w)
		a.rejections = slices.DeleteFunc(a.rejections, func(rj rejection) bool {
			return windowKey(rj.Window) == key && rj.at.Before(obs.At)
		})
		if w.Rejected {
			a.rejections = append(a.rejections, rejection{Window: w, at: obs.At})
		}
	}
	a.rejections = slices.DeleteFunc(a.rejections, func(rj rejection) bool {
		return !rj.end(r.cfg).After(obs.At)
	})
}

// Report records a failure before output and returns the class the policy
// acts on. Exhaustion and model limits are confirmed only by this attempt's
// own evidence: an observation taken at or after AttemptStart with a
// rejected window that applies to the model. Without it, or for an account
// that is not enrolled, the failure is generic throttling, because the SDK
// keeps an older snapshot when a response carries no quota headers. An auth
// failure excludes the account from new placement until Relogin.
func (r *Router) Report(f Failure) Class {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx, ok := r.index[f.Account]
	switch f.Class {
	case ClassAuth:
		if ok {
			r.accounts[idx].needsLogin = true
		}
	case ClassExhausted, ClassModelLimit:
		confirmed := ok && fromAttempt(f.AttemptStart, f.Observation) &&
			slices.ContainsFunc(f.Observation.Windows, func(w Window) bool { return w.Rejected && w.appliesTo(f.Model) })
		if !confirmed {
			return ClassThrottle
		}
		r.observe(f.Account, f.Observation)
	}
	return f.Class
}

// ObserveOverage records an account's paid-overflow state as checked at at.
// The latest check wins, except that paid use is cleared only by a check
// strictly after the paid-use observation.
func (r *Router) ObserveOverage(account AccountID, state Overage, at time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx, ok := r.index[account]
	if !ok {
		return
	}
	a := &r.accounts[idx]
	if at.Before(a.overageAt) || a.overage == OveragePaidUse && state != OveragePaidUse && !at.After(a.overageAt) {
		return
	}
	a.overage, a.overageAt = state, at
}

// Relogin marks an account as logged in again.
func (r *Router) Relogin(account AccountID) error {
	r.commitMu.Lock()
	defer r.commitMu.Unlock()
	if err := r.store.Err(); err != nil {
		return err
	}
	r.mu.Lock()
	defer r.mu.Unlock()
	if idx, ok := r.index[account]; ok {
		r.accounts[idx].needsLogin = false
	}
	return nil
}

func (r *Router) touch(conv ConversationID, now time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if now.After(r.active[conv]) {
		r.active[conv] = now
	}
}
