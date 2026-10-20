package probes

import (
	"context"
	"encoding/json"
	"io"
	"os"
	"path/filepath"
	"testing"

	log "github.com/sirupsen/logrus"
)

func TestMain(m *testing.M) {
	log.SetOutput(io.Discard)
	os.Exit(m.Run())
}

type transcript struct {
	t       *testing.T
	Test    string           `json:"test"`
	Sets    Settings         `json:"settings"`
	Steps   []map[string]any `json:"steps"`
	probe   *Probe
	Refused []string `json:"refused_hosts"`
}

func startProbe(t *testing.T, accounts []string, s Settings) (*Probe, *transcript) {
	t.Helper()
	p, err := Start(t.TempDir(), Options{Accounts: accounts, Settings: s})
	if err != nil {
		t.Fatalf("start probe: %v", err)
	}
	tr := &transcript{t: t, Test: t.Name(), Sets: s, probe: p}
	t.Cleanup(func() {
		p.Close()
		tr.Refused = p.Upstream.Refused()
		if len(tr.Refused) > 0 {
			t.Errorf("SDK attempted hosts other than the fake API: %v", tr.Refused)
		}
		tr.write()
	})
	return p, tr
}

func (tr *transcript) step(name string, fields map[string]any) {
	fields["step"] = name
	tr.Steps = append(tr.Steps, fields)
}

func (tr *transcript) send(name string, call Call) Outcome {
	before := len(tr.probe.Upstream.Attempts())
	out := tr.probe.Send(context.Background(), call)
	attempts := tr.probe.Upstream.Attempts()[before:]
	tr.step(name, map[string]any{"outcome": out, "upstream_attempts": attempts})
	return out
}

func (tr *transcript) write() {
	dir := os.Getenv("CLAUDE_ROUTER_PROBE_EVIDENCE")
	if dir == "" {
		return
	}
	if err := os.MkdirAll(dir, 0o755); err != nil {
		tr.t.Errorf("evidence dir: %v", err)
		return
	}
	tr.step("sdk_results", map[string]any{"results": tr.probe.Results()})
	data, err := json.MarshalIndent(tr, "", "  ")
	if err != nil {
		tr.t.Errorf("marshal transcript: %v", err)
		return
	}
	name := filepath.Join(dir, filepathSafe(tr.Test)+".json")
	if err := os.WriteFile(name, append(data, '\n'), 0o644); err != nil {
		tr.t.Errorf("write transcript: %v", err)
	}
}

func filepathSafe(s string) string {
	out := []rune(s)
	for i, r := range out {
		if r == '/' || r == ' ' {
			out[i] = '_'
		}
	}
	return string(out)
}

func attemptsByAccount(attempts []Attempt) map[string]int {
	counts := map[string]int{}
	for _, a := range attempts {
		counts[a.Account]++
	}
	return counts
}
