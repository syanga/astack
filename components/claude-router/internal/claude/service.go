package claude

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"time"

	"github.com/gin-gonic/gin"
	sdkaccess "github.com/router-for-me/CLIProxyAPI/v8/sdk/access"
	sdkapi "github.com/router-for-me/CLIProxyAPI/v8/sdk/api"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/api/handlers"
	sdkauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/auth"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy"
	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/config"
	log "github.com/sirupsen/logrus"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// Options holds what tests vary. The zero value is production.
type Options struct {
	// Upstream replaces the transport that reaches api.anthropic.com.
	Upstream http.RoundTripper
	// Now replaces the clock.
	Now func() time.Time
}

// Service is a running router: the embedded SDK, the routing policy, and the
// client-facing loopback server.
type Service struct {
	cfg       Config
	token     string
	now       func() time.Time
	router    *router.Router
	store     *router.Store
	events    *eventLog
	transport *transport
	overage   *overageReader
	core      *coreauth.Manager
	base      *handlers.BaseAPIHandler
	authIDs   map[router.AccountID]string

	sdkStop context.CancelFunc
	sdkDone chan error
	bgStop  context.CancelFunc
	bgDone  sync.WaitGroup
	server  *http.Server
	addr    string
	started time.Time
	seq     atomic.Int64
	closed  atomic.Bool
}

// Start validates the configuration and credentials, starts the SDK with the
// single-attempt executor posture, waits until every enrolled account is
// registered, reads each account's paid-overflow setting, and only then
// opens the client-facing listener.
func Start(cfg Config, opts Options) (s *Service, err error) {
	cfg = cfg.withDefaults()
	if err := cfg.validate(); err != nil {
		return nil, err
	}
	token, err := readClientToken(cfg.ClientTokenFile)
	if err != nil {
		return nil, err
	}
	if err := checkCredentials(cfg.StateDir, cfg.Accounts); err != nil {
		return nil, err
	}
	var redirect *url.URL
	if cfg.TestUpstream != "" {
		redirect, _ = url.Parse(cfg.TestUpstream)
	}
	inner := opts.Upstream
	if inner == nil {
		inner = &http.Transport{
			Proxy:               nil,
			ForceAttemptHTTP2:   true,
			MaxIdleConnsPerHost: 64,
			IdleConnTimeout:     90 * time.Second,
			TLSHandshakeTimeout: 10 * time.Second,
		}
	}
	now := opts.Now
	if now == nil {
		now = time.Now
	}

	s = &Service{cfg: cfg, token: token, now: now, authIDs: map[router.AccountID]string{}}
	defer func() {
		if err != nil {
			s.Close()
			s = nil
		}
	}()
	if s.events, err = openEventLog(filepath.Join(cfg.StateDir, "events.jsonl")); err != nil {
		return nil, err
	}
	if s.store, err = router.OpenStore(filepath.Join(cfg.StateDir, "assignments")); err != nil {
		return nil, err
	}
	policy := router.DefaultConfig()
	policy.OverageFreshFor = time.Duration(cfg.OverageFreshFor)
	if s.router, err = router.New(policy, cfg.Accounts, s.store); err != nil {
		return nil, err
	}
	s.overage = newOverageReader(s)
	s.transport = newTransport(inner, redirect, cfg.Accounts, now, s.observeHeaders)
	if redirect != nil {
		s.events.emit(Event{Kind: "test_upstream_in_use", Detail: redirect.Host})
	}
	if err = s.startSDK(); err != nil {
		return nil, err
	}

	readCtx, cancelReads := context.WithTimeout(context.Background(), 15*time.Second)
	var wg sync.WaitGroup
	for _, a := range cfg.Accounts {
		wg.Add(1)
		go func(id router.AccountID) {
			defer wg.Done()
			s.overage.read(readCtx, id)
		}(a.ID)
	}
	wg.Wait()
	cancelReads()

	bg, cancel := context.WithCancel(context.Background())
	s.bgStop = cancel
	s.bgDone.Add(1)
	go s.readOverageEvery(bg, time.Duration(cfg.OverageCheckEvery))

	ln, err := net.Listen("tcp", cfg.Listen)
	if err != nil {
		return nil, err
	}
	s.addr = ln.Addr().String()
	s.started = time.Now()
	s.server = &http.Server{Handler: s.handler(), ReadHeaderTimeout: 30 * time.Second}
	go func() { _ = s.server.Serve(ln) }()
	s.events.emit(Event{Kind: "started", Detail: fmt.Sprintf("%d accounts", len(cfg.Accounts))})
	return s, nil
}

// Addr is the client-facing address.
func (s *Service) Addr() string { return s.addr }

// Close stops serving, then stops the SDK and closes the store.
func (s *Service) Close() {
	if s == nil || !s.closed.CompareAndSwap(false, true) {
		return
	}
	if s.server != nil {
		ctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		_ = s.server.Shutdown(ctx)
		cancel()
	}
	if s.bgStop != nil {
		s.bgStop()
		s.bgDone.Wait()
	}
	if s.sdkStop != nil {
		s.sdkStop()
		select {
		case <-s.sdkDone:
		case <-time.After(35 * time.Second):
		}
	}
	if s.store != nil {
		_ = s.store.Close()
	}
	if s.events != nil {
		s.events.emit(Event{Kind: "stopped"})
		_ = s.events.close()
	}
}

func (s *Service) readOverageEvery(ctx context.Context, every time.Duration) {
	defer s.bgDone.Done()
	t := time.NewTicker(every)
	defer t.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-t.C:
			for _, a := range s.cfg.Accounts {
				s.overage.read(ctx, a.ID)
			}
		}
	}
}

// observeHeaders feeds every upstream response's quota and paid-overflow
// headers to the router as they arrive. Paid use stops new dispatch to the
// account before the response that showed it finishes.
func (s *Service) observeHeaders(account router.AccountID, h http.Header, at time.Time) {
	if obs, ok := observationFrom(h, at); ok {
		s.router.Observe(account, obs)
	}
	if state, ok := overageFrom(h); ok {
		s.router.ObserveOverage(account, state, at)
		s.overage.note(account, state, at, "response")
		if state == router.OveragePaidUse {
			s.events.emit(Event{At: at, Kind: "paid_use_observed", Account: account, Overage: state})
		}
	}
}

func randomHex(n int) string {
	b := make([]byte, n)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

// denyAll is the only access provider for the SDK's own server. Every stock
// route, including /v1/messages and count_tokens, answers 401: the stock
// handlers ignore the router's pin and would fail over between accounts.
type denyAll struct{}

func (denyAll) Identifier() string { return denyProvider }

func (denyAll) Authenticate(context.Context, *http.Request) (*sdkaccess.Result, *sdkaccess.AuthError) {
	return nil, sdkaccess.NewInvalidCredentialError()
}

const denyProvider = "claude-router-deny-all"

var registerDeny sync.Once

// watcherStarted is logged by Service.Run after the watcher step, right
// before the SDK's synchronous startup model registration
// (sdk/cliproxy/service_lifecycle.go:204).
const watcherStartedMessage = "file watcher started for config and auth directory changes"

type logMarker struct {
	mu      sync.Mutex
	waiters []chan struct{}
}

func (m *logMarker) Levels() []log.Level { return log.AllLevels }

func (m *logMarker) Fire(e *log.Entry) error {
	if !strings.HasPrefix(e.Message, watcherStartedMessage) {
		return nil
	}
	m.mu.Lock()
	defer m.mu.Unlock()
	for _, ch := range m.waiters {
		close(ch)
	}
	m.waiters = nil
	return nil
}

func (m *logMarker) wait() <-chan struct{} {
	ch := make(chan struct{})
	m.mu.Lock()
	m.waiters = append(m.waiters, ch)
	m.mu.Unlock()
	return ch
}

var (
	marker     = &logMarker{}
	markerOnce sync.Once
	sdkStartMu sync.Mutex
)

// startSDK writes the SDK configuration once and runs the service without a
// file watcher. Without a watcher the SDK never hot-reloads configuration
// (RP-10) and never applies a credential change while serving (RP-16);
// both take a restart. The SDK server listens on a private loopback port
// and refuses every request through denyAll.
func (s *Service) startSDK() error {
	sdkStartMu.Lock()
	defer sdkStartMu.Unlock()
	if !log.IsLevelEnabled(log.InfoLevel) {
		return errors.New("the SDK logger must enable Info entries: startup waits for one; send them to io.Discard instead")
	}
	registerDeny.Do(func() {
		sdkaccess.RegisterProvider(denyProvider, denyAll{})
		sdkaccess.SetExclusiveProvider(denyProvider)
	})
	markerOnce.Do(func() { log.AddHook(marker) })
	gin.SetMode(gin.ReleaseMode)

	port, err := freePort()
	if err != nil {
		return err
	}
	cfgPath := filepath.Join(s.cfg.StateDir, "sdk-config.yaml")
	if err := writePrivate(cfgPath, []byte(sdkConfigYAML(port, authDir(s.cfg.StateDir)))); err != nil {
		return err
	}
	cfg, err := config.LoadConfig(cfgPath)
	if err != nil {
		return err
	}
	if err := checkSDKPosture(cfg); err != nil {
		return err
	}
	store := sdkauth.NewFileTokenStore()
	store.SetBaseDir(authDir(s.cfg.StateDir))
	s.core = coreauth.NewManager(store, nil, nil)

	afterStart := make(chan struct{})
	svc, err := cliproxy.NewBuilder().
		WithConfig(cfg).
		WithConfigPath(cfgPath).
		WithCoreAuthManager(s.core).
		WithWatcherFactory(func(string, string, func(*config.Config)) (*cliproxy.WatcherWrapper, error) {
			return &cliproxy.WatcherWrapper{}, nil
		}).
		WithServerOptions(sdkapi.WithRouterConfigurator(func(_ *gin.Engine, base *handlers.BaseAPIHandler, _ *config.Config) {
			s.base = base
		})).
		WithHooks(cliproxy.Hooks{OnAfterStart: func(*cliproxy.Service) { close(afterStart) }}).
		Build()
	if err != nil {
		return err
	}
	s.core.SetRoundTripperProvider(s.transport)

	registered := marker.wait()
	ctx, cancel := context.WithCancel(context.Background())
	s.sdkStop = cancel
	s.sdkDone = make(chan error, 1)
	go func() { s.sdkDone <- svc.Run(ctx) }()
	for _, step := range []struct {
		name string
		ch   <-chan struct{}
	}{{"start", afterStart}, {"registration", registered}} {
		select {
		case <-step.ch:
		case err := <-s.sdkDone:
			s.sdkDone <- err
			return fmt.Errorf("SDK exited during %s: %v", step.name, err)
		case <-time.After(20 * time.Second):
			return fmt.Errorf("SDK %s did not complete", step.name)
		}
	}
	return s.waitRegistered()
}

// waitRegistered blocks until every enrolled account has an SDK auth with
// registered models and the SDK's account state has stopped changing. The
// SDK exposes no signal for the end of its startup model registration, so
// the quiet period is a heuristic (row RP-20).
func (s *Service) waitRegistered() error {
	const quietFor = 250 * time.Millisecond
	deadline := time.Now().Add(20 * time.Second)
	reg := cliproxy.GlobalModelRegistry()
	last, stableSince := "", time.Now()
	for time.Now().Before(deadline) {
		var b strings.Builder
		ids := map[router.AccountID]string{}
		for _, a := range s.core.List() {
			if a == nil {
				continue
			}
			account := s.transport.accounts[baseName(a.FileName)]
			if account == "" {
				account = s.transport.accounts[baseName(a.ID)]
			}
			if account == "" || a.Disabled || len(reg.GetModelsForClient(a.ID)) == 0 {
				continue
			}
			ids[account] = a.ID
			fmt.Fprintf(&b, "%s:%d:%d:%d;", a.ID, a.Generation, a.UpdatedAt.UnixNano(), reg.ClientRegistrationEpoch(a.ID))
		}
		cur := b.String()
		switch {
		case len(ids) < len(s.cfg.Accounts) || cur != last:
			last, stableSince = cur, time.Now()
		case time.Since(stableSince) >= quietFor:
			s.authIDs = ids
			return nil
		}
		time.Sleep(25 * time.Millisecond)
	}
	return errors.New("enrolled accounts did not register in the SDK")
}

func freePort() (int, error) {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port, nil
}

func writePrivate(path string, data []byte) error {
	tmp := path + ".tmp-" + randomHex(4)
	if err := os.WriteFile(tmp, data, 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, path)
}

// sdkConfigYAML is the single-attempt executor posture from CONTRACT.md:
// no SDK retries, no bootstrap retries, cooling and session affinity off,
// and no proxy at any level.
func sdkConfigYAML(port int, auths string) string {
	return fmt.Sprintf(`config-version: 8
server:
  host: "127.0.0.1"
  port: %d
access:
  api-keys: []
oauth:
  auth-dir: %q
routing:
  strategy: "fill-first"
  session-affinity: false
  retry:
    request-retry: 0
    max-retry-credentials: 1
    max-retry-interval: 0
  cooldown:
    disable-cooling: true
requests:
  passthrough-headers: false
  streaming:
    bootstrap-retries: 0
observability:
  logs:
    debug: false
    request-log: false
  usage:
    usage-statistics-enabled: false
`, port, auths)
}

// checkSDKPosture refuses an SDK configuration with a global proxy, which
// would bypass the router's transport and its attempt and paid-use evidence.
func checkSDKPosture(cfg *config.Config) error {
	if strings.TrimSpace(cfg.ProxyURL) != "" {
		return errors.New("SDK configuration sets a proxy; the router requires none at any level")
	}
	return nil
}
