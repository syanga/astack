package claude

import (
	"context"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

const anthropicHost = "api.anthropic.com"

type attempt struct {
	Account router.AccountID
	Path    string
	Start   time.Time
	End     time.Time
	Status  int
	Header  http.Header
	Err     error
	Refused refusal
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
	id      string
	account router.AccountID
	model   string
	budget  *atomic.Int32
}

type callKey struct{}

func withCall(ctx context.Context, id string, account router.AccountID, model string, budget *atomic.Int32) context.Context {
	return context.WithValue(ctx, callKey{}, callInfo{id: id, account: account, model: model, budget: budget})
}

// transport is the round tripper the SDK uses for every upstream request.
// The SDK uses it only while no proxy is configured at any level, which the
// service guarantees. It refuses every host but api.anthropic.com, ties
// each attempt to its router call, and reports each response's quota and
// paid-overflow headers before the response body reaches the SDK.
type transport struct {
	inner    http.RoundTripper
	redirect *url.URL
	limit    int32
	accounts map[string]router.AccountID
	admit    func(account router.AccountID, model string) router.Reason
	onHeader func(account router.AccountID, h http.Header, start, at time.Time)
	now      func() time.Time

	mu    sync.Mutex
	calls map[string][]attempt
}

func newTransport(inner http.RoundTripper, redirect *url.URL, limit int, accounts []router.Account, now func() time.Time, admit func(router.AccountID, string) router.Reason, onHeader func(router.AccountID, http.Header, time.Time, time.Time)) *transport {
	t := &transport{inner: inner, redirect: redirect, limit: int32(limit), accounts: map[string]router.AccountID{}, now: now, admit: admit, onHeader: onHeader, calls: map[string][]attempt{}}
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
		if req.Body != nil {
			_ = req.Body.Close()
		}
		return nil, fmt.Errorf("claude-router transport refuses host %s", req.URL.Hostname())
	}
	if account == "" {
		if req.Body != nil {
			_ = req.Body.Close()
		}
		return nil, fmt.Errorf("claude-router transport: request for an account that is not enrolled")
	}
	out := req
	if t.redirect != nil {
		out = req.Clone(req.Context())
		out.URL.Scheme, out.URL.Host = t.redirect.Scheme, t.redirect.Host
		out.Host = ""
	}
	info, _ := req.Context().Value(callKey{}).(callInfo)
	call := info.id
	a := attempt{Account: account, Path: req.URL.Path, Start: t.now()}
	if a.Refused = t.refuse(call, account, info); a.Refused != nil {
		if req.Body != nil {
			_ = req.Body.Close()
		}
		a.End = a.Start
		t.mu.Lock()
		t.calls[call] = append(t.calls[call], a)
		t.mu.Unlock()
		return nil, a.Refused
	}
	resp, err := t.inner.RoundTrip(out)
	a.End = t.now()
	if err != nil {
		a.Err = err
	} else {
		a.Status = resp.StatusCode
		a.Header = resp.Header.Clone()
		if t.onHeader != nil {
			t.onHeader(account, resp.Header, a.Start, a.End)
		}
	}
	if call != "" {
		t.mu.Lock()
		t.calls[call] = append(t.calls[call], a)
		t.mu.Unlock()
	}
	return resp, err
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
	if info.budget != nil && info.budget.Add(1) > t.limit {
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
