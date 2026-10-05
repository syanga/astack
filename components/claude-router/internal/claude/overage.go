package claude

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

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
	s           *Service
	mu          sync.Mutex
	inFlight    map[router.AccountID]*sync.Mutex
	last        map[router.AccountID]time.Time
	state       map[router.AccountID]overageState
	failedToken map[router.AccountID]string
	streak      map[router.AccountID]readStreak
	settled     map[router.AccountID]chan struct{}
}

// readStreak is an account's run of failed reads, scheduled or on demand:
// how many in a row, and the last error.
type readStreak struct {
	failures int
	last     error
}

func newOverageReader(s *Service) *overageReader {
	return &overageReader{
		s: s, inFlight: map[router.AccountID]*sync.Mutex{}, last: map[router.AccountID]time.Time{},
		state: map[router.AccountID]overageState{}, failedToken: map[router.AccountID]string{},
		streak: map[router.AccountID]readStreak{}, settled: map[router.AccountID]chan struct{}{},
	}
}

// failedReads returns the account's current streak of failed reads.
func (o *overageReader) failedReads(account router.AccountID) readStreak {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.streak[account]
}

// settlements receives after each read of the account, scheduled or on
// demand, has updated its streak.
func (o *overageReader) settlements(account router.AccountID) <-chan struct{} {
	o.mu.Lock()
	defer o.mu.Unlock()
	return o.settledChan(account)
}

func (o *overageReader) settledChan(account router.AccountID) chan struct{} {
	ch := o.settled[account]
	if ch == nil {
		ch = make(chan struct{}, 1)
		o.settled[account] = ch
	}
	return ch
}

// settle records the outcome of a read in the account's streak. Callers
// hold o.mu.
func (o *overageReader) settle(account router.AccountID, err error) {
	if err != nil {
		st := o.streak[account]
		o.streak[account] = readStreak{failures: st.failures + 1, last: err}
	} else {
		delete(o.streak, account)
	}
	select {
	case o.settledChan(account) <- struct{}{}:
	default:
	}
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

// readLocked reads the account's setting and records the outcome in its
// streak of failed reads.
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
		o.settle(account, err)
		o.mu.Unlock()
		o.s.events.emit(Event{Kind: "overage_read", Account: account, Outcome: "unknown", Detail: err.Error(), DurationMS: &dur})
		return
	}
	begun, arrived, ok := o.s.bounded(start, end, state != router.OverageDisabled)
	if !ok {
		o.mu.Lock()
		o.settle(account, errors.New("reading dropped: zero or future stamp"))
		o.mu.Unlock()
		return
	}
	stamp := readingStamp(state, begun, arrived)
	o.s.router.ObserveOverage(account, state, stamp)
	o.note(account, state, stamp, "settings")
	o.mu.Lock()
	o.settle(account, nil)
	o.mu.Unlock()
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

// readError is a failed settings read. RetryAfter is the delay the usage
// endpoint asked for, or zero.
type readError struct {
	msg          string
	retryAfter   time.Duration
	unauthorized bool
}

func (e *readError) Error() string { return e.msg }

// fetch reads the setting with a credential the SDK is not about to
// replace. The SDK revokes an access token when it refreshes it, so a read
// that races the SDK's refresh answers 401. After a 401 on a credential the
// router did not just refresh, the read renews the credential once and reads
// once more. A read sends at most two usage requests and starts at most one
// refresh.
func (o *overageReader) fetch(ctx context.Context, account router.AccountID) (router.Overage, error) {
	authID := o.s.authIDs[account]
	auth, ok := o.s.core.GetByID(authID)
	if !ok || auth == nil {
		return "", errors.New("account is not registered in the SDK")
	}
	refreshed := false
	if refreshDue(auth, time.Now()) {
		var err error
		if auth, refreshed, err = o.renew(ctx, account, authID, accessToken(auth), "expiring"); err != nil {
			return "", err
		}
	}
	state, err := o.get(ctx, account, auth)
	var re *readError
	if !errors.As(err, &re) || !re.unauthorized || refreshed {
		return state, err
	}
	auth, _, err = o.renew(ctx, account, authID, accessToken(auth), "unauthorized")
	if err != nil {
		return "", fmt.Errorf("usage endpoint answered 401, and %w", err)
	}
	return o.get(ctx, account, auth)
}

// sdkRefreshWait bounds how long a read waits for a refresh the SDK has
// pending.
const sdkRefreshWait = 3 * time.Second

var (
	errRefreshFailed  = errors.New("the last refresh of the credential failed; the SDK retries it on its own schedule")
	errRefreshPending = errors.New("the SDK's refresh of the credential did not finish in time")
)

// renew returns a credential to use in place of the account's current one,
// whose access token observed is due for refresh or was refused. It reports
// whether the router refreshed it.
//
// The router prefers the SDK's refresh: a newer credential the SDK already
// installed, or one the SDK installs within sdkRefreshWait of a pending
// refresh. Otherwise it refreshes through the SDK, and the refresh guard
// declines that refresh, with no exchange, when the credential changed since
// observed or its refresh token was spent by an earlier exchange. A failed
// refresh, the router's or the SDK's, starts a failure run that lasts until
// the access token changes; during it a read fails at once.
func (o *overageReader) renew(ctx context.Context, account router.AccountID, authID, observed, reason string) (*coreauth.Auth, bool, error) {
	cur, ok := o.s.core.GetByID(authID)
	if !ok || cur == nil {
		return nil, false, errors.New("account is not registered in the SDK")
	}
	if accessToken(cur) != observed {
		o.s.events.emit(Event{Kind: "credential_refresh", Account: account, Reason: reason, Outcome: "sdk_refreshed"})
		return cur, false, nil
	}
	if o.refreshFailed(account, cur, reason) {
		return nil, false, errRefreshFailed
	}
	if cur.NextRefreshAfter.After(time.Now()) {
		landed, failed := o.awaitSDKRefresh(ctx, authID, cur)
		switch {
		case landed != nil:
			o.s.events.emit(Event{Kind: "credential_refresh", Account: account, Reason: reason, Outcome: "sdk_refreshed"})
			return landed, false, nil
		case failed:
			o.failRun(account, observed, Event{Reason: reason, Detail: "sdk refresh"})
			return nil, false, errRefreshFailed
		default:
			return nil, false, errRefreshPending
		}
	}
	if ctx.Err() != nil {
		return nil, false, errors.New("the read was canceled before the refresh")
	}
	if ex, _ := o.s.core.Executor("claude"); ex != coreauth.ProviderExecutor(o.s.guard) {
		return nil, false, errors.New("the refresh guard is not the SDK's Claude executor")
	}
	start := o.s.now()
	// A refresh runs to completion once begun: a refresh canceled after the
	// token endpoint rotated the refresh token would lose the new one.
	rctx, cancel := context.WithTimeout(withRouterRefresh(context.WithoutCancel(ctx), observed), 30*time.Second)
	auth, err := o.s.core.ForceRefreshAuth(rctx, authID)
	cancel()
	dur := o.s.now().Sub(start).Milliseconds()
	if errors.Is(err, errRefreshDeclined) {
		if cur, ok := o.s.core.GetByID(authID); ok && cur != nil && accessToken(cur) != observed {
			o.s.events.emit(Event{Kind: "credential_refresh", Account: account, Reason: reason, Outcome: "sdk_refreshed"})
			return cur, false, nil
		}
		o.failRun(account, observed, Event{Reason: reason, Detail: "sdk refresh"})
		return nil, false, errRefreshFailed
	}
	if err != nil || auth == nil {
		o.failRun(account, observed, Event{Reason: reason, DurationMS: &dur})
		return nil, false, errors.New("the SDK could not refresh the credential")
	}
	o.s.events.emit(Event{Kind: "credential_refresh", Account: account, Reason: reason, Outcome: "refreshed", DurationMS: &dur})
	return auth, true, nil
}

// refreshFailed reports whether the account is in a failure run for auth's
// access token: a refresh of that token failed and none has replaced it.
func (o *overageReader) refreshFailed(account router.AccountID, auth *coreauth.Auth, reason string) bool {
	token := accessToken(auth)
	o.mu.Lock()
	failedToken, known := o.failedToken[account]
	o.mu.Unlock()
	if known && failedToken == token {
		return true
	}
	if !o.s.guard.failed(auth) {
		return false
	}
	o.failRun(account, token, Event{Reason: reason, Detail: "sdk refresh"})
	return true
}

// failRun starts a failure run for token and records the failed refresh as
// a credential_refresh event, once per run.
func (o *overageReader) failRun(account router.AccountID, token string, e Event) {
	o.mu.Lock()
	failedToken, known := o.failedToken[account]
	o.failedToken[account] = token
	o.mu.Unlock()
	if known && failedToken == token {
		return
	}
	e.Kind, e.Account, e.Outcome = "credential_refresh", account, "failed"
	o.s.events.emit(e)
}

// awaitSDKRefresh polls until the SDK's pending refresh of before lands or
// its exchange fails, for at most sdkRefreshWait. It returns the credential
// the refresh installed, or nil and whether the exchange failed.
func (o *overageReader) awaitSDKRefresh(ctx context.Context, authID string, before *coreauth.Auth) (*coreauth.Auth, bool) {
	deadline := time.NewTimer(sdkRefreshWait)
	defer deadline.Stop()
	tick := time.NewTicker(50 * time.Millisecond)
	defer tick.Stop()
	for {
		select {
		case <-ctx.Done():
			return nil, false
		case <-deadline.C:
			return nil, false
		case <-tick.C:
		}
		cur, ok := o.s.core.GetByID(authID)
		if !ok || cur == nil {
			return nil, false
		}
		if accessToken(cur) != accessToken(before) {
			return cur, false
		}
		if o.s.guard.failed(cur) {
			return nil, true
		}
	}
}

// refreshDue mirrors the SDK's refresh decision for a Claude credential
// (Manager.shouldRefresh): the access token expires within the provider's
// refresh lead, four hours for Claude. The SDK's refresh loop replaces such
// a credential as soon as it can.
func refreshDue(a *coreauth.Auth, now time.Time) bool {
	exp, ok := a.ExpirationTime()
	if !ok || exp.IsZero() {
		return false
	}
	lead := coreauth.ProviderRefreshLead(a.Provider, a.Runtime)
	if lead == nil {
		return !exp.After(now)
	}
	return exp.Sub(now) <= *lead
}

func accessToken(a *coreauth.Auth) string {
	if a == nil {
		return ""
	}
	t, _ := a.Metadata["access_token"].(string)
	return t
}

func (o *overageReader) get(ctx context.Context, account router.AccountID, auth *coreauth.Auth) (router.Overage, error) {
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
		return "", &readError{msg: "usage request failed before a response"}
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		e := &readError{msg: fmt.Sprintf("usage endpoint answered %d", resp.StatusCode), unauthorized: resp.StatusCode == http.StatusUnauthorized}
		if d, ok := retryAfter(resp.Header.Get("Retry-After"), time.Now()); ok {
			e.retryAfter = d
			e.msg += fmt.Sprintf(" (retry-after %s)", d)
		}
		return "", e
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

// retryAfter parses a Retry-After value: delay seconds or an HTTP date.
func retryAfter(v string, now time.Time) (time.Duration, bool) {
	v = strings.TrimSpace(v)
	if v == "" {
		return 0, false
	}
	if n, err := strconv.ParseInt(v, 10, 64); err == nil {
		if n < 0 || n > int64(24*time.Hour/time.Second) {
			return 0, false
		}
		return time.Duration(n) * time.Second, true
	}
	if t, err := http.ParseTime(v); err == nil {
		return max(t.Sub(now), 0), true
	}
	return 0, false
}

// readRetries is how many re-reads one run of failed reads may add. After
// them, the account is read once per OverageCheckEvery until a read
// succeeds.
const readRetries = 6

const defaultReadRetryBase = 5 * time.Second

// nextRead is the delay before an account's next scheduled read, after
// failures failed reads in a row ending in err. The n-th failure waits
// base*2^(n-1), or the endpoint's Retry-After when that is longer, capped
// at half of every. After a success, and once the retries are used, it is
// every.
func nextRead(failures int, err error, every, base time.Duration) time.Duration {
	if failures == 0 || failures > readRetries {
		return every
	}
	d := base << (failures - 1)
	var re *readError
	if errors.As(err, &re) && re.retryAfter > d {
		d = re.retryAfter
	}
	return min(d, every/2)
}
