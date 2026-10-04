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
	}{
		{"transient 503", Reply{Status: 503}, 503, ClassTransient, false},
		{"overloaded 529", Reply{Status: 529, Body: anthropicError("overloaded_error", "Overloaded")}, 529, ClassTransient, false},
		{"generic 429", Reply{Status: 429, Body: anthropicError("rate_limit_error", "slow down")}, 429, ClassThrottle, false},
		{"429 with Retry-After only", Reply{Status: 429, Header: map[string]string{"Retry-After": "20"}}, 429, ClassThrottle, false},
		{"five-hour window rejected", Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":    "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status": "rejected",
			"Anthropic-Ratelimit-Unified-5h-Reset":  reset,
			"Anthropic-Ratelimit-Unified-7d-Status": "allowed",
			"Anthropic-Ratelimit-Unified-Reset":     reset,
		}}, 429, ClassExhausted, true},
		{"weekly window rejected", Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":    "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status": "allowed",
			"Anthropic-Ratelimit-Unified-7d-Status": "rejected",
			"Anthropic-Ratelimit-Unified-7d-Reset":  weekly,
			"Anthropic-Ratelimit-Unified-Reset":     weekly,
		}}, 429, ClassExhausted, true},
		{"overage-only rejection", Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":                  "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status":               "allowed",
			"Anthropic-Ratelimit-Unified-7d-Status":               "allowed",
			"Anthropic-Ratelimit-Unified-Overage-Status":          "rejected",
			"Anthropic-Ratelimit-Unified-Overage-Disabled-Reason": "org_level_disabled",
		}}, 429, ClassModelLimit, false},
		{"unauthorized", Reply{Status: 401, Body: anthropicError("authentication_error", "invalid")}, 401, ClassAuth, false},
		{"forbidden", Reply{Status: 403, Body: anthropicError("permission_error", "denied")}, 403, ClassAuth, false},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, pinnedExecutor)
			p.Upstream.Script("acct-a", c.reply)

			out := tr.send(c.name, Call{Account: "acct-a"})
			results := p.Results()
			accountSignals, modelSignals := p.QuotaSignals("acct-a")
			tr.step("sdk quota snapshot", map[string]any{"account_signals": accountSignals, "model_signals": modelSignals})
			t.Logf("signal=%+v account=%v model=%v results=%+v", out.Signal, accountSignals, modelSignals, results)

			if out.Status != c.status {
				t.Fatalf("client status %d, want %d", out.Status, c.status)
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
			wantAttempts(t, p, "acct-a")
		})
	}
}
