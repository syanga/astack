package router

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"time"
)

// AccountState is the part of an account's state that survives a restart:
// the login requirement, known rejections, the latest paid-overflow check,
// and the latest observed paid use. Quota utilization is not kept, because
// it informs only reset preference and goes stale within FreshFor. Allowed
// window reports are not kept either: without them a delayed rejection
// stamped before an allowed report is recorded after a restart, which blocks
// the account longer, never shorter.
type AccountState struct {
	ID           AccountID   `json:"id"`
	NeedsLogin   bool        `json:"needs_login,omitempty"`
	NeedsLoginAt time.Time   `json:"needs_login_at,omitzero"`
	Rejections   []Rejection `json:"rejections,omitempty"`
	Check        Overage     `json:"check,omitempty"`
	CheckAt      time.Time   `json:"check_at,omitzero"`
	PaidUseAt    time.Time   `json:"paid_use_at,omitzero"`
}

// Rejection is a rejected quota window and the time it was observed.
type Rejection struct {
	Window
	At time.Time `json:"at"`
}

// checkPersistEvery bounds how often an unchanged disabled check is written
// only to advance its time. A persisted check older than the one in memory
// goes stale sooner after a restart, which refuses dispatch sooner.
const checkPersistEvery = time.Minute

const accountsName = "accounts.json"

func (a accountView) state() AccountState {
	st := AccountState{ID: a.ID, NeedsLogin: a.needsLogin, NeedsLoginAt: a.needsLoginAt,
		Check: a.check, CheckAt: a.checkAt, PaidUseAt: a.paidUseAt}
	for _, rj := range a.rejections {
		st.Rejections = append(st.Rejections, Rejection{Window: rj.Window, At: rj.at})
	}
	return st
}

// needsWrite reports whether cur differs from the saved state in anything
// but a disabled check that advanced by less than checkPersistEvery, or a
// rejection with a known reset that was observed again later. A saved
// rejection's older time changes nothing before its reset.
func needsWrite(saved, cur AccountState) bool {
	advanced := cur.Check == OverageDisabled && saved.Check == OverageDisabled &&
		cur.CheckAt.After(saved.CheckAt) && cur.CheckAt.Sub(saved.CheckAt) < checkPersistEvery
	if !advanced && !cur.CheckAt.Equal(saved.CheckAt) {
		return true
	}
	return saved.NeedsLogin != cur.NeedsLogin || !saved.NeedsLoginAt.Equal(cur.NeedsLoginAt) ||
		saved.Check != cur.Check || !saved.PaidUseAt.Equal(cur.PaidUseAt) ||
		!slices.EqualFunc(saved.Rejections, cur.Rejections, func(x, y Rejection) bool {
			return (x.At.Equal(y.At) || !x.ResetsAt.IsZero()) && x.Kind == y.Kind && x.Rejected == y.Rejected &&
				x.ResetsAt.Equal(y.ResetsAt) && slices.Equal(x.Models, y.Models)
		})
}

// Open returns a router whose account state survives restarts. It loads the
// state saved in the store's directory and writes every later change before
// the call that made it returns. A stamp later than now, from a clock that
// stepped back, is clamped as the service clamps live readings: blocking
// evidence is kept and stamped now, and a disabled check is dropped.
func Open(cfg Config, accounts []Account, store *Store, now time.Time) (*Router, error) {
	r, err := New(cfg, accounts, store)
	if err != nil {
		return nil, err
	}
	states, err := store.loadAccounts()
	if err != nil {
		return nil, err
	}
	r.saved = map[AccountID]AccountState{}
	for _, st := range states {
		idx, ok := r.index[st.ID]
		if !ok {
			r.unenrolled = append(r.unenrolled, st)
			continue
		}
		a := &r.accounts[idx]
		a.needsLogin, a.needsLoginAt = st.NeedsLogin, clamp(st.NeedsLoginAt, now)
		a.check, a.checkAt = st.Check, st.CheckAt
		if a.checkAt.After(now) {
			if a.check == OverageDisabled {
				a.check, a.checkAt = "", time.Time{}
			} else {
				a.checkAt = now
			}
		}
		a.paidUseAt = clamp(st.PaidUseAt, now)
		for _, rj := range st.Rejections {
			a.rejections = append(a.rejections, rejection{Window: rj.Window, at: clamp(rj.At, now)})
		}
	}
	for _, a := range r.accounts {
		r.saved[a.ID] = a.state()
	}
	return r, nil
}

func clamp(t, now time.Time) time.Time {
	if t.After(now) {
		return now
	}
	return t
}

// persist writes the account state when it changed since the last write,
// with the saved records of accounts that are not enrolled, unchanged.
// Callers hold r.mu. A failed write stops the store, as a failed journal
// write does, so no later decision relies on state a restart would lose.
func (r *Router) persist() {
	if r.saved == nil {
		return
	}
	states := make([]AccountState, len(r.accounts), len(r.accounts)+len(r.unenrolled))
	dirty := false
	for i, a := range r.accounts {
		states[i] = a.state()
		dirty = dirty || needsWrite(r.saved[a.ID], states[i])
	}
	if !dirty {
		return
	}
	if r.store.saveAccounts(append(states, r.unenrolled...)) != nil {
		return
	}
	for _, st := range states {
		r.saved[st.ID] = st
	}
}

// AccountStates returns every enrolled account's durable state.
func (r *Router) AccountStates() []AccountState {
	r.mu.RLock()
	defer r.mu.RUnlock()
	out := make([]AccountState, len(r.accounts))
	for i, a := range r.accounts {
		out[i] = a.state()
	}
	return out
}

func (s *Store) loadAccounts() ([]AccountState, error) {
	data, err := os.ReadFile(filepath.Join(s.dir, accountsName))
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var states []AccountState
	if err := json.Unmarshal(data, &states); err != nil {
		return nil, fmt.Errorf("%s: %w", accountsName, err)
	}
	for _, st := range states {
		if st.ID == "" {
			return nil, fmt.Errorf("%s: an account state without an ID", accountsName)
		}
	}
	return states, nil
}

// saveAccounts replaces the saved account state durably: it writes a
// temporary file, syncs it, renames it over the old one, and syncs the
// directory. A failure stops the store until it is reopened.
func (s *Store) saveAccounts(states []AccountState) error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.err(); err != nil {
		return err
	}
	if err := s.writeAccounts(states); err != nil {
		s.failed = err
		return err
	}
	return nil
}

func (s *Store) writeAccounts(states []AccountState) error {
	data, err := json.Marshal(states)
	if err != nil {
		return err
	}
	tmp := filepath.Join(s.dir, accountsName+".tmp")
	f, err := os.OpenFile(tmp, os.O_WRONLY|os.O_CREATE|os.O_TRUNC, 0o600)
	if err != nil {
		return err
	}
	if _, err := f.Write(data); err != nil {
		f.Close()
		return err
	}
	if err := f.Sync(); err != nil {
		f.Close()
		return err
	}
	if err := f.Close(); err != nil {
		return err
	}
	if err := os.Rename(tmp, filepath.Join(s.dir, accountsName)); err != nil {
		return err
	}
	return syncDir(s.dir)
}

// ClearNeedsLogin records in the saved account state that the account was
// logged in again. claude-router login calls it while it holds the store, so
// the next start does not exclude the account.
func (s *Store) ClearNeedsLogin(account AccountID) error {
	states, err := s.loadAccounts()
	if err != nil {
		return err
	}
	i := slices.IndexFunc(states, func(st AccountState) bool { return st.ID == account })
	if i < 0 || !states[i].NeedsLogin {
		return nil
	}
	states[i].NeedsLogin, states[i].NeedsLoginAt = false, time.Time{}
	return s.saveAccounts(states)
}
