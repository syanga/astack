//go:build lane2

// Lane PR3.live.2 runs the compiled service against a controlled loopback
// upstream under a sandbox that denies every non-loopback connection. Run:
//
//	CLAUDE_ROUTER_BIN=/path/to/claude-router CLAUDE_ROUTER_LANE_OUT=/path/controls.json \
//	  go test -tags lane2 -run TestLane2 -count=1 -v ./cmd/claude-router
package main

import (
	"bufio"
	"bytes"
	"crypto/rand"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"strings"
	"sync"
	"testing"
	"time"
)

const sandboxProfile = `(version 1)
(allow default)
(deny network-outbound)
(allow network-outbound (remote ip "localhost:*"))
(allow network-outbound (remote unix-socket))
`

func hash(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])[:12]
}

// upstream is the controlled Anthropic API: token to account, a set of
// revoked tokens, per-account settings answers, and per-account response
// headers for the next inference reply.
type upstream struct {
	mu       sync.Mutex
	accounts map[string]string
	revoked  map[string]bool
	overage  map[string]string
	headers  map[string]map[string]string
	log      []map[string]any
}

func (u *upstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	body, _ := io.ReadAll(r.Body)
	token := strings.TrimPrefix(r.Header.Get("Authorization"), "Bearer ")
	u.mu.Lock()
	account := u.accounts[hash(token)]
	revoked := u.revoked[hash(token)]
	setting := u.overage[account]
	hdr := u.headers[account]
	delete(u.headers, account)
	u.log = append(u.log, map[string]any{"path": r.URL.Path, "account": account, "token_sha256_prefix": hash(token), "revoked": revoked})
	u.mu.Unlock()
	w.Header().Set("Content-Type", "application/json")
	if account == "" || revoked {
		w.WriteHeader(401)
		io.WriteString(w, `{"type":"error","error":{"type":"authentication_error","message":"invalid token"}}`)
		return
	}
	switch r.URL.Path {
	case "/api/oauth/usage":
		switch setting {
		case "unknown":
			w.WriteHeader(404)
			io.WriteString(w, `{"type":"error","error":{"type":"not_found_error","message":"no usage"}}`)
		case "enabled":
			io.WriteString(w, `{"extra_usage":{"is_enabled":true}}`)
		default:
			io.WriteString(w, `{"extra_usage":{"is_enabled":false}}`)
		}
		return
	case "/v1/messages":
	default:
		w.WriteHeader(404)
		return
	}
	for k, v := range hdr {
		w.Header().Set(k, v)
	}
	var shape struct {
		Stream bool `json:"stream"`
	}
	_ = json.Unmarshal(body, &shape)
	usage := `"usage":{"input_tokens":12,"output_tokens":3,"cache_creation_input_tokens":100,"cache_read_input_tokens":2000}`
	if !shape.Stream {
		fmt.Fprintf(w, `{"id":"msg_lane","type":"message","role":"assistant","model":"claude-sonnet-4-5-20250929","content":[{"type":"text","text":"served-by:%s"}],"stop_reason":"end_turn","stop_sequence":null,%s}`, account, usage)
		return
	}
	w.Header().Set("Content-Type", "text/event-stream")
	w.WriteHeader(200)
	for _, ev := range []string{
		`event: message_start` + "\n" + `data: {"type":"message_start","message":{"id":"msg_lane","type":"message","role":"assistant","model":"claude-sonnet-4-5-20250929","content":[],"stop_reason":null,"stop_sequence":null,` + usage + `}}`,
		`event: content_block_start` + "\n" + `data: {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`,
		`event: content_block_delta` + "\n" + `data: {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"served-by:` + account + `"}}`,
		`event: content_block_stop` + "\n" + `data: {"type":"content_block_stop","index":0}`,
		`event: message_delta` + "\n" + `data: {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":null},"usage":{"output_tokens":3}}`,
		`event: message_stop` + "\n" + `data: {"type":"message_stop"}`,
	} {
		io.WriteString(w, ev+"\n\n")
		w.(http.Flusher).Flush()
	}
}

type lane struct {
	t       *testing.T
	bin     string
	dir     string
	cfgPath string
	token   string
	up      *upstream
	cmd     *exec.Cmd
	addr    string
	stderr  *bytes.Buffer
	stdout  *bytes.Buffer
	logs    []string
	tokens  map[string]string
	steps   []map[string]any
}

func (l *lane) writeCredential(account string) {
	l.t.Helper()
	b := make([]byte, 8)
	_, _ = rand.Read(b)
	tok := "sk-ant-oat01-lane-" + account + "-" + hex.EncodeToString(b)
	l.tokens[account] = tok
	l.up.mu.Lock()
	l.up.accounts[hash(tok)] = account
	l.up.mu.Unlock()
	payload, _ := json.Marshal(map[string]any{"type": "claude", "email": account + "@router.invalid", "access_token": tok, "expired": time.Now().Add(24 * time.Hour).UTC().Format(time.RFC3339), "skip_account_profile": true})
	if err := os.WriteFile(filepath.Join(l.dir, "auths", account+".json"), payload, 0o600); err != nil {
		l.t.Fatal(err)
	}
}

func (l *lane) writeConfig(capacityB float64) {
	l.t.Helper()
	cfg := map[string]any{
		"listen": "127.0.0.1:0", "state_dir": l.dir, "client_token_file": filepath.Join(l.dir, "client-token"),
		"accounts":      []map[string]any{{"id": "acct-a", "capacity": 1}, {"id": "acct-b", "capacity": capacityB}},
		"test_upstream": "", "overage_fresh_for": "30m", "overage_check_every": "10m",
	}
	cfg["test_upstream"] = os.Getenv("LANE_UPSTREAM")
	b, _ := json.MarshalIndent(cfg, "", "  ")
	if err := os.WriteFile(l.cfgPath, b, 0o600); err != nil {
		l.t.Fatal(err)
	}
}

var servingRE = regexp.MustCompile(`serving on http://(\S+)`)

func (l *lane) start() {
	l.t.Helper()
	profile := filepath.Join(l.dir, "sandbox.sb")
	_ = os.WriteFile(profile, []byte(sandboxProfile), 0o600)
	l.cmd = exec.Command("/usr/bin/sandbox-exec", "-f", profile, l.bin, "serve", "-config", l.cfgPath)
	l.cmd.Env = []string{"HOME=" + l.dir, "PATH=/usr/bin:/bin", "TMPDIR=" + l.dir + "/"}
	l.stderr = &bytes.Buffer{}
	pr, pw := io.Pipe()
	l.cmd.Stderr = io.MultiWriter(l.stderr, pw)
	l.stdout = &bytes.Buffer{}
	l.cmd.Stdout = l.stdout
	if err := l.cmd.Start(); err != nil {
		l.t.Fatal(err)
	}
	found := make(chan string, 1)
	go func() {
		sc := bufio.NewScanner(pr)
		for sc.Scan() {
			if m := servingRE.FindStringSubmatch(sc.Text()); m != nil {
				found <- m[1]
			}
		}
	}()
	select {
	case l.addr = <-found:
	case <-time.After(30 * time.Second):
		l.t.Fatalf("service did not start: %s", l.stderr.String())
	}
	l.step("start", map[string]any{"pid_started": true})
}

func (l *lane) stop() {
	l.t.Helper()
	_ = l.cmd.Process.Signal(os.Interrupt)
	done := make(chan error, 1)
	go func() { done <- l.cmd.Wait() }()
	select {
	case err := <-done:
		l.step("stop", map[string]any{"exit_error": fmt.Sprint(err)})
	case <-time.After(40 * time.Second):
		_ = l.cmd.Process.Kill()
		l.t.Fatal("service did not stop on SIGINT")
	}
	l.logs = append(l.logs, l.stderr.String(), l.stdout.String())
}

func (l *lane) step(name string, fields map[string]any) {
	fields["step"] = name
	l.steps = append(l.steps, fields)
}

type answer struct {
	Status int    `json:"status"`
	Text   string `json:"text,omitempty"`
	Error  string `json:"error_type,omitempty"`
	Retry  string `json:"x_should_retry,omitempty"`
	Msg    string `json:"message,omitempty"`
}

func (l *lane) send(name, token, session string, stream bool) answer {
	l.t.Helper()
	uid, _ := json.Marshal(map[string]string{"device_id": "dev", "account_uuid": "", "session_id": session})
	body, _ := json.Marshal(map[string]any{"model": "claude-sonnet-4-5-20250929", "max_tokens": 16, "stream": stream, "metadata": map[string]string{"user_id": string(uid)}, "messages": []map[string]any{{"role": "user", "content": "lane"}}})
	req, _ := http.NewRequest(http.MethodPost, "http://"+l.addr+"/v1/messages?beta=true", bytes.NewReader(body))
	if token != "" {
		req.Header.Set("X-Api-Key", token)
	}
	if session != "" {
		req.Header.Set("X-Claude-Code-Session-Id", session)
	}
	req.Header.Set("Content-Type", "application/json")
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		l.t.Fatalf("%s: %v", name, err)
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	a := answer{Status: resp.StatusCode, Retry: resp.Header.Get("X-Should-Retry")}
	if m := regexp.MustCompile(`served-by:([a-z-]+)`).FindSubmatch(data); m != nil {
		a.Text = string(m[1])
	}
	var e struct {
		Error struct {
			Type    string `json:"type"`
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal(data, &e) == nil {
		a.Error, a.Msg = e.Error.Type, e.Error.Message
	}
	l.step(name, map[string]any{"session_sha256_prefix": hash(session), "answer": a})
	return a
}

func TestLane2(t *testing.T) {
	bin, out := os.Getenv("CLAUDE_ROUTER_BIN"), os.Getenv("CLAUDE_ROUTER_LANE_OUT")
	if bin == "" || out == "" {
		t.Skip("set CLAUDE_ROUTER_BIN and CLAUDE_ROUTER_LANE_OUT")
	}
	up := &upstream{accounts: map[string]string{}, revoked: map[string]bool{}, overage: map[string]string{}, headers: map[string]map[string]string{}}
	srv := httptest.NewServer(up)
	defer srv.Close()
	os.Setenv("LANE_UPSTREAM", srv.URL)
	dir, err := os.MkdirTemp("", "pr3-lane2-")
	if err != nil {
		t.Fatal(err)
	}
	defer os.RemoveAll(dir)
	_ = os.MkdirAll(filepath.Join(dir, "auths"), 0o700)
	l := &lane{t: t, bin: bin, dir: dir, cfgPath: filepath.Join(dir, "config.json"), up: up, tokens: map[string]string{}}
	if err := exec.Command(bin, "token", "-out", filepath.Join(dir, "client-token")).Run(); err != nil {
		t.Fatal(err)
	}
	tb, _ := os.ReadFile(filepath.Join(dir, "client-token"))
	l.token = strings.TrimSpace(string(tb))
	l.writeCredential("acct-a")
	l.writeCredential("acct-b")
	l.writeConfig(1)
	controls := map[string]bool{}
	check := func(name string, ok bool) {
		controls[name] = ok
		if !ok {
			t.Errorf("control %s failed", name)
		}
	}
	c1, c2, c3, c4 := "11111111-0000-4000-8000-000000000001", "11111111-0000-4000-8000-000000000002", "11111111-0000-4000-8000-000000000003", "11111111-0000-4000-8000-000000000004"

	l.start()
	first := l.send("bind conversation 1", l.token, c1, true)
	home := first.Text
	check("valid_token_served", first.Status == 200 && home != "")
	bad := l.send("invalid client token", "client-"+strings.Repeat("0", 40), c1, true)
	none := l.send("missing client token", "", c1, true)
	check("invalid_client_auth_rejected", bad.Status == 401 && none.Status == 401 && bad.Retry == "false")
	noID := l.send("missing session identity", l.token, "", true)
	check("missing_identity_rejected", noID.Status == 400 && noID.Error == "invalid_request_error" && noID.Retry == "false")
	other := "acct-b"
	if home == "acct-b" {
		other = "acct-a"
	}

	// Unsafe settings: the other account reports overflow enabled at the next
	// start, so a new conversation can go only to the bound account.
	l.stop()
	up.mu.Lock()
	up.overage[other] = "enabled"
	up.mu.Unlock()
	l.start()
	resume := l.send("resume conversation 1 after restart", l.token, c1, false)
	check("restart_keeps_binding", resume.Status == 200 && resume.Text == home)
	newConv := l.send("new conversation with one unsafe account", l.token, c2, true)
	check("unsafe_account_not_placed", newConv.Status == 200 && newConv.Text == home)
	up.mu.Lock()
	up.overage[home] = "unknown"
	up.headers[home] = map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true", "anthropic-ratelimit-unified-representative-claim": "overage"}
	up.mu.Unlock()
	paid := l.send("response that shows paid use", l.token, c1, true)
	afterPaid := l.send("conversation 1 after paid use", l.token, c1, true)
	check("paid_use_stops_routing", paid.Status == 200 && afterPaid.Status == 503 && afterPaid.Retry == "false")

	// Credential renewal: the upstream revokes the access token. The router
	// never answers 401, keeps the binding, and the conversation resumes on
	// the same account once a renewed credential is installed by restart.
	l.stop()
	up.mu.Lock()
	up.overage[home], up.overage[other] = "disabled", "disabled"
	up.revoked[hash(l.tokens[home])] = true
	up.mu.Unlock()
	l.start()
	revoked := l.send("conversation 1 with a revoked access token", l.token, c1, true)
	check("expired_token_not_surfaced_as_401", revoked.Status == 503 && revoked.Retry == "false")
	l.stop()
	l.writeCredential(home)
	l.start()
	renewed := l.send("conversation 1 after renewal", l.token, c1, true)
	check("renewal_keeps_account", renewed.Status == 200 && renewed.Text == home)

	// Configuration change by restart: the other account gets five times the
	// capacity. Healthy bindings stay; a new conversation follows the new
	// capacities.
	l.stop()
	if home == "acct-a" {
		l.writeConfig(5)
	} else {
		l.writeConfig(0.2)
	}
	l.start()
	afterReload := l.send("conversation 1 after configuration change", l.token, c1, true)
	conv2 := l.send("conversation 2 after configuration change", l.token, c2, true)
	fresh := l.send("new conversation after configuration change", l.token, c3, true)
	check("config_change_keeps_bindings", afterReload.Text == home && conv2.Text == home)
	check("config_change_applies", fresh.Status == 200 && fresh.Text == other)

	req, _ := http.NewRequest(http.MethodGet, "http://"+l.addr+"/claude-router/status", nil)
	req.Header.Set("X-Api-Key", l.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	statusBody, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	_ = l.send("final request", l.token, c4, true)
	l.stop()

	events, _ := os.ReadFile(filepath.Join(dir, "events.jsonl"))
	journal, _ := os.ReadFile(filepath.Join(dir, "assignments", "assignments.jsonl"))
	outputs := map[string][]byte{"events": events, "status": statusBody, "service_logs": []byte(strings.Join(l.logs, "\n")), "journal": journal}
	leaks := map[string]bool{}
	for name, text := range outputs {
		leaked := bytes.Contains(text, []byte(l.token))
		for _, tok := range l.tokens {
			leaked = leaked || bytes.Contains(text, []byte(tok)) || bytes.Contains(text, []byte(tok[len(tok)-16:]))
		}
		leaks[name] = leaked
	}
	check("no_secret_in_outputs", !leaks["events"] && !leaks["status"] && !leaks["service_logs"] && !leaks["journal"])
	upstreamHosts := map[string]int{}
	up.mu.Lock()
	for _, rec := range up.log {
		upstreamHosts[rec["path"].(string)]++
	}
	upLog := up.log
	up.mu.Unlock()

	var cacheReads int
	for _, line := range bytes.Split(events, []byte("\n")) {
		if bytes.Contains(line, []byte(`"cache_read_input_tokens":2000`)) {
			cacheReads++
		}
	}
	check("cache_counters_recorded", cacheReads > 0)

	result := map[string]any{
		"probe":                          "PR3.live.2",
		"sandbox":                        "sandbox-exec deny network-outbound except localhost",
		"binary_sha256":                  fileHash(bin),
		"controls":                       controls,
		"secret_leaks":                   leaks,
		"steps":                          l.steps,
		"upstream_paths":                 upstreamHosts,
		"upstream_log":                   upLog,
		"events_with_cache_read_counter": cacheReads,
		"pass":                           !t.Failed(),
	}
	b, _ := json.MarshalIndent(result, "", "  ")
	if err := os.WriteFile(out, append(b, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}

func fileHash(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	sum := sha256.Sum256(b)
	return hex.EncodeToString(sum[:])
}
