package claude

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	log "github.com/sirupsen/logrus"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

var sdkLogs = &lockedBuffer{}

func TestMain(m *testing.M) {
	log.SetOutput(sdkLogs)
	os.Exit(m.Run())
}

type lockedBuffer struct {
	mu sync.Mutex
	b  bytes.Buffer
}

func (l *lockedBuffer) Write(p []byte) (int, error) {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.Write(p)
}

func (l *lockedBuffer) String() string {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.b.String()
}

// clock is a settable test clock.
type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time {
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.t.IsZero() {
		return time.Now()
	}
	return c.t
}

func (c *clock) set(t time.Time) {
	c.mu.Lock()
	c.t = t
	c.mu.Unlock()
}

// env is one router state directory with its client token, fake
// credentials, and fake upstream, so a test can restart the service on the
// same state.
type env struct {
	t        *testing.T
	dir      string
	cfg      Config
	token    string
	tokens   map[string]string
	upstream *fakeUpstream
	clock    *clock
	svc      *Service
}

func newEnv(t *testing.T, accounts ...string) *env {
	t.Helper()
	dir, err := os.MkdirTemp("", "claude-router-test-")
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.RemoveAll(dir) })
	e := &env{t: t, dir: dir, token: "client-" + randomHex(24), tokens: map[string]string{}, upstream: newFakeUpstream(), clock: &clock{}}
	if err := os.MkdirAll(authDir(dir), 0o700); err != nil {
		t.Fatal(err)
	}
	tokenFile := filepath.Join(dir, "client-token")
	if err := os.WriteFile(tokenFile, []byte(e.token+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	e.cfg = Config{Listen: "127.0.0.1:0", StateDir: dir, ClientTokenFile: tokenFile}
	for _, a := range accounts {
		e.cfg.Accounts = append(e.cfg.Accounts, router.Account{ID: router.AccountID(a), Capacity: 1})
		e.writeCredential(a)
	}
	return e
}

// writeCredential writes a fake OAuth credential. Rewriting one models a
// rotated access token.
func (e *env) writeCredential(account string) string {
	e.t.Helper()
	token := "sk-ant-oat01-fake-" + account + "-" + randomHex(8)
	e.tokens[account] = token
	e.upstream.bind(token, account)
	payload, _ := json.Marshal(map[string]any{
		"type":                 "claude",
		"email":                account + "@router.invalid",
		"access_token":         token,
		"expired":              time.Now().Add(24 * time.Hour).UTC().Format(time.RFC3339),
		"skip_account_profile": true,
	})
	if err := writePrivate(credentialFile(e.dir, router.AccountID(account)), payload); err != nil {
		e.t.Fatal(err)
	}
	return token
}

func (e *env) start() *Service {
	e.t.Helper()
	svc, err := Start(e.cfg, Options{Upstream: e.upstream, Now: e.clock.now})
	if err != nil {
		e.t.Fatalf("start: %v", err)
	}
	e.svc = svc
	e.t.Cleanup(svc.Close)
	return svc
}

func (e *env) restart() *Service {
	e.t.Helper()
	e.svc.Close()
	return e.start()
}

// msg is one client request. Session is the x-claude-code-session-id
// header; MetaSession, when set, is the session in metadata.user_id.
type msg struct {
	Session     string
	MetaSession string
	NoSession   bool
	Agent       string
	Model       string
	NonStream   bool
	Token       string
	Body        []byte
}

// result is the client's view of one response.
type result struct {
	Status  int
	Header  http.Header
	Text    string
	Events  []string
	ErrType string
	ErrMsg  string
}

func (e *env) body(m msg) []byte {
	if m.Body != nil {
		return m.Body
	}
	meta := m.MetaSession
	if meta == "" {
		meta = m.Session
	}
	model := m.Model
	if model == "" {
		model = "claude-sonnet-4-5-20250929"
	}
	userID, _ := json.Marshal(map[string]string{"device_id": "dev-1", "account_uuid": "", "session_id": meta})
	b, _ := json.Marshal(map[string]any{
		"model": model, "max_tokens": 64, "stream": !m.NonStream,
		"metadata": map[string]string{"user_id": string(userID)},
		"messages": []map[string]any{{"role": "user", "content": "hello"}},
	})
	return b
}

func (e *env) send(m msg) result {
	e.t.Helper()
	return e.sendCtx(context.Background(), m)
}

func (e *env) sendCtx(ctx context.Context, m msg) result {
	e.t.Helper()
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, "http://"+e.svc.Addr()+"/v1/messages?beta=true", bytes.NewReader(e.body(m)))
	if err != nil {
		e.t.Fatal(err)
	}
	token := m.Token
	if token == "" {
		token = e.token
	}
	if token != "-" {
		req.Header.Set("X-Api-Key", token)
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Anthropic-Version", "2023-06-01")
	req.Header.Set("User-Agent", "claude-cli/2.1.285 (external, cli)")
	req.Header.Set("X-App", "cli")
	if !m.NoSession {
		req.Header.Set(headerSessionID, m.Session)
	}
	if m.Agent != "" {
		req.Header.Set(headerAgentID, m.Agent)
	}
	return readResult(e.t, req)
}

func readResult(t *testing.T, req *http.Request) result {
	t.Helper()
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return result{Status: -1, ErrMsg: err.Error()}
	}
	defer resp.Body.Close()
	out := result{Status: resp.StatusCode, Header: resp.Header}
	if strings.HasPrefix(resp.Header.Get("Content-Type"), "text/event-stream") {
		sc := bufio.NewScanner(resp.Body)
		var event string
		for sc.Scan() {
			line := sc.Text()
			switch {
			case strings.HasPrefix(line, "event: "):
				event = strings.TrimPrefix(line, "event: ")
				out.Events = append(out.Events, event)
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
				out.ErrType = streamErrorType([]byte(strings.TrimPrefix(line, "data: ")))
			}
		}
		return out
	}
	data, _ := io.ReadAll(resp.Body)
	var shape struct {
		Content []struct {
			Text string `json:"text"`
		} `json:"content"`
		Error struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"error"`
	}
	_ = json.Unmarshal(data, &shape)
	for _, c := range shape.Content {
		out.Text += c.Text
	}
	out.ErrType, out.ErrMsg = shape.Error.Type, shape.Error.Message
	return out
}

func (e *env) events() []Event {
	e.t.Helper()
	data, err := os.ReadFile(filepath.Join(e.dir, "events.jsonl"))
	if err != nil {
		e.t.Fatal(err)
	}
	var out []Event
	for _, line := range bytes.Split(bytes.TrimSpace(data), []byte("\n")) {
		var ev Event
		if json.Unmarshal(line, &ev) == nil {
			out = append(out, ev)
		}
	}
	return out
}

func (e *env) binding(session string) router.Binding {
	e.t.Helper()
	b, ok := e.svc.router.Lookup(router.ConversationID(session))
	if !ok {
		e.t.Fatalf("conversation %s has no binding", session)
	}
	return b
}

func served(account string) string { return "served-by:" + account }

func sessionID(n int) string { return fmt.Sprintf("00000000-0000-4000-8000-%012d", n) }
