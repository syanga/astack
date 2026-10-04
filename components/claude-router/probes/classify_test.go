package probes

import (
	"fmt"
	"testing"
	"time"
)

func TestFailureSignalsClassifyDistinctly(t *testing.T) {
	reset := fmt.Sprint(time.Now().Add(3 * time.Hour).Unix())
	weekly := fmt.Sprint(time.Now().Add(72 * time.Hour).Unix())
	cases := []struct {
		name            string
		reply           Reply
		status          int
		class           Class
		credentialScope bool
		resetHeader     string
		resetValue      string
	}{
		{name: "transient 503", reply: Reply{Status: 503}, status: 503, class: ClassTransient},
		{name: "overloaded 529", reply: Reply{Status: 529, Body: anthropicError("overloaded_error", "Overloaded")}, status: 529, class: ClassTransient},
		{name: "generic 429", reply: Reply{Status: 429, Body: anthropicError("rate_limit_error", "slow down")}, status: 429, class: ClassThrottle},
		{name: "429 with Retry-After only", reply: Reply{Status: 429, Header: map[string]string{"Retry-After": "20"}}, status: 429, class: ClassThrottle},
		{name: "five-hour window rejected", reply: Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":    "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status": "rejected",
			"Anthropic-Ratelimit-Unified-5h-Reset":  reset,
			"Anthropic-Ratelimit-Unified-7d-Status": "allowed",
			"Anthropic-Ratelimit-Unified-Reset":     reset,
		}}, status: 429, class: ClassExhausted, credentialScope: true, resetHeader: "Anthropic-Ratelimit-Unified-5h-Reset", resetValue: reset},
		{name: "weekly window rejected", reply: Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":    "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status": "allowed",
			"Anthropic-Ratelimit-Unified-7d-Status": "rejected",
			"Anthropic-Ratelimit-Unified-7d-Reset":  weekly,
			"Anthropic-Ratelimit-Unified-Reset":     weekly,
		}}, status: 429, class: ClassExhausted, credentialScope: true, resetHeader: "Anthropic-Ratelimit-Unified-7d-Reset", resetValue: weekly},
		{name: "overage-only rejection", reply: Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":                  "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status":               "allowed",
			"Anthropic-Ratelimit-Unified-7d-Status":               "allowed",
			"Anthropic-Ratelimit-Unified-Overage-Status":          "rejected",
			"Anthropic-Ratelimit-Unified-Overage-Disabled-Reason": "org_level_disabled",
		}}, status: 429, class: ClassModelLimit},
		{name: "unauthorized", reply: Reply{Status: 401, Body: anthropicError("authentication_error", "invalid")}, status: 401, class: ClassAuth},
		{name: "forbidden", reply: Reply{Status: 403, Body: anthropicError("permission_error", "denied")}, status: 403, class: ClassAuth},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, pinnedExecutor)
			p.Upstream.Script("acct-a", c.reply)

			out := tr.send(c.name, Call{Account: "acct-a"})
			results := p.Results()
			attempts := p.Upstream.AttemptsFor(out.Call)

			if out.Status != c.status {
				t.Fatalf("client status %d, want %d", out.Status, c.status)
			}
			if len(attempts) != 1 || attempts[0].Account != "acct-a" {
				t.Fatalf("transport saw attempts %+v for %s, want one on acct-a", attempts, out.Call)
			}
			if out.Signal == nil {
				t.Fatalf("route received no SDK error")
			}
			if got := Classify(*out.Signal); got != c.class {
				t.Fatalf("Classify(%+v) = %s, want %s", *out.Signal, got, c.class)
			}
			if len(results) != 1 || results[0].CredentialScope != c.credentialScope {
				t.Fatalf("SDK results %+v, want one result with credential scope %v", results, c.credentialScope)
			}
			if c.resetHeader != "" {
				if got := out.Signal.Headers[c.resetHeader]; got != c.resetValue {
					t.Fatalf("attempt header %s = %q, want the scripted reset %q", c.resetHeader, got, c.resetValue)
				}
				if got := out.Signal.Snapshot[c.resetHeader]; got != c.resetValue || !out.Signal.SnapshotFresh() {
					t.Fatalf("snapshot %s = %q fresh=%v, want the scripted reset %q from this call", c.resetHeader, got, out.Signal.SnapshotFresh(), c.resetValue)
				}
			}
			wantAttempts(t, p, "acct-a")
		})
	}
}

func TestQuotaSnapshotOutlivesAHeaderlessFailure(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, pinnedExecutor)
	reset := fmt.Sprint(time.Now().Add(3 * time.Hour).Unix())
	p.Upstream.Script("acct-a",
		Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":    "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status": "rejected",
			"Anthropic-Ratelimit-Unified-5h-Reset":  reset,
		}},
		Reply{Status: 429, Body: anthropicError("rate_limit_error", "slow down")},
	)

	tr.send("five-hour rejection", Call{Account: "acct-a"})
	time.Sleep(20 * time.Millisecond)
	out := tr.send("later headerless 429", Call{Account: "acct-a"})
	sig := *out.Signal
	fromSnapshot := Classify(ErrorSignal{Status: sig.Status, Headers: sig.Snapshot})
	tr.step("stale snapshot", map[string]any{"snapshot_fresh": sig.SnapshotFresh(), "class_from_attempt": Classify(sig), "class_from_snapshot": fromSnapshot})

	if sig.Snapshot["Anthropic-Ratelimit-Unified-5h-Status"] != "rejected" || sig.SnapshotFresh() {
		t.Fatalf("snapshot %v fresh=%v, want the earlier rejection left in place and marked stale", sig.Snapshot, sig.SnapshotFresh())
	}
	if fromSnapshot != ClassExhausted {
		t.Fatalf("classifying from the stale snapshot gave %s, want the exhausted misclassification it causes", fromSnapshot)
	}
	if got := Classify(sig); got != ClassThrottle {
		t.Fatalf("Classify from the attempt's own headers = %s, want throttle", got)
	}
}

func TestRequestScopedFailureClassifiesSeparately(t *testing.T) {
	sig := ErrorSignal{Status: 429, RequestScoped: true}

	got := Classify(sig)

	if got != ClassRequestScoped {
		t.Fatalf("Classify(%+v) = %s, want request_scoped", sig, got)
	}
}
