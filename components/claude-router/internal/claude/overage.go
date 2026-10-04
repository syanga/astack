package claude

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"sync"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// usageURL is the claude.ai usage endpoint Claude Code reads for /usage. It
// is undocumented as an API; the Agent SDK exposes it only as an
// experimental call. Its extra_usage.is_enabled field is the account's
// paid-overflow setting, and reading it makes no inference request.
const usageURL = "https://" + anthropicHost + "/api/oauth/usage"

const minRecheckInterval = 30 * time.Second

type overageState struct {
	State   router.Overage `json:"state,omitempty"`
	At      time.Time      `json:"at,omitzero"`
	Source  string         `json:"source,omitempty"`
	Failure string         `json:"last_read_failure,omitempty"`
}

type overageReader struct {
	s        *Service
	mu       sync.Mutex
	inFlight map[router.AccountID]*sync.Mutex
	last     map[router.AccountID]time.Time
	state    map[router.AccountID]overageState
}

func newOverageReader(s *Service) *overageReader {
	return &overageReader{s: s, inFlight: map[router.AccountID]*sync.Mutex{}, last: map[router.AccountID]time.Time{}, state: map[router.AccountID]overageState{}}
}

func (o *overageReader) lock(account router.AccountID) *sync.Mutex {
	o.mu.Lock()
	defer o.mu.Unlock()
	m := o.inFlight[account]
	if m == nil {
		m = &sync.Mutex{}
		o.inFlight[account] = m
	}
	return m
}

func (o *overageReader) recheck(ctx context.Context, account router.AccountID) bool {
	m := o.lock(account)
	m.Lock()
	defer m.Unlock()
	o.mu.Lock()
	recent := o.s.now().Sub(o.last[account]) < minRecheckInterval
	o.mu.Unlock()
	if recent {
		return false
	}
	o.readLocked(ctx, account)
	return true
}

func (o *overageReader) read(ctx context.Context, account router.AccountID) {
	m := o.lock(account)
	m.Lock()
	defer m.Unlock()
	o.readLocked(ctx, account)
}

func (o *overageReader) readLocked(ctx context.Context, account router.AccountID) {
	start := o.s.now()
	o.mu.Lock()
	o.last[account] = start
	o.mu.Unlock()
	state, err := o.fetch(ctx, account)
	end := o.s.now()
	dur := end.Sub(start).Milliseconds()
	if err != nil {
		o.mu.Lock()
		st := o.state[account]
		st.Failure = err.Error()
		o.state[account] = st
		o.mu.Unlock()
		o.s.events.emit(Event{Kind: "overage_read", Account: account, Outcome: "unknown", Detail: err.Error(), DurationMS: &dur})
		return
	}
	begun, arrived, ok := o.s.bounded(start, end, state != router.OverageDisabled)
	if !ok {
		return
	}
	stamp := readingStamp(state, begun, arrived)
	o.s.router.ObserveOverage(account, state, stamp)
	o.note(account, state, stamp, "settings")
	o.s.events.emit(Event{Kind: "overage_read", Account: account, Outcome: "read", Overage: state, DurationMS: &dur})
}

// readingStamp dates a reading the upstream made at an unknown time between
// the start of a read or attempt and its arrival. A disabled reading, which
// allows dispatch, takes the earliest possible time, and a reading that
// blocks dispatch, enabled or paid use, takes the latest. A reading is then
// never ordered after another that the upstream may have made later.
func readingStamp(state router.Overage, start, arrival time.Time) time.Time {
	if state == router.OverageDisabled {
		return start
	}
	return arrival
}

func (o *overageReader) note(account router.AccountID, state router.Overage, at time.Time, source string) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if cur := o.state[account]; at.Before(cur.At) {
		return
	}
	o.state[account] = overageState{State: state, At: at, Source: source}
}

func (o *overageReader) readFailure(account router.AccountID) string {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.state[account].Failure
}

func (o *overageReader) snapshot() map[router.AccountID]overageState {
	o.mu.Lock()
	defer o.mu.Unlock()
	out := make(map[router.AccountID]overageState, len(o.state))
	for k, v := range o.state {
		out[k] = v
	}
	return out
}

var errNoExtraUsage = errors.New("usage response has no extra_usage")

func (o *overageReader) fetch(ctx context.Context, account router.AccountID) (router.Overage, error) {
	authID := o.s.authIDs[account]
	auth, ok := o.s.core.GetByID(authID)
	if !ok || auth == nil {
		return "", errors.New("account is not registered in the SDK")
	}
	ctx, cancel := context.WithTimeout(ctx, 10*time.Second)
	defer cancel()
	header := http.Header{}
	header.Set("Accept", "application/json")
	header.Set("Anthropic-Beta", "oauth-2025-04-20")
	req, err := o.s.core.NewHttpRequest(ctx, auth, http.MethodGet, usageURL, nil, header)
	if err != nil {
		return "", errors.New("could not prepare the usage request")
	}
	resp, err := o.s.transport.roundTrip(account, req)
	if err != nil {
		return "", errors.New("usage request failed before a response")
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("usage endpoint answered %d", resp.StatusCode)
	}
	var body struct {
		ExtraUsage *struct {
			IsEnabled *bool `json:"is_enabled"`
		} `json:"extra_usage"`
	}
	if err := json.NewDecoder(io.LimitReader(resp.Body, 1<<20)).Decode(&body); err != nil {
		return "", errors.New("usage response is not JSON")
	}
	if body.ExtraUsage == nil || body.ExtraUsage.IsEnabled == nil {
		return "", errNoExtraUsage
	}
	if *body.ExtraUsage.IsEnabled {
		return router.OverageEnabled, nil
	}
	return router.OverageDisabled, nil
}
