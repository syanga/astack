//go:build perf

// PR3.perf compares request latency through the parent's SDK probe
// (probes.Start, unchanged since PR2) with the head service, against the
// same in-process controlled upstream (probes.Upstream). It writes
// baseline.json before measuring the head, then perf.json. Run:
//
//	CLAUDE_ROUTER_PERF_OUT=DIR go test -tags perf -run TestPerf -count=1 -v ./internal/claude
package claude

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"sort"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
	"github.com/syanga/astack/components/claude-router/probes"
)

const (
	perfClients  = 16
	perfRequests = 1000
	perfWarmup   = 100
	perfBatch    = 100
)

// usageFront answers the settings read and passes inference to the shared
// probe upstream.
type usageFront struct{ up *probes.Upstream }

func (f usageFront) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL.Path == "/api/oauth/usage" {
		if req.Body != nil {
			req.Body.Close()
		}
		return respond(req, 200, nil, `{"extra_usage":{"is_enabled":false}}`), nil
	}
	return f.up.RoundTrip(req)
}

type sample struct {
	MS    float64 `json:"ms"`
	OK    bool    `json:"ok"`
	Which string  `json:"which"`
}

func run(n int, which string, do func(i int) bool) []sample {
	var next atomic.Int64
	out := make([]sample, n)
	var wg sync.WaitGroup
	for c := 0; c < perfClients; c++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				i := int(next.Add(1)) - 1
				if i >= n {
					return
				}
				start := time.Now()
				ok := do(i)
				out[i] = sample{MS: float64(time.Since(start).Microseconds()) / 1000, OK: ok, Which: which}
			}
		}()
	}
	wg.Wait()
	return out
}

type dist struct {
	N      int     `json:"n"`
	Failed int     `json:"failed"`
	P50    float64 `json:"p50_ms"`
	P95    float64 `json:"p95_ms"`
	P99    float64 `json:"p99_ms"`
	Max    float64 `json:"max_ms"`
	Mean   float64 `json:"mean_ms"`
}

func summarize(ss []sample) dist {
	var ms []float64
	d := dist{N: len(ss)}
	sum := 0.0
	for _, s := range ss {
		if !s.OK {
			d.Failed++
		}
		ms = append(ms, s.MS)
		sum += s.MS
	}
	sort.Float64s(ms)
	q := func(p float64) float64 { return ms[min(len(ms)-1, int(p*float64(len(ms))))] }
	d.P50, d.P95, d.P99, d.Max, d.Mean = q(0.50), q(0.95), q(0.99), ms[len(ms)-1], sum/float64(len(ms))
	return d
}

func upstreamTimes(up *probes.Upstream) dist {
	var ss []sample
	for _, a := range up.Attempts() {
		if !a.EndedAt.IsZero() {
			ss = append(ss, sample{MS: float64(a.EndedAt.Sub(a.StartedAt).Microseconds()) / 1000, OK: a.Status == 200})
		}
	}
	return summarize(ss)
}

func env_() map[string]any {
	host, _ := os.Hostname()
	return map[string]any{"go": runtime.Version(), "goos": runtime.GOOS, "goarch": runtime.GOARCH, "cpus": runtime.NumCPU(), "host_sha256_prefix": hashToken(host), "clients": perfClients, "race": raceEnabled}
}

func writeJSON(t *testing.T, path string, v any) {
	b, _ := json.MarshalIndent(v, "", "  ")
	if err := os.WriteFile(path, append(b, '\n'), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestPerf(t *testing.T) {
	outDir := os.Getenv("CLAUDE_ROUTER_PERF_OUT")
	if outDir == "" {
		t.Skip("set CLAUDE_ROUTER_PERF_OUT")
	}
	accounts := []string{"acct-a", "acct-b"}

	// Parent: the PR2 SDK probe with the single-attempt posture.
	parentDir := t.TempDir()
	parent, err := probes.Start(parentDir, probes.Options{Accounts: accounts, Settings: probes.Settings{Strategy: "fill-first", DisableCooling: true}})
	if err != nil {
		t.Fatal(err)
	}
	defer parent.Close()
	parentDo := func(i int) bool {
		o := parent.Send(context.Background(), probes.Call{Account: accounts[i%2]})
		return o.Status == 200 && strings.HasPrefix(o.Text, "served-by:")
	}

	// Head: the service, with its own probes.Upstream instance of the same
	// code and replies.
	e := newEnv(t)
	headUp := probes.NewUpstream()
	for _, a := range accounts {
		e.cfg.Accounts = append(e.cfg.Accounts, router.Account{ID: router.AccountID(a), Capacity: 1})
		tok := e.writeCredential(a)
		headUp.Bind(tok, a)
	}
	svc, err := Start(e.cfg, Options{Upstream: usageFront{up: headUp}})
	if err != nil {
		t.Fatal(err)
	}
	defer svc.Close()
	e.svc = svc
	client := &http.Client{Transport: &http.Transport{MaxIdleConnsPerHost: 64}, Timeout: 30 * time.Second}
	bodies := make([][]byte, perfClients)
	for c := range bodies {
		bodies[c] = e.body(msg{Session: sessionID(c + 1)})
	}
	headDo := func(i int) bool {
		c := i % perfClients
		req, _ := http.NewRequest(http.MethodPost, "http://"+svc.Addr()+"/v1/messages", bytes.NewReader(bodies[c]))
		req.Header.Set("X-Api-Key", e.token)
		req.Header.Set(headerSessionID, sessionID(c+1))
		req.Header.Set("Content-Type", "application/json")
		resp, err := client.Do(req)
		if err != nil {
			return false
		}
		data, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		return resp.StatusCode == 200 && bytes.Contains(data, []byte("message_stop"))
	}

	run(perfWarmup, "parent", parentDo)
	run(perfWarmup, "head", headDo)

	baselineSamples := run(perfRequests, "parent", parentDo)
	baseline := map[string]any{
		"probe": "PR3.perf baseline", "revision": "parent SDK probe (probes.Start)", "environment": env_(),
		"latency": summarize(baselineSamples), "upstream_response": upstreamTimes(parent.Upstream),
		"samples_ms": baselineSamples,
	}
	writeJSON(t, filepath.Join(outDir, "baseline.json"), baseline)

	var parentSamples, headSamples []sample
	for len(headSamples) < perfRequests {
		parentSamples = append(parentSamples, run(perfBatch, "parent", parentDo)...)
		headSamples = append(headSamples, run(perfBatch, "head", headDo)...)
	}
	pd, hd := summarize(parentSamples), summarize(headSamples)

	migrations := 0
	selections := map[string]int{}
	events, _ := os.ReadFile(filepath.Join(e.dir, "events.jsonl"))
	for _, line := range bytes.Split(events, []byte("\n")) {
		var ev Event
		if json.Unmarshal(line, &ev) != nil {
			continue
		}
		if ev.Kind == "migrated" {
			migrations++
		}
		if ev.Kind == "request" && ev.Status == 200 {
			selections[string(ev.Account)]++
		}
	}
	added := hd.P95 - pd.P95
	rule := map[string]any{
		"added_p95_ms":               added,
		"added_p95_budget_ms":        50,
		"head_p95_ms":                hd.P95,
		"head_p95_budget_ms":         200,
		"healthy_migrations":         migrations,
		"healthy_migrations_budget":  0,
		"upstream_p95_within_10ms":   upstreamTimes(headUp).P95 <= 10 && upstreamTimes(parent.Upstream).P95 <= 10,
		"pass":                       added <= 50 && hd.P95 <= 200 && migrations == 0 && pd.Failed == 0 && hd.Failed == 0,
	}
	writeJSON(t, filepath.Join(outDir, "perf.json"), map[string]any{
		"probe": "PR3.perf", "environment": env_(),
		"interleave":        map[string]any{"batch": perfBatch, "order": "parent then head, repeated", "warmup_each": perfWarmup},
		"parent":            pd,
		"head":              hd,
		"upstream_parent":   upstreamTimes(parent.Upstream),
		"upstream_head":     upstreamTimes(headUp),
		"head_selections":   selections,
		"rule":              rule,
		"parent_samples_ms": parentSamples,
		"head_samples_ms":   headSamples,
	})
	if rule["pass"] != true {
		t.Fatalf("perf rule failed: %+v", rule)
	}
}
