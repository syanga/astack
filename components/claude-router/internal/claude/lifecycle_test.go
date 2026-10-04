package claude

import (
	"fmt"
	"os"
	"strconv"
	"sync"
	"testing"
)

// startRounds is how many times TestStartWithEnrolledAccountsUnderLoad
// restarts the service. CLAUDE_ROUTER_START_ROUNDS raises it for the RP-20
// evidence run.
func startRounds(t *testing.T) int {
	if v, err := strconv.Atoi(os.Getenv("CLAUDE_ROUTER_START_ROUNDS")); err == nil && v > 0 {
		return v
	}
	return 3
}

// TestStartWithEnrolledAccountsUnderLoad starts the service with enrolled
// accounts already on disk, then sends concurrent requests the moment Start
// returns, in each of several rounds on the same state. Under -race it is
// the probe for the SDK's startup registration race (RP-20). Each round
// also rewrites one credential before the restart, so a rotation applied by
// restart runs under the same load (RP-16).
func TestStartWithEnrolledAccountsUnderLoad(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b", "acct-c")
	rounds := startRounds(t)
	sessions := []string{sessionID(1), sessionID(2), sessionID(3), sessionID(4)}
	var bound map[string]string
	for round := 0; round < rounds; round++ {
		rotated := ""
		if round > 0 {
			e.svc.Close()
			rotated = e.writeCredential("acct-a")
		}
		e.start()
		var wg sync.WaitGroup
		results := make([][]result, len(sessions))
		for i, s := range sessions {
			wg.Add(1)
			go func(i int, s string) {
				defer wg.Done()
				for j := 0; j < 4; j++ {
					results[i] = append(results[i], e.send(msg{Session: s, NonStream: j%2 == 1}))
				}
			}(i, s)
		}
		wg.Wait()
		now := map[string]string{}
		for i, rs := range results {
			for _, r := range rs {
				if r.Status != 200 {
					t.Fatalf("round %d: session %d got %d %s", round, i, r.Status, r.ErrMsg)
				}
				if prev, ok := now[sessions[i]]; ok && prev != r.Text {
					t.Fatalf("round %d: session %d served by %s and %s", round, i, prev, r.Text)
				}
				now[sessions[i]] = r.Text
			}
		}
		if bound != nil && fmt.Sprint(bound) != fmt.Sprint(now) {
			t.Fatalf("round %d: bindings %v changed from %v across restart", round, now, bound)
		}
		bound = now
		if rotated != "" {
			for _, s := range e.upstream.inference() {
				if s.Account == "acct-a" && s.Token != rotated && s.At.After(e.svc.started) {
					t.Fatalf("round %d: acct-a used a stale credential after the restart", round)
				}
			}
		}
	}
}

// TestAccountFileAddedWhileServingIsNotLoaded shows the service has no file
// watcher: a credential added under a running service is not registered,
// so the account set changes only by restart (RP-10, RP-16).
func TestAccountFileAddedWhileServingIsNotLoaded(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.writeCredential("acct-x")
	e.send(msg{Session: sessionID(1)})

	for _, a := range e.svc.core.List() {
		if a != nil && baseName(a.FileName) == "acct-x.json" {
			t.Fatal("the SDK loaded a credential added while the service was running")
		}
	}
}
