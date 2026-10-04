//go:build sdkreload

// The pinned SDK races on configuration hot reload: Service.applyConfigRuntime
// writes BaseAPIHandler fields that request handling reads without
// synchronization (internal/api/server_reload.go:171, sdk/api/handlers
// handlers.go:400 and :411). This probe is excluded from the default -race
// run; CONTRACT.md records its outcomes with and without the race detector.

package probes

import (
	"fmt"
	"reflect"
	"testing"
	"time"
)

func TestConfigHotReloadKeepsPinnedSelection(t *testing.T) {
	for _, settle := range []bool{true, false} {
		t.Run(fmt.Sprintf("settle=%v", settle), func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, Settings{Strategy: "fill-first", RequestRetry: 0, DisableCooling: true})

			control := tr.send("unpinned control under fill-first", Call{})
			first := tr.send("pinned request after initial apply", Call{Account: "acct-b"})

			before := fmt.Sprintf("%T", p.Manager().Selector())
			if err := p.WriteSettings(Settings{Strategy: "round-robin", SessionAffinity: true, RequestRetry: 3, MaxRetryInterval: 30, BootstrapRetries: 2}); err != nil {
				t.Fatal(err)
			}
			deadline := time.Now().Add(10 * time.Second)
			for fmt.Sprintf("%T", p.Manager().Selector()) == before {
				if time.Now().After(deadline) {
					t.Fatalf("SDK did not apply the reloaded routing configuration; selector still %s", before)
				}
				time.Sleep(20 * time.Millisecond)
			}
			if settle {
				if err := p.WaitQuiet(); err != nil {
					t.Fatal(err)
				}
			}
			tr.step("config reloaded", map[string]any{"selector_before": before, "selector_after": fmt.Sprintf("%T", p.Manager().Selector())})
			var afterReload []string
			for i := 0; i < 3; i++ {
				afterReload = append(afterReload, tr.send("pinned request after reload", Call{Account: "acct-b"}).Text)
			}

			if control.Text != "served-by:acct-a" {
				t.Fatalf("fill-first control served %q, want acct-a", control.Text)
			}
			if first.Text != "served-by:acct-b" {
				t.Fatalf("pinned request after apply served %q, want acct-b", first.Text)
			}
			want := []string{"served-by:acct-b", "served-by:acct-b", "served-by:acct-b"}
			if !reflect.DeepEqual(afterReload, want) {
				t.Fatalf("pinned requests after reload served %v, want %v", afterReload, want)
			}
			wantAttempts(t, p, "acct-a", "acct-b", "acct-b", "acct-b", "acct-b")
		})
	}
}
