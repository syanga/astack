package claude

import (
	"context"
	"fmt"
	"net"
	"net/http"
	"net/http/httptrace"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

const anthropicHost = "api.anthropic.com"

const netRetryFor = 25 * time.Second

// Without a dial timeout, a connect to a host that drops packets waits for
// the operating system's TCP connect timeout, 75 s on macOS.
func providerTransport() http.RoundTripper {
	return &http.Transport{
		Proxy:               nil,
		DialContext:         (&net.Dialer{Timeout: 10 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:   true,
		MaxIdleConnsPerHost: 64,
		IdleConnTimeout:     90 * time.Second,
		TLSHandshakeTimeout: 10 * time.Second,
	}
}

type attempt struct {
	Account router.AccountID
	Path    string
	Start   time.Time
	End     time.Time
	Status  int
	Header  http.Header
	Err     error
	Phase   phase
	Refused refusal
}

type phase string

const (
	beforeWrite phase = "before_write"
	afterWrite  phase = "after_write"
)

// net/http can call these hooks after RoundTrip returns.
type writeTrace struct {
	mu     sync.Mutex
	conn   bool
	wrote  bool
	failed bool
}

func (w *writeTrace) hooks() *httptrace.ClientTrace {
	return &httptrace.ClientTrace{
		GotConn: func(httptrace.GotConnInfo) {
			w.mu.Lock()
			w.conn = true
			w.mu.Unlock()
		},
		WroteRequest: func(i httptrace.WroteRequestInfo) {
			w.mu.Lock()
			if i.Err == nil {
				w.wrote = true
			} else {
				w.failed = true
			}
			w.mu.Unlock()
		},
	}
}

// net/http's HTTP/2 transport calls no WroteRequest hook when writing the
// headers fails, so a connection without a write report counts as written.
func (w *writeTrace) phase() phase {
	w.mu.Lock()
	defer w.mu.Unlock()
	if !w.wrote && (!w.conn || w.failed) {
		return beforeWrite
	}
	return afterWrite
}

type refusal interface {
	error
	refusal()
}

type offPin struct{}

type ineligible struct{ reason router.Reason }

type overCap struct{}

func (offPin) refusal()     {}
func (ineligible) refusal() {}
func (overCap) refusal()    {}

func (offPin) Error() string { return "claude-router transport refused the attempt: account" }
func (r ineligible) Error() string {
	return "claude-router transport refused the attempt: ineligible (" + string(r.reason) + ")"
}
func (overCap) Error() string { return "claude-router transport refused the attempt: budget" }

type callInfo struct {
	id           string
	account      router.AccountID
	model        string
	conversation string
	attempt      int
	budget       *budget
}

type callKey struct{}

func withCall(ctx context.Context, info callInfo) context.Context {
	return context.WithValue(ctx, callKey{}, info)
}

type budget struct {
	attempts atomic.Int32

	mu       sync.Mutex
	netFails int
	netUntil time.Time
}

func (b *budget) netRetry(now time.Time, window time.Duration) (time.Duration, bool) {
	b.mu.Lock()
	defer b.mu.Unlock()
	if b.netFails == 0 {
		b.netUntil = now.Add(window)
	}
	b.netFails++
	wait := retryBackoff(b.netFails + 1)
	return wait, !now.Add(wait).After(b.netUntil)
}

type upstream struct {
	open func() http.RoundTripper

	mu sync.Mutex
	rt http.RoundTripper
}

func newUpstream(open func() http.RoundTripper) *upstream {
	return &upstream{open: open, rt: open()}
}

func (u *upstream) get() http.RoundTripper {
	u.mu.Lock()
	defer u.mu.Unlock()
	return u.rt
}

func (u *upstream) replace(failed http.RoundTripper) {
	u.mu.Lock()
	if u.rt != failed {
		u.mu.Unlock()
		return
	}
	fresh := u.open()
	u.rt = fresh
	u.mu.Unlock()
	if c, ok := failed.(interface{ CloseIdleConnections() }); ok && fresh != failed {
		c.CloseIdleConnections()
	}
}

// transport is the round tripper the SDK uses for every upstream request.
// The SDK uses it only while no proxy is configured at any level, which the
// service guarantees. It refuses every host but api.anthropic.com, ties
// each attempt to its router call, and reports each response's quota and
// paid-overflow headers before the response body reaches the SDK.
type transport struct {
	upstream    *upstream
	redirect    *url.URL
	limit       int32
	netRetryFor time.Duration
	accounts    map[string]router.AccountID
	admit       func(account router.AccountID, model string) router.Reason
	onHeader    func(account router.AccountID, h http.Header, start, at time.Time)
	onNetwork   func(info callInfo, a attempt)
	now         func() time.Time

	mu    sync.Mutex
	calls map[string][]attempt
}

func newTransport(up *upstream, redirect *url.URL, limit int, netRetryFor time.Duration, accounts []router.Account, now func() time.Time, admit func(router.AccountID, string) router.Reason, onHeader func(router.AccountID, http.Header, time.Time, time.Time), onNetwork func(callInfo, attempt)) *transport {
	t := &transport{upstream: up, redirect: redirect, limit: int32(limit), netRetryFor: netRetryFor, accounts: map[string]router.AccountID{}, now: now, admit: admit, onHeader: onHeader, onNetwork: onNetwork, calls: map[string][]attempt{}}
	for _, a := range accounts {
		t.accounts[string(a.ID)+".json"] = a.ID
	}
	return t
}

// RoundTripperFor implements the SDK's round tripper provider.
func (t *transport) RoundTripperFor(a *coreauth.Auth) http.RoundTripper {
	account := router.AccountID("")
	if a != nil {
		account = t.accounts[baseName(a.FileName)]
		if account == "" {
			account = t.accounts[baseName(a.ID)]
		}
	}
	return accountTransport{t: t, account: account}
}

func baseName(p string) string {
	if i := strings.LastIndexAny(p, `/\`); i >= 0 {
		return p[i+1:]
	}
	return p
}

type accountTransport struct {
	t       *transport
	account router.AccountID
}

func (rt accountTransport) RoundTrip(req *http.Request) (*http.Response, error) {
	return rt.t.roundTrip(rt.account, req)
}

func (t *transport) roundTrip(account router.AccountID, req *http.Request) (*http.Response, error) {
	if !strings.EqualFold(req.URL.Hostname(), anthropicHost) {
		closeBody(req)
		return nil, fmt.Errorf("claude-router transport refuses host %s", req.URL.Hostname())
	}
	if account == "" {
		closeBody(req)
		return nil, fmt.Errorf("claude-router transport: request for an account that is not enrolled")
	}
	info, _ := req.Context().Value(callKey{}).(callInfo)
	for try := 0; ; try++ {
		out, trace, err := t.prepare(req, try)
		if err != nil {
			return nil, err
		}
		a := attempt{Account: account, Path: req.URL.Path, Start: t.now()}
		if a.Refused = t.refuse(info.id, account, info); a.Refused != nil {
			closeBody(out)
			a.End = a.Start
			t.record(info.id, a)
			return nil, a.Refused
		}
		rt := t.upstream.get()
		resp, err := rt.RoundTrip(out)
		a.End = t.now()
		if err == nil {
			a.Status = resp.StatusCode
			a.Header = resp.Header.Clone()
			if t.onHeader != nil {
				t.onHeader(account, resp.Header, a.Start, a.End)
			}
			t.record(info.id, a)
			return resp, nil
		}
		a.Err = err
		if req.Context().Err() == nil {
			a.Phase = trace.phase()
			t.upstream.replace(rt)
		}
		var wait time.Duration
		retry := false
		if a.Phase == beforeWrite && info.budget != nil {
			info.budget.attempts.Add(-1)
			wait, retry = info.budget.netRetry(time.Now(), t.netRetryFor)
			retry = retry && req.GetBody != nil
		}
		t.record(info.id, a)
		if a.Phase != "" && info.id != "" && t.onNetwork != nil {
			t.onNetwork(info, a)
		}
		if !retry || !sleepCtx(req.Context(), wait) {
			return nil, err
		}
	}
}

func (t *transport) prepare(req *http.Request, try int) (*http.Request, *writeTrace, error) {
	trace := &writeTrace{}
	out := req.Clone(httptrace.WithClientTrace(req.Context(), trace.hooks()))
	if try > 0 && req.Body != nil {
		body, err := req.GetBody()
		if err != nil {
			return nil, nil, err
		}
		out.Body = body
	}
	if t.redirect != nil {
		out.URL.Scheme, out.URL.Host = t.redirect.Scheme, t.redirect.Host
		out.Host = ""
	}
	return out, trace, nil
}

func closeBody(req *http.Request) {
	if req.Body != nil {
		_ = req.Body.Close()
	}
}

func (t *transport) record(call string, a attempt) {
	if call == "" {
		return
	}
	t.mu.Lock()
	t.calls[call] = append(t.calls[call], a)
	t.mu.Unlock()
}

func (t *transport) refuse(call string, account router.AccountID, info callInfo) refusal {
	if info.account != "" && info.account != account {
		return offPin{}
	}
	if call != "" && t.admit != nil {
		if reason := t.admit(account, info.model); reason != "" {
			return ineligible{reason}
		}
	}
	if info.budget != nil && info.budget.attempts.Add(1) > t.limit {
		return overCap{}
	}
	return nil
}

func (t *transport) attempts(call string) []attempt {
	t.mu.Lock()
	defer t.mu.Unlock()
	return append([]attempt(nil), t.calls[call]...)
}

func (t *transport) forget(call string) {
	t.mu.Lock()
	delete(t.calls, call)
	t.mu.Unlock()
}
