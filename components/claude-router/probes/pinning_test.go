package probes

import (
	"fmt"
	"reflect"
	"testing"
	"time"
)

var twoAccounts = []string{"acct-a", "acct-b"}

var sdkDefaults = Settings{RequestRetry: 2, MaxRetryInterval: 30, BootstrapRetries: 1}

var pinnedExecutor = Settings{RequestRetry: 0, BootstrapRetries: 0, DisableCooling: true}

func wantAttempts(t *testing.T, p *Probe, want ...string) {
	t.Helper()
	if want == nil {
		want = []string{}
	}
	got := p.Upstream.AccountsAttempted()
	if !reflect.DeepEqual(got, want) {
		t.Fatalf("upstream attempts = %v, want %v", got, want)
	}
}

func TestPinnedRetriesStayOnSelectedAccount(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, Settings{RequestRetry: 2, BootstrapRetries: 1, DisableCooling: true})
	p.Upstream.Script("acct-a", Reply{Status: 503}, Reply{Status: 503})

	out := tr.send("pinned request after two transient failures", Call{Account: "acct-a"})

	if out.Status != 200 || out.Text != "served-by:acct-a" {
		t.Fatalf("client saw status %d text %q, want 200 from acct-a", out.Status, out.Text)
	}
	if n := len(p.Upstream.AttemptsFor(out.Call)); n != 3 {
		t.Fatalf("transport tied %d attempts to %s, want 3", n, out.Call)
	}
	if !reflect.DeepEqual(out.Selected, []string{"acct-a", "acct-a", "acct-a"}) {
		t.Fatalf("SDK selected %v, want acct-a three times, matching the transport", out.Selected)
	}
	wantAttempts(t, p, "acct-a", "acct-a", "acct-a")
}

func TestNonStreamingPinnedRetriesStayOnSelectedAccount(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, Settings{RequestRetry: 2, BootstrapRetries: 1, DisableCooling: true})
	p.Upstream.Script("acct-a", Reply{Status: 503}, Reply{Status: 503})

	out := tr.send("non-streaming pinned request after two transient failures", Call{Account: "acct-a", NonStream: true})

	if out.Status != 200 || out.Text != "served-by:acct-a" {
		t.Fatalf("client saw status %d text %q, want 200 from acct-a", out.Status, out.Text)
	}
	if n := len(p.Upstream.AttemptsFor(out.Call)); n != 3 {
		t.Fatalf("transport tied %d attempts to %s, want 3", n, out.Call)
	}
	wantAttempts(t, p, "acct-a", "acct-a", "acct-a")
}

func TestPinnedConnectionDropBeforeOutputRetriesSameAccount(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, Settings{RequestRetry: 2, BootstrapRetries: 1, DisableCooling: true})
	p.Upstream.Script("acct-a", Reply{Fail: "drop"})

	out := tr.send("pinned request whose first stream drops before any event", Call{Account: "acct-a"})

	if out.Status != 200 || out.Text != "served-by:acct-a" {
		t.Fatalf("client saw status %d text %q, want 200 from acct-a", out.Status, out.Text)
	}
	wantAttempts(t, p, "acct-a", "acct-a")
}

func TestUnpinnedRequestFailsOverToAnotherAccount(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, Settings{RequestRetry: 2, BootstrapRetries: 1, DisableCooling: true})
	p.Upstream.Script("acct-a", Reply{Status: 503})

	out := tr.send("unpinned request after one transient failure", Call{})

	if out.Text != "served-by:acct-b" {
		t.Fatalf("client text %q, want the stock SDK to fail over to acct-b", out.Text)
	}
	wantAttempts(t, p, "acct-a", "acct-b")
}

func TestBootstrapAuthErrorsDoNotFailOver(t *testing.T) {
	for _, status := range []int{401, 403} {
		t.Run(fmt.Sprint(status), func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, sdkDefaults)
			p.Upstream.Script("acct-a", Reply{Status: status})

			out := tr.send("pinned request rejected by upstream auth", Call{Account: "acct-a"})

			if out.Status != status {
				t.Fatalf("client status %d, want %d", out.Status, status)
			}
			wantAttempts(t, p, "acct-a")
		})
	}
}

func TestPinToUnknownAuthSelectsNothing(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, sdkDefaults)

	out := tr.send("request pinned to an auth ID the SDK does not hold", Call{RawPin: "acct-missing.json"})

	if out.Status != 503 {
		t.Fatalf("client status %d text %q, want 503", out.Status, out.Text)
	}
	if len(out.Selected) != 0 {
		t.Fatalf("SDK selected %v for an unknown pin", out.Selected)
	}
	wantAttempts(t, p)
}

func TestPinnedExecutorPostureMakesOneAttemptPerCall(t *testing.T) {
	future := fmt.Sprint(time.Now().Add(3 * time.Hour).Unix())
	failures := []struct {
		name  string
		reply Reply
	}{
		{"503", Reply{Status: 503}},
		{"529", Reply{Status: 529}},
		{"429", Reply{Status: 429}},
		{"429-five-hour-rejected", Reply{Status: 429, Header: map[string]string{
			"Anthropic-Ratelimit-Unified-Status":    "rejected",
			"Anthropic-Ratelimit-Unified-5h-Status": "rejected",
			"Anthropic-Ratelimit-Unified-5h-Reset":  future,
		}}},
		{"401", Reply{Status: 401}},
		{"drop-before-output", Reply{Fail: "drop"}},
	}
	for _, f := range failures {
		t.Run(f.name, func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, pinnedExecutor)
			p.Upstream.Script("acct-a", f.reply)

			failed := tr.send("first pinned call fails", Call{Account: "acct-a"})
			next := tr.send("router retries the same account", Call{Account: "acct-a"})

			if failed.Status == 200 {
				t.Fatalf("first call returned 200 with text %q, want a failure status the router can act on", failed.Text)
			}
			if n := len(p.Upstream.AttemptsFor(failed.Call)); n != 1 {
				t.Fatalf("transport tied %d attempts to the failed call, want 1", n)
			}
			if next.Status != 200 || next.Text != "served-by:acct-a" {
				t.Fatalf("router retry saw status %d text %q, want 200 from acct-a", next.Status, next.Text)
			}
			wantAttempts(t, p, "acct-a", "acct-a")
		})
	}
}

func TestSDKCooldownBlocksPinnedAccountWithoutFailover(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, Settings{RequestRetry: 0, BootstrapRetries: 0, DisableCooling: false})
	p.Upstream.Script("acct-a", Reply{Status: 503})

	failed := tr.send("pinned call fails and the SDK starts a cooldown", Call{Account: "acct-a"})
	blocked := tr.send("next pinned call during the SDK cooldown", Call{Account: "acct-a"})

	if failed.Status != 503 || !reflect.DeepEqual(failed.Selected, []string{"acct-a"}) {
		t.Fatalf("upstream failure: status %d selected %v, want 503 with one acct-a callback", failed.Status, failed.Selected)
	}
	if blocked.Status != 503 || len(blocked.Selected) != 0 {
		t.Fatalf("call during cooldown: status %d selected %v, want 503 with no callback", blocked.Status, blocked.Selected)
	}
	if n := len(p.Upstream.AttemptsFor(blocked.Call)); n != 0 {
		t.Fatalf("transport tied %d attempts to the blocked call, want 0", n)
	}
	wantAttempts(t, p, "acct-a")
}
