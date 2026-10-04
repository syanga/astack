package probes

import (
	"reflect"
	"testing"
	"time"
)

func TestNoAutomaticRetryAfterPartialOutput(t *testing.T) {
	for _, fail := range []string{"drop", "overloaded"} {
		t.Run(fail, func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, sdkDefaults)
			p.Upstream.Script("acct-a", Reply{PartialEvents: 3, Fail: fail})

			out := tr.send("pinned stream fails after three events", Call{Account: "acct-a"})

			wantEvents := []string{"message_start", "content_block_start", "content_block_delta", "error"}
			if !reflect.DeepEqual(out.Events, wantEvents) {
				t.Fatalf("client events %v, want %v", out.Events, wantEvents)
			}
			if out.Text != "served-by:acct-a" || !out.Incomplete {
				t.Fatalf("client text %q incomplete=%v, want the partial delta and an incomplete stream", out.Text, out.Incomplete)
			}
			time.Sleep(300 * time.Millisecond)
			wantAttempts(t, p, "acct-a")
		})
	}
}

func TestInStreamOverloadBeforeOutputIsDeliveredNotRetried(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, sdkDefaults)
	p.Upstream.Script("acct-a", Reply{Fail: "overloaded"})

	out := tr.send("pinned stream whose first event is overloaded_error", Call{Account: "acct-a"})

	if !reflect.DeepEqual(out.Events, []string{"error"}) || out.ErrorType != "overloaded_error" {
		t.Fatalf("client events %v error %q, want one overloaded_error event", out.Events, out.ErrorType)
	}
	time.Sleep(300 * time.Millisecond)
	wantAttempts(t, p, "acct-a")
}

func TestCancellationStopsUpstreamWork(t *testing.T) {
	cases := []struct {
		name   string
		events int
	}{{"before output", 0}, {"after output", 2}}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, sdkDefaults)
			p.Upstream.Script("acct-a", Reply{PartialEvents: c.events, Hold: true})
			const after = 300 * time.Millisecond

			start := time.Now()
			out := tr.send("client cancels a held pinned stream", Call{Account: "acct-a", Cancel: after})
			if !p.Upstream.WaitEnded(1, 5*time.Second) {
				t.Fatal("upstream attempt never observed cancellation")
			}
			time.Sleep(300 * time.Millisecond)

			attempts := p.Upstream.Attempts()
			stopLag := attempts[0].EndedAt.Sub(start.Add(after))
			tr.step("cancellation lag", map[string]any{"lag_ms": stopLag.Milliseconds()})
			if !out.Canceled {
				t.Fatalf("client outcome %+v, want a canceled request", out)
			}
			if attempts[0].Outcome != "canceled" || attempts[0].Events != c.events {
				t.Fatalf("upstream attempt %+v, want canceled after %d events", attempts[0], c.events)
			}
			if stopLag > time.Second {
				t.Fatalf("upstream stopped %v after the client canceled, want under 1s", stopLag)
			}
			wantAttempts(t, p, "acct-a")
		})
	}
}
