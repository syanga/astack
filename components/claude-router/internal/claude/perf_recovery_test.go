//go:build perfrecover

// PR4.perf runs one controlled failure run against the service in this
// tree: 16 conversations on two accounts, then one exhaustion event with a
// five-second reset. The "partial" scenario exhausts acct-a only; the
// "total" scenario exhausts both accounts. Each conversation is a waiting
// client: it sleeps until the unified reset of a local 429, as Claude Code
// does with CLAUDE_CODE_RETRY_WATCHDOG=1, and sends again. The run writes
// its raw samples to CLAUDE_ROUTER_PERF_OUT/<CLAUDE_ROUTER_PERF_NAME>.json.
// The same file runs in the parent tree for the baseline. Run:
//
//	CLAUDE_ROUTER_PERF_OUT=DIR CLAUDE_ROUTER_PERF_NAME=head-1 \
//	  go test -tags perfrecover -run TestRecoveryPerf -count=1 -v ./internal/claude
package claude

import (
	"bytes"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

const (
	recoveryConversations = 16
	recoveryReset         = 5 * time.Second
	recoveryDeadline      = 10 * time.Second
	// knownAfter separates attempts the router dispatched before it could
	// have read an account's first rejection from attempts it dispatched
	// knowing it. Concurrent first requests reach the upstream within a few
	// milliseconds of each other, before the first 429 returns.
	knownAfter = 50 * time.Millisecond
)

// exhauster answers inference on an exhausted account with a 429 that
// reports a rejected five-hour window until the account's reset, and passes
// everything else to the fake upstream. It records every inference attempt.
type exhauster struct {
	inner *fakeUpstream
	mu    sync.Mutex
	until map[string]time.Time
	seen  []upstreamAttempt
}

type upstreamAttempt struct {
	Account  string  `json:"account"`
	StartMS  float64 `json:"start_ms"`
	EndMS    float64 `json:"end_ms"`
	Rejected bool    `json:"rejected"`
	start    time.Time
	end      time.Time
}

func (x *exhauster) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL.Path != "/v1/messages" {
		return x.inner.RoundTrip(req)
	}
	token := strings.TrimSpace(strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer "))
	x.inner.mu.Lock()
	account := x.inner.accounts[hashToken(token)]
	x.inner.mu.Unlock()
	start := time.Now()
	x.mu.Lock()
	until := x.until[account]
	x.mu.Unlock()
	var resp *http.Response
	var err error
	rejected := start.Before(until)
	if rejected {
		if req.Body != nil {
			_, _ = io.Copy(io.Discard, req.Body)
			req.Body.Close()
		}
		resp = respond(req, http.StatusTooManyRequests, exhaustedHeaders(until), errorBody("rate_limit_error", "five-hour limit reached"))
	} else {
		resp, err = x.inner.RoundTrip(req)
	}
	x.mu.Lock()
	x.seen = append(x.seen, upstreamAttempt{Account: account, Rejected: rejected, start: start, end: time.Now()})
	x.mu.Unlock()
	return resp, err
}

type conversationResult struct {
	Session      string  `json:"session"`
	Account      string  `json:"served_by"`
	Status       int     `json:"status"`
	Requests     int     `json:"client_requests"`
	Waited       bool    `json:"waited"`
	CompletedMS  float64 `json:"completed_ms"`
	ResumeMS     float64 `json:"resume_after_reset_ms,omitempty"`
	WithinBudget bool    `json:"within_deadline"`
}

type scenarioResult struct {
	Scenario               string               `json:"scenario"`
	ResetMS                float64              `json:"reset_ms"`
	Conversations          []conversationResult `json:"conversations"`
	Attempts               []upstreamAttempt    `json:"attempts"`
	KnownExhaustedAttempts int                  `json:"attempts_to_known_exhausted_before_reset"`
	HealthyMigrations      int                  `json:"healthy_migrations"`
	Migrations             int                  `json:"migrations"`
	AllWithinDeadline      bool                 `json:"all_within_deadline"`
}

// waitingClient sends one turn of a conversation and, on a local 429,
// sleeps until its unified reset (or its retry-after when the reset is
// absent) and sends again, until the deadline.
func waitingClient(e *env, session string, t0 time.Time) conversationResult {
	out := conversationResult{Session: session}
	deadline := t0.Add(recoveryDeadline)
	for time.Now().Before(deadline) {
		out.Requests++
		req, _ := http.NewRequest(http.MethodPost, "http://"+e.svc.Addr()+"/v1/messages?beta=true", bytes.NewReader(e.body(msg{Session: session})))
		req.Header.Set("X-Api-Key", e.token)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set(headerSessionID, session)
		r := readResult(e.t, req)
		out.Status = r.Status
		if r.Status == http.StatusOK {
			out.Account = strings.TrimPrefix(r.Text, "served-by:")
			out.CompletedMS = ms(time.Since(t0))
			out.WithinBudget = time.Now().Before(deadline)
			return out
		}
		if r.Status != http.StatusTooManyRequests {
			return out
		}
		out.Waited = true
		var wake time.Time
		if v, err := strconv.ParseInt(r.Header.Get("Anthropic-Ratelimit-Unified-Reset"), 10, 64); err == nil {
			wake = time.Unix(v, 0)
		} else if v, err := strconv.Atoi(r.Header.Get("Retry-After")); err == nil {
			wake = time.Now().Add(time.Duration(v) * time.Second)
		} else {
			return out
		}
		time.Sleep(time.Until(wake))
	}
	return out
}

func ms(d time.Duration) float64 { return float64(d.Microseconds()) / 1000 }

func runRecoveryScenario(t *testing.T, scenario string) scenarioResult {
	e := newEnv(t, "acct-a", "acct-b")
	x := &exhauster{inner: e.upstream, until: map[string]time.Time{}}
	startMu.Lock()
	svc, err := Start(e.cfg, Options{Upstream: x})
	startMu.Unlock()
	if err != nil {
		t.Fatalf("start: %v", err)
	}
	e.svc = svc
	defer svc.Close()

	sessions := make([]string, recoveryConversations)
	for i := range sessions {
		sessions[i] = sessionID(100 + i)
		if r := e.send(msg{Session: sessions[i]}); r.Status != http.StatusOK {
			t.Fatalf("placing %s: %d", sessions[i], r.Status)
		}
	}
	x.mu.Lock()
	x.seen = nil
	t0 := time.Now()
	reset := t0.Add(recoveryReset + time.Second - 1).Truncate(time.Second)
	x.until["acct-a"] = reset
	if scenario == "total" {
		x.until["acct-b"] = reset
	}
	x.mu.Unlock()
	eventsBefore := len(e.events())

	res := scenarioResult{Scenario: scenario, ResetMS: ms(reset.Sub(t0)), AllWithinDeadline: true}
	results := make([]conversationResult, len(sessions))
	var wg sync.WaitGroup
	for i, s := range sessions {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = waitingClient(e, s, t0)
		}()
	}
	wg.Wait()
	for i := range results {
		r := &results[i]
		if r.Waited && r.Status == http.StatusOK {
			r.ResumeMS = r.CompletedMS - res.ResetMS
		}
		res.AllWithinDeadline = res.AllWithinDeadline && r.WithinBudget
	}
	res.Conversations = results

	x.mu.Lock()
	firstRejection := map[string]time.Time{}
	for _, a := range x.seen {
		if a.Rejected {
			if f, ok := firstRejection[a.Account]; !ok || a.end.Before(f) {
				firstRejection[a.Account] = a.end
			}
		}
	}
	for _, a := range x.seen {
		a.StartMS, a.EndMS = ms(a.start.Sub(t0)), ms(a.end.Sub(t0))
		res.Attempts = append(res.Attempts, a)
		if f, ok := firstRejection[a.Account]; ok && a.start.After(f.Add(knownAfter)) && a.start.Before(x.until[a.Account]) {
			res.KnownExhaustedAttempts++
		}
	}
	x.mu.Unlock()
	for _, ev := range e.events()[eventsBefore:] {
		if ev.Kind != "migrated" {
			continue
		}
		res.Migrations++
		if f, ok := firstRejection[string(ev.From)]; !ok || ev.At.Before(f) {
			res.HealthyMigrations++
		}
	}
	return res
}

func TestRecoveryPerf(t *testing.T) {
	outDir, name := os.Getenv("CLAUDE_ROUTER_PERF_OUT"), os.Getenv("CLAUDE_ROUTER_PERF_NAME")
	if outDir == "" || name == "" {
		t.Skip("set CLAUDE_ROUTER_PERF_OUT and CLAUDE_ROUTER_PERF_NAME")
	}
	run := map[string]any{"name": name, "started": time.Now().UTC()}
	var scenarios []scenarioResult
	for _, s := range []string{"partial", "total"} {
		scenarios = append(scenarios, runRecoveryScenario(t, s))
	}
	run["scenarios"] = scenarios
	data, _ := json.MarshalIndent(run, "", "  ")
	if err := os.WriteFile(filepath.Join(outDir, name+".json"), data, 0o644); err != nil {
		t.Fatal(err)
	}
}
