package router

import (
	"errors"
	"fmt"
	"maps"
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
// After any journal write or sync failure, or a failed write of the account
// state of a router made by Open, every method that answers with an account
// or changes routing state returns ErrFailed until the store is reopened.
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
	// saved is the account state last written by persist, or nil for a
	// router whose account state lives in memory only (New).
	saved map[AccountID]AccountState
}

// New returns a router over the enrolled accounts, in tie-break order, and an
// open store. Account login and quota state start unknown and live in memory
// only; Open keeps them across restarts.
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
	if err := r.store.Err(); err != nil {
		return Decision{}, err
	}
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
	r.persist()
}

func windowKey(w Window) string {
	models := slices.Clone(w.Models)
	slices.Sort(models)
	return string(w.Kind) + "|" + strings.Join(models, ",")
}

// observe merges an observation that passed the attempt-start rule, in any
// order relative to earlier ones. Only the utilization snapshot is gated on
// recency. Per window, the newest report wins: a rejection is cleared only by
// a report of the same window, not rejected, stamped after it, and a
// rejection stamped before such a report, or before the window's newest
// recorded rejection, is not recorded. Of two rejections stamped at the same
// time, the later end wins. A rejection is kept after its reset, where it no
// longer blocks, until one weekly period after its stamp, and an allowed
// report's time is kept for one weekly period: older evidence of the window
// belongs to a window that has ended.
func (r *Router) observe(account AccountID, obs Observation) {
	idx, ok := r.index[account]
	if !ok {
		return
	}
	a := &r.accounts[idx]
	if !obs.At.Before(a.observation.At) {
		a.observation = obs
	}
	if a.allowedAt == nil {
		a.allowedAt = map[string]time.Time{}
	}
	for _, w := range obs.Windows {
		key := windowKey(w)
		if !w.Rejected {
			if obs.At.After(a.allowedAt[key]) {
				a.allowedAt[key] = obs.At
			}
			a.rejections = slices.DeleteFunc(a.rejections, func(x rejection) bool {
				return windowKey(x.Window) == key && x.at.Before(obs.At)
			})
			continue
		}
		if a.allowedAt[key].After(obs.At) {
			continue
		}
		rj := rejection{Window: w, at: obs.At}
		i := slices.IndexFunc(a.rejections, func(x rejection) bool { return windowKey(x.Window) == key })
		if i < 0 {
			a.rejections = append(a.rejections, rj)
			continue
		}
		prev := a.rejections[i]
		if prev.at.Before(obs.At) || prev.at.Equal(obs.At) && rj.end(r.cfg).After(prev.end(r.cfg)) {
			a.rejections[i] = rj
		}
	}
	horizon := obs.At.Add(-Weekly.Period())
	a.rejections = slices.DeleteFunc(a.rejections, func(x rejection) bool {
		return !x.end(r.cfg).After(obs.At) && !x.at.After(horizon)
	})
	maps.DeleteFunc(a.allowedAt, func(_ string, at time.Time) bool { return !at.After(horizon) })
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
			a := &r.accounts[idx]
			a.needsLogin = true
			if f.AttemptStart.After(a.needsLoginAt) {
				a.needsLoginAt = f.AttemptStart
			}
			r.persist()
		}
	case ClassExhausted, ClassModelLimit:
		confirmed := ok && fromAttempt(f.AttemptStart, f.Observation) &&
			slices.ContainsFunc(f.Observation.Windows, func(w Window) bool { return w.Rejected && w.appliesTo(f.Model) })
		if !confirmed {
			return ClassThrottle
		}
		r.observe(f.Account, f.Observation)
		r.persist()
	}
	return f.Class
}

// ObserveOverage records a paid-overflow check (disabled or enabled) or an
// observation of paid use, stamped at. Checks are kept in time order: a check
// stamped before the latest recorded check is ignored, and of two checks
// stamped at the same time, disabled does not replace another state. Paid use
// excludes the account unless the latest check is disabled and stamped
// strictly after the latest paid use.
func (r *Router) ObserveOverage(account AccountID, state Overage, at time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx, ok := r.index[account]
	if !ok {
		return
	}
	a := &r.accounts[idx]
	switch {
	case state == OveragePaidUse:
		if at.After(a.paidUseAt) {
			a.paidUseAt = at
		}
	case at.After(a.checkAt), at.Equal(a.checkAt) && state != OverageDisabled:
		a.check, a.checkAt = state, at
	}
	r.persist()
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
		r.persist()
	}
	return r.store.Err()
}

// LoginConfirmed clears an account's login requirement when a request with
// its credential that started at start succeeded after the failure that set
// it: the credential works, so the account needs no browser login. It
// reports whether it cleared the requirement.
func (r *Router) LoginConfirmed(account AccountID, start time.Time) bool {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx, ok := r.index[account]
	if !ok || !r.accounts[idx].needsLogin || !start.After(r.accounts[idx].needsLoginAt) {
		return false
	}
	r.accounts[idx].needsLogin = false
	r.persist()
	return true
}

func (r *Router) touch(conv ConversationID, now time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if now.After(r.active[conv]) {
		r.active[conv] = now
	}
}
