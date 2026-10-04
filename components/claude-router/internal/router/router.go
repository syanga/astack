package router

import (
	"errors"
	"fmt"
	"slices"
	"sync"
	"time"
)

// Router is the production routing boundary. Decide is the policy; the
// Commit methods make its Place and Migrate outcomes durable before dispatch.
// Route composes them for one request. Every method takes the current time so
// the simulator can drive the same code with a deterministic clock.
type Router struct {
	cfg   Config
	store *Store

	mu       sync.RWMutex
	accounts []accountView
	index    map[AccountID]int
	active   map[ConversationID]time.Time
}

// New returns a router over the enrolled accounts, in tie-break order, and an
// open store. Account login and quota state start unknown.
func New(cfg Config, accounts []Account, store *Store) (*Router, error) {
	if len(accounts) == 0 {
		return nil, errors.New("no enrolled accounts")
	}
	r := &Router{cfg: cfg, store: store, index: map[AccountID]int{}, active: map[ConversationID]time.Time{}}
	for i, a := range accounts {
		if a.ID == "" || a.Capacity <= 0 {
			return nil, fmt.Errorf("account %d: need an ID and a positive capacity", i)
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
func (r *Router) Decide(now time.Time, req Request) Decision {
	r.mu.RLock()
	defer r.mu.RUnlock()
	b, bound := r.store.Lookup(req.Conversation)
	v := view{
		accounts: r.accounts,
		index:    r.index,
		binding:  b,
		bound:    bound,
		loads:    func() []int { return r.loads(now) },
	}
	return decide(r.cfg, now, v, req)
}

func (r *Router) loads(now time.Time) []int {
	loads := make([]int, len(r.accounts))
	horizon := now.Add(-r.cfg.ActiveFor)
	r.store.each(func(conv ConversationID, b Binding) {
		last, ok := r.active[conv]
		if !ok || b.Since.After(last) {
			last = b.Since
		}
		if idx, enrolled := r.index[b.Account]; enrolled && !last.Before(horizon) {
			loads[idx]++
		}
	})
	return loads
}

// CommitAssignment durably records a conversation's first assignment and
// returns the account it is bound to. A concurrent first request that
// committed earlier wins, and its account is returned.
func (r *Router) CommitAssignment(now time.Time, conv ConversationID, account AccountID, reason Reason) (AccountID, error) {
	b, err := r.store.Assign(conv, account, reason, now)
	if err != nil {
		return "", err
	}
	r.touch(conv, now)
	return b.Account, nil
}

// CommitMigration durably moves a conversation from one account to another if
// it is still on from, and returns the account it is bound to.
func (r *Router) CommitMigration(now time.Time, conv ConversationID, from, to AccountID, reason Reason) (AccountID, error) {
	b, err := r.store.Migrate(conv, from, to, reason, now)
	if err != nil {
		return "", err
	}
	r.touch(conv, now)
	return b.Account, nil
}

// Route decides a request and commits a placement or migration before
// returning it. The returned account is the one to pin the request to.
func (r *Router) Route(now time.Time, req Request) (Decision, error) {
	d := r.Decide(now, req)
	switch d.Kind {
	case Place:
		got, err := r.CommitAssignment(now, req.Conversation, d.Account, d.Reason)
		if err != nil {
			return Decision{}, err
		}
		if got != d.Account {
			return Decision{Kind: Dispatch, Account: got, Reason: ReasonConcurrentFirst}, nil
		}
	case Migrate:
		got, err := r.CommitMigration(now, req.Conversation, d.From, d.Account, d.Reason)
		if err != nil {
			return Decision{}, err
		}
		if got != d.Account {
			return r.Route(now, req)
		}
	case Dispatch, Retry:
		r.touch(req.Conversation, now)
	}
	return d, nil
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
	for {
		b, ok := r.store.Lookup(conv)
		if !ok {
			return ErrUnassigned
		}
		if b.Account == to {
			return nil
		}
		if _, err := r.CommitMigration(now, conv, b.Account, to, ReasonManual); err != nil {
			return err
		}
	}
}

// Lookup returns a conversation's durable binding.
func (r *Router) Lookup(conv ConversationID) (Binding, bool) { return r.store.Lookup(conv) }

// Observe records an account's latest quota observation. An older
// observation than the one held is ignored.
func (r *Router) Observe(account AccountID, obs Observation) {
	r.mu.Lock()
	defer r.mu.Unlock()
	r.observe(account, obs)
}

func (r *Router) observe(account AccountID, obs Observation) {
	idx, ok := r.index[account]
	if !ok || obs.At.Before(r.accounts[idx].observation.At) {
		return
	}
	r.accounts[idx].observation = obs
}

// Report records a classified failure before output on an account. An
// exhaustion without a rejected window for the model is recorded as an
// unspecified rejection with an unknown reset. An auth failure excludes the
// account from new placement until Relogin.
func (r *Router) Report(now time.Time, account AccountID, model string, class Class, obs Observation) {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx, ok := r.index[account]
	if !ok {
		return
	}
	if class == ClassAuth {
		r.accounts[idx].needsLogin = true
	}
	if obs.At.IsZero() {
		obs.At = now
	}
	if class == ClassExhausted || class == ClassModelLimit {
		covered := slices.ContainsFunc(obs.Windows, func(w Window) bool { return w.Rejected && w.appliesTo(model) })
		if !covered {
			w := Window{Kind: Unspecified, Rejected: true}
			if class == ClassModelLimit {
				w.Models = []string{model}
			}
			obs.Windows = append(slices.Clone(obs.Windows), w)
		}
	}
	if len(obs.Windows) > 0 {
		r.observe(account, obs)
	}
}

// Relogin marks an account as logged in again.
func (r *Router) Relogin(account AccountID) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if idx, ok := r.index[account]; ok {
		r.accounts[idx].needsLogin = false
	}
}

func (r *Router) touch(conv ConversationID, now time.Time) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if now.After(r.active[conv]) {
		r.active[conv] = now
	}
}
