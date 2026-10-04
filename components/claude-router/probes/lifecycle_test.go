package probes

import (
	"testing"
	"time"
)

func TestRotatedCredentialKeepsAccount(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, pinnedExecutor)
	authID := p.AuthID("acct-b")
	tr.send("pinned request before rotation", Call{Account: "acct-b"})

	rotated := "sk-ant-oat01-probe-acct-b-rotated-" + randomHex(8)
	if err := p.WriteAccount("acct-b", rotated); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(10 * time.Second)
	for p.AccountTokenHash("acct-b") != tokenHash(rotated) {
		if time.Now().After(deadline) {
			t.Fatal("SDK did not load the rotated credential")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if err := p.WaitQuiet(); err != nil {
		t.Fatal(err)
	}
	out := tr.send("pinned request after rotation", Call{Account: "acct-b"})

	if p.AuthID("acct-b") != authID {
		t.Fatalf("auth ID changed from %s to %s", authID, p.AuthID("acct-b"))
	}
	if out.Status != 200 || out.Text != "served-by:acct-b" {
		t.Fatalf("client saw status %d text %q, want 200 from acct-b", out.Status, out.Text)
	}
	attempts := p.Upstream.Attempts()
	if last := attempts[len(attempts)-1]; last.TokenHash != tokenHash(rotated) {
		t.Fatalf("upstream saw token %s, want the rotated token %s", last.TokenHash, tokenHash(rotated))
	}
	wantAttempts(t, p, "acct-b", "acct-b")
}

func TestConfigApplyKeepsPinnedSelection(t *testing.T) {
	configs := map[string]Settings{
		"fill-first":                {Strategy: "fill-first", RequestRetry: 3, BootstrapRetries: 2},
		"round-robin":               {Strategy: "round-robin", RequestRetry: 3, BootstrapRetries: 2},
		"session-affinity":          {Strategy: "fill-first", SessionAffinity: true, RequestRetry: 3, BootstrapRetries: 2},
		"pinned-executor-posture":   pinnedExecutor,
		"weighted-round-robin":      {Strategy: "weighted-round-robin", RequestRetry: 1},
		"cooling-disabled-defaults": {Strategy: "fill-first", DisableCooling: true, RequestRetry: 2, BootstrapRetries: 1},
	}
	for name, s := range configs {
		t.Run(name, func(t *testing.T) {
			p, tr := startProbe(t, twoAccounts, s)

			var served []string
			for i := 0; i < 3; i++ {
				served = append(served, tr.send("pinned request under applied configuration", Call{Account: "acct-b"}).Text)
			}

			for i, text := range served {
				if text != "served-by:acct-b" {
					t.Fatalf("pinned request %d served %q, want acct-b", i, text)
				}
			}
			wantAttempts(t, p, "acct-b", "acct-b", "acct-b")
		})
	}
}

func TestAccountReloadKeepsPinnedSelection(t *testing.T) {
	p, tr := startProbe(t, twoAccounts, Settings{Strategy: "fill-first", DisableCooling: true})
	tr.send("pinned request before the account set changes", Call{Account: "acct-b"})

	if err := p.WriteAccount("acct-c", "sk-ant-oat01-probe-acct-c-"+randomHex(8)); err != nil {
		t.Fatal(err)
	}
	if err := p.WaitAccount("acct-c"); err != nil {
		t.Fatal(err)
	}
	out := tr.send("pinned request after an account is added", Call{Account: "acct-b"})
	control := tr.send("unpinned control under fill-first", Call{})

	if out.Text != "served-by:acct-b" {
		t.Fatalf("pinned request after auth reload served %q, want acct-b", out.Text)
	}
	if control.Text == "served-by:acct-b" {
		t.Fatalf("unpinned control also chose acct-b, so the probe cannot distinguish pinning from the strategy")
	}
	wantAttempts(t, p, "acct-b", "acct-b", control.Text[len("served-by:"):])
}
