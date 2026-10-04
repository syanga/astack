// Package probes drives the pinned CLIProxyAPI SDK against an in-process fake
// Anthropic upstream and records sanitized outcomes. It is feasibility
// evidence for the routing contract, not production runtime code.
package probes

import (
	"bufio"
	"bytes"
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/gin-gonic/gin"
	sdkapi "github.com/router-for-me/CLIProxyAPI/v8/sdk/api"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/api/handlers"
	claudehandlers "github.com/router-for-me/CLIProxyAPI/v8/sdk/api/handlers/claude"
	sdkauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/auth"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy"
	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/config"
)

const (
	Model         = "claude-sonnet-4-5-20250929"
	routePath     = "/probe/v1/messages"
	headerAccount = "X-Probe-Account"
	headerCall    = "X-Probe-Call"
	headerRawPin  = "X-Probe-Raw-Pin"
)

// Settings is the subset of SDK configuration the probes vary.
type Settings struct {
	Strategy         string
	SessionAffinity  bool
	RequestRetry     int
	MaxRetryInterval int
	DisableCooling   bool
	BootstrapRetries int
}

// Options configures a Probe.
type Options struct {
	Accounts []string
	Settings Settings
}

// Result is one sanitized SDK execution result seen by the result policy.
type Result struct {
	AuthID          string `json:"auth_id"`
	Account         string `json:"account"`
	Success         bool   `json:"success"`
	HTTPStatus      int    `json:"http_status,omitempty"`
	Code            string `json:"code,omitempty"`
	CredentialScope bool   `json:"credential_scope"`
	RetryAfterMS    int64  `json:"retry_after_ms,omitempty"`
}

// Call is one client request through the probe route.
type Call struct {
	Account string
	// RawPin pins an SDK auth ID directly, bypassing the account name map.
	RawPin string
	Cancel time.Duration
}

// Outcome is what the client observed for a Call.
type Outcome struct {
	Call      string   `json:"call"`
	Pinned    string   `json:"pinned_account,omitempty"`
	Status    int      `json:"status"`
	Text      string   `json:"text,omitempty"`
	Events    []string `json:"events,omitempty"`
	ErrorType string   `json:"error_type,omitempty"`
	Selected  []string `json:"selected_accounts"`
	// Signal is what the SDK error handed to the route exposes.
	Signal     *ErrorSignal `json:"error_signal,omitempty"`
	Canceled   bool         `json:"client_canceled"`
	Incomplete bool         `json:"stream_incomplete"`
}

// Probe is an embedded SDK service bound to loopback with fake accounts.
type Probe struct {
	Upstream *Upstream

	cfgPath  string
	authDir  string
	baseURL  string
	port     int
	instance string
	core     *coreauth.Manager
	stop     context.CancelFunc
	runErr   chan error
	client   *http.Client
	mu       sync.Mutex
	ids      map[string]string
	names    map[string]string
	selected map[string][]string
	signals  map[string]*ErrorSignal
	results  []Result
	seq      int
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

// Start writes temporary configuration and fake credentials, then runs the
// SDK service until Close.
func Start(dir string, opts Options) (*Probe, error) {
	port, err := freePort()
	if err != nil {
		return nil, err
	}
	p := &Probe{
		Upstream: NewUpstream(),
		cfgPath:  filepath.Join(dir, "config.yaml"),
		authDir:  filepath.Join(dir, "auths"),
		port:     port,
		instance: randomHex(3),
		baseURL:  fmt.Sprintf("http://127.0.0.1:%d", port),
		runErr:   make(chan error, 1),
		client:   &http.Client{Timeout: 30 * time.Second},
		ids:      map[string]string{},
		names:    map[string]string{},
		selected: map[string][]string{},
		signals:  map[string]*ErrorSignal{},
	}
	if err := os.MkdirAll(p.authDir, 0o700); err != nil {
		return nil, err
	}
	if err := p.WriteSettings(opts.Settings); err != nil {
		return nil, err
	}
	cfg, err := config.LoadConfig(p.cfgPath)
	if err != nil {
		return nil, err
	}

	store := sdkauth.NewFileTokenStore()
	store.SetBaseDir(p.authDir)
	p.core = coreauth.NewManager(store, nil, nil)

	ready := make(chan struct{})
	svc, err := cliproxy.NewBuilder().
		WithConfig(cfg).
		WithConfigPath(p.cfgPath).
		WithCoreAuthManager(p.core).
		WithResultPolicy(coreauth.ResultPolicyFunc(p.observe)).
		WithServerOptions(sdkapi.WithRouterConfigurator(func(e *gin.Engine, base *handlers.BaseAPIHandler, _ *config.Config) {
			e.POST(routePath, p.route(base))
		})).
		WithHooks(cliproxy.Hooks{OnAfterStart: func(*cliproxy.Service) { close(ready) }}).
		Build()
	if err != nil {
		return nil, err
	}
	p.core.SetRoundTripperProvider(p)

	watcherStarted := expectWatcherStart()
	ctx, cancel := context.WithCancel(context.Background())
	p.stop = cancel
	go func() { p.runErr <- svc.Run(ctx) }()
	select {
	case <-ready:
	case err := <-p.runErr:
		return nil, fmt.Errorf("service exited before start: %w", err)
	case <-time.After(15 * time.Second):
		cancel()
		return nil, errors.New("service did not start")
	}
	select {
	case <-watcherStarted:
	case <-time.After(15 * time.Second):
		p.Close()
		return nil, errors.New("service watcher did not start")
	}
	// Accounts are added after the watcher starts, one registration at a time,
	// because the pinned SDK races when startup configuration apply or model
	// registration overlaps the auth update queue or a request (see
	// CONTRACT.md, "SDK data races").
	for _, name := range opts.Accounts {
		if err := p.WriteAccount(name, "sk-ant-oat01-probe-"+name+"-"+randomHex(8)); err != nil {
			p.Close()
			return nil, err
		}
		if err := p.WaitAccount(name); err != nil {
			p.Close()
			return nil, err
		}
	}
	return p, nil
}

// WaitQuiet blocks until no account has changed for quietFor. It is a test
// heuristic that lowers the odds of overlapping SDK background registration;
// it does not synchronize with the SDK and is not a production control.
func (p *Probe) WaitQuiet() error {
	const quietFor = 250 * time.Millisecond
	deadline := time.Now().Add(15 * time.Second)
	last, stableSince := "", time.Now()
	for time.Now().Before(deadline) {
		var b strings.Builder
		for _, a := range p.core.List() {
			fmt.Fprintf(&b, "%s:%d:%d;", a.ID, a.Generation, a.UpdatedAt.UnixNano())
		}
		if cur := b.String(); cur != last {
			last, stableSince = cur, time.Now()
		} else if time.Since(stableSince) >= quietFor {
			return nil
		}
		time.Sleep(25 * time.Millisecond)
	}
	return errors.New("SDK account state did not settle")
}

// RoundTripperFor routes every SDK upstream request to the fake API.
func (p *Probe) RoundTripperFor(*coreauth.Auth) http.RoundTripper { return p.Upstream }

// Manager exposes the SDK auth manager for lifecycle probes.
func (p *Probe) Manager() *coreauth.Manager { return p.core }

// WriteSettings writes config.yaml. The service watcher applies changes.
func (p *Probe) WriteSettings(s Settings) error {
	if s.Strategy == "" {
		s.Strategy = "fill-first"
	}
	yaml := fmt.Sprintf(`config-version: 8
server:
  host: "127.0.0.1"
  port: %d
access:
  api-keys: []
oauth:
  auth-dir: %q
routing:
  strategy: %q
  session-affinity: %t
  retry:
    request-retry: %d
    max-retry-credentials: 0
    max-retry-interval: %d
  cooldown:
    disable-cooling: %t
requests:
  passthrough-headers: false
  streaming:
    bootstrap-retries: %d
observability:
  logs:
    debug: false
    request-log: false
  usage:
    usage-statistics-enabled: false
`, p.port, p.authDir, s.Strategy, s.SessionAffinity, s.RequestRetry, s.MaxRetryInterval, s.DisableCooling, s.BootstrapRetries)
	return writeFileAtomic(p.cfgPath, []byte(yaml), 0o600)
}

// WriteAccount writes a fake OAuth credential file for the account. Rewriting
// an existing account models a rotated access token.
func (p *Probe) WriteAccount(name, token string) error {
	p.Upstream.Bind(token, name)
	payload := map[string]any{
		"type":                 "claude",
		"email":                name + "@probe.invalid",
		"access_token":         token,
		"expired":              time.Now().Add(24 * time.Hour).UTC().Format(time.RFC3339),
		"skip_account_profile": true,
	}
	data, err := json.Marshal(payload)
	if err != nil {
		return err
	}
	return writeFileAtomic(filepath.Join(p.authDir, p.fileName(name)), data, 0o600)
}

func (p *Probe) fileName(account string) string {
	return account + "-" + p.instance + ".json"
}

func writeFileAtomic(path string, data []byte, mode os.FileMode) error {
	tmp := path + ".tmp-" + randomHex(4)
	if err := os.WriteFile(tmp, data, mode); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

func (p *Probe) waitAccount(name string, timeout time.Duration) error {
	deadline := time.Now().Add(timeout)
	for time.Now().Before(deadline) {
		for _, a := range p.core.List() {
			if a == nil || a.Disabled {
				continue
			}
			if filepath.Base(a.FileName) != p.fileName(name) && filepath.Base(a.ID) != p.fileName(name) {
				continue
			}
			if cliproxy.GlobalModelRegistry().ClientSupportsModel(a.ID, Model) {
				p.mu.Lock()
				p.ids[name] = a.ID
				p.names[a.ID] = name
				p.mu.Unlock()
				return nil
			}
		}
		time.Sleep(20 * time.Millisecond)
	}
	return fmt.Errorf("account %s not registered", name)
}

// RegisteredAuthIDs lists the SDK auth IDs currently registered from an
// account's credential file.
func (p *Probe) RegisteredAuthIDs(name string) []string {
	var ids []string
	for _, a := range p.core.List() {
		if a != nil && filepath.Base(a.FileName) == p.fileName(name) {
			ids = append(ids, a.ID)
		}
	}
	return ids
}

// WaitAccount blocks until a newly written account is registered.
func (p *Probe) WaitAccount(name string) error {
	if err := p.waitAccount(name, 10*time.Second); err != nil {
		return err
	}
	return p.WaitQuiet()
}

// AuthID returns the SDK auth ID of an account.
func (p *Probe) AuthID(name string) string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.ids[name]
}

// QuotaSignals returns the passive header snapshot the SDK keeps for an
// account and for the probe model.
func (p *Probe) QuotaSignals(name string) (account, model map[string]string) {
	a, ok := p.core.GetByID(p.AuthID(name))
	if !ok || a == nil {
		return nil, nil
	}
	account = a.Quota.Signals
	if st := a.ModelStates[Model]; st != nil {
		model = st.Quota.Signals
	}
	return account, model
}

// AccountTokenHash returns a hash prefix of the access token the SDK
// currently holds for an account.
func (p *Probe) AccountTokenHash(name string) string {
	a, ok := p.core.GetByID(p.AuthID(name))
	if !ok || a == nil {
		return ""
	}
	token, _ := a.Metadata["access_token"].(string)
	return tokenHash(token)
}

// Results returns the SDK results observed so far.
func (p *Probe) Results() []Result {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]Result(nil), p.results...)
}

func (p *Probe) observe(_ context.Context, r coreauth.Result) coreauth.Result {
	rec := Result{AuthID: r.AuthID, Success: r.Success, CredentialScope: r.CredentialScope}
	if r.Error != nil {
		rec.HTTPStatus = r.Error.HTTPStatus
		rec.Code = r.Error.Code
	}
	if r.RetryAfter != nil {
		rec.RetryAfterMS = r.RetryAfter.Milliseconds()
	}
	p.mu.Lock()
	rec.Account = p.names[r.AuthID]
	p.results = append(p.results, rec)
	p.mu.Unlock()
	return r
}

func (p *Probe) route(base *handlers.BaseAPIHandler) gin.HandlerFunc {
	api := claudehandlers.NewClaudeCodeAPIHandler(base)
	return func(c *gin.Context) {
		body, err := io.ReadAll(c.Request.Body)
		if err != nil {
			c.Status(http.StatusBadRequest)
			return
		}
		call := c.GetHeader(headerCall)
		ctx, cancel := base.GetContextWithCancel(api, c, context.Background())
		pinned := p.AuthID(c.GetHeader(headerAccount))
		if pinned == "" {
			pinned = c.GetHeader(headerRawPin)
		}
		if pinned != "" {
			ctx = handlers.WithPinnedAuthID(ctx, pinned)
		}
		ctx = WithCall(ctx, call)
		callStart := time.Now()
		ctx = handlers.WithSelectedAuthIDCallback(ctx, func(id string) {
			p.mu.Lock()
			p.selected[call] = append(p.selected[call], p.names[id])
			p.mu.Unlock()
		})
		data, _, errs := base.ExecuteStreamWithAuthManager(ctx, "claude", Model, body, "")
		started := false
		for data != nil || errs != nil {
			select {
			case <-c.Request.Context().Done():
				cancel(c.Request.Context().Err())
				return
			case msg, ok := <-errs:
				if !ok {
					errs = nil
					continue
				}
				status := http.StatusBadGateway
				var cause error
				if msg != nil {
					if msg.StatusCode > 0 {
						status = msg.StatusCode
					}
					cause = msg.Error
					p.recordSignal(call, pinned, callStart, status, msg.Error)
				}
				payload := anthropicError(errorType(status), http.StatusText(status))
				if started {
					_, _ = fmt.Fprintf(c.Writer, "event: error\ndata: %s\n\n", payload)
					c.Writer.Flush()
				} else {
					c.Data(status, "application/json", []byte(payload))
				}
				cancel(cause)
				return
			case chunk, ok := <-data:
				if !ok {
					data = nil
					continue
				}
				if !started {
					c.Header("Content-Type", "text/event-stream")
					c.Status(http.StatusOK)
					started = true
				}
				_, _ = c.Writer.Write(chunk)
				c.Writer.Flush()
			}
		}
		cancel(nil)
	}
}

func errorType(status int) string {
	switch {
	case status == http.StatusUnauthorized:
		return "authentication_error"
	case status == http.StatusForbidden:
		return "permission_error"
	case status == http.StatusTooManyRequests:
		return "rate_limit_error"
	case status >= 500:
		return "api_error"
	default:
		return "invalid_request_error"
	}
}

// Send issues one streaming request, optionally pinned to an account, and
// returns the sanitized client view.
func (p *Probe) Send(ctx context.Context, call Call) Outcome {
	p.mu.Lock()
	p.seq++
	id := fmt.Sprintf("call-%d", p.seq)
	p.mu.Unlock()
	out := Outcome{Call: id, Pinned: call.Account}

	body := fmt.Sprintf(`{"model":%q,"max_tokens":16,"stream":true,"messages":[{"role":"user","content":"probe"}]}`, Model)
	reqCtx := ctx
	if call.Cancel > 0 {
		var cancel context.CancelFunc
		reqCtx, cancel = context.WithTimeout(ctx, call.Cancel)
		defer cancel()
	}
	req, err := http.NewRequestWithContext(reqCtx, http.MethodPost, p.baseURL+routePath, bytes.NewBufferString(body))
	if err != nil {
		out.ErrorType = err.Error()
		return out
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set(headerCall, id)
	if call.Account != "" {
		req.Header.Set(headerAccount, call.Account)
	}
	if call.RawPin != "" {
		req.Header.Set(headerRawPin, call.RawPin)
	}
	resp, err := p.client.Do(req)
	if err != nil {
		out.Canceled = errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled)
		out.ErrorType = "transport"
		out.Selected = p.selectedFor(id)
		return out
	}
	defer resp.Body.Close()
	out.Status = resp.StatusCode
	if resp.StatusCode != http.StatusOK {
		var e struct {
			Error struct {
				Type string `json:"type"`
			} `json:"error"`
		}
		data, _ := io.ReadAll(resp.Body)
		_ = json.Unmarshal(data, &e)
		out.ErrorType = e.Error.Type
		out.Selected = p.selectedFor(id)
		out.Signal = p.signalsFor(id)
		return out
	}
	sc := bufio.NewScanner(resp.Body)
	sawStop := false
	var event string
	for sc.Scan() {
		line := sc.Text()
		switch {
		case strings.HasPrefix(line, "event: "):
			event = strings.TrimPrefix(line, "event: ")
			out.Events = append(out.Events, event)
			if event == "message_stop" {
				sawStop = true
			}
		case strings.HasPrefix(line, "data: ") && event == "content_block_delta":
			var d struct {
				Delta struct {
					Text string `json:"text"`
				} `json:"delta"`
			}
			if json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &d) == nil {
				out.Text += d.Delta.Text
			}
		case strings.HasPrefix(line, "data: ") && event == "error":
			var e struct {
				Error struct {
					Type string `json:"type"`
				} `json:"error"`
			}
			if json.Unmarshal([]byte(strings.TrimPrefix(line, "data: ")), &e) == nil {
				out.ErrorType = e.Error.Type
			}
		}
	}
	if err := sc.Err(); err != nil {
		out.Canceled = errors.Is(err, context.DeadlineExceeded) || errors.Is(err, context.Canceled)
	}
	out.Incomplete = !sawStop
	out.Selected = p.selectedFor(id)
	return out
}

func (p *Probe) selectedFor(id string) []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	return append([]string{}, p.selected[id]...)
}

// ErrorSignal is what a route can learn about a failed call. Status, scope
// flags, and the fuzzed retry hint come from the SDK error. Headers come from
// the call's last upstream attempt, captured by the router-owned transport.
// Snapshot is the pinned account's passive header snapshot, kept to show that
// it can predate the call.
type ErrorSignal struct {
	Status             int               `json:"status"`
	CredentialScoped   bool              `json:"credential_scoped"`
	RequestScoped      bool              `json:"request_scoped"`
	RetryAfterMS       *int64            `json:"retry_after_ms"`
	Headers            map[string]string `json:"attempt_headers"`
	Snapshot           map[string]string `json:"snapshot_headers"`
	SnapshotObservedAt time.Time         `json:"snapshot_observed_at"`
	CallStartedAt      time.Time         `json:"call_started_at"`
}

// SnapshotFresh reports whether the passive snapshot was observed during
// this call. A stale snapshot describes an earlier response.
func (s ErrorSignal) SnapshotFresh() bool {
	return !s.SnapshotObservedAt.IsZero() && !s.SnapshotObservedAt.Before(s.CallStartedAt)
}

func (p *Probe) recordSignal(call, pinned string, started time.Time, status int, err error) {
	sig := &ErrorSignal{Status: status, Headers: map[string]string{}, CallStartedAt: started}
	var scoped interface{ IsCredentialScoped() bool }
	if errors.As(err, &scoped) {
		sig.CredentialScoped = scoped.IsCredentialScoped()
	}
	var requestScoped interface{ IsRequestScoped() bool }
	if errors.As(err, &requestScoped) {
		sig.RequestScoped = requestScoped.IsRequestScoped()
	}
	var retry interface{ RetryAfter() *time.Duration }
	if errors.As(err, &retry) {
		if d := retry.RetryAfter(); d != nil {
			ms := d.Milliseconds()
			sig.RetryAfterMS = &ms
		}
	}
	if attempts := p.Upstream.AttemptsFor(call); len(attempts) > 0 {
		for name, value := range attempts[len(attempts)-1].RateLimitHeaders {
			sig.Headers[name] = value
		}
	}
	if a, ok := p.core.GetByID(pinned); ok && a != nil {
		if st := a.ModelStates[Model]; st != nil {
			sig.Snapshot = st.Quota.Signals
			sig.SnapshotObservedAt = st.Quota.ObservedAt
		}
	}
	p.mu.Lock()
	p.signals[call] = sig
	p.mu.Unlock()
}

func (p *Probe) signalsFor(id string) *ErrorSignal {
	p.mu.Lock()
	defer p.mu.Unlock()
	return p.signals[id]
}

// Close stops the service.
func (p *Probe) Close() {
	if p.stop == nil {
		return
	}
	p.stop()
	select {
	case <-p.runErr:
	case <-time.After(35 * time.Second):
	}
	p.stop = nil
}
