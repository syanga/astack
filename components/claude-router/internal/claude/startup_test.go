package claude

import (
	"net"
	"strings"
	"testing"
	"time"
)

func TestFailedStartReleasesTheStateDirectory(t *testing.T) {
	e := newEnv(t, "acct-a")
	busy, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	e.cfg.Listen = busy.Addr().String()

	_, failed := Start(e.cfg, Options{Upstream: e.upstream})
	busy.Close()
	e.cfg.Listen = "127.0.0.1:0"
	e.start()
	r := e.send(msg{Session: sessionID(1)})

	if failed == nil {
		t.Fatal("start on an occupied port succeeded")
	}
	if r.Status != 200 {
		t.Fatalf("start after a failed start served %d, want 200", r.Status)
	}
}

func TestStartRefusesUnsafeSettings(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.OverageCheckEvery = Duration(-time.Minute)
	_, negative := Start(e.cfg, Options{Upstream: e.upstream})
	e.cfg.OverageCheckEvery = 0
	t.Setenv("MANAGEMENT_PASSWORD", " ")
	_, management := Start(e.cfg, Options{Upstream: e.upstream})

	if negative == nil || !strings.Contains(negative.Error(), "positive") {
		t.Fatalf("negative interval: %v, want a refusal", negative)
	}
	if management == nil || !strings.Contains(management.Error(), "MANAGEMENT_PASSWORD") {
		t.Fatalf("management password: %v, want a refusal", management)
	}
}

func TestDataOnlyStreamErrorBeforeOutputRetries(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{StreamError: "overloaded_error", DataOnly: true})

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-a") || len(e.upstream.inference()) != 2 {
		t.Fatalf("got %d %q after %d attempts, want 200 from acct-a after a retry", r.Status, r.Text, len(e.upstream.inference()))
	}
}
