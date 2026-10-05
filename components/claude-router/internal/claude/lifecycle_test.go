package claude

import (
	"fmt"
	"os"
	"strconv"
	"sync"
	"testing"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func startRounds(t *testing.T) int {
	if v, err := strconv.Atoi(os.Getenv("CLAUDE_ROUTER_START_ROUNDS")); err == nil && v > 0 {
		return v
	}
	return 3
}

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
		if ex, _ := e.svc.core.Executor("claude"); ex != coreauth.ProviderExecutor(e.svc.guard) {
			t.Fatalf("round %d: the refresh guard is not the SDK's Claude executor after start and load", round)
		}
		if n := e.eventCount("refresh_guard_replaced"); n != 0 {
			t.Fatalf("round %d: refresh_guard_replaced events %d, want 0", round, n)
		}
		if rotated != "" {
			for _, s := range e.upstream.inference() {
				if s.Account == "acct-a" && s.Token != rotated && s.At.After(e.svc.started) {
					t.Fatalf("round %d: acct-a used a stale credential after the restart", round)
				}
			}
		}
	}
}

func TestAccountFileAddedWhileServingIsNotLoaded(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.writeCredential("acct-x")
	e.send(msg{Session: sessionID(1)})
	whileServing := e.svc.authIDs["acct-x"] != "" || loaded(e.svc, "acct-x.json")

	e.svc.Close()
	e.cfg.Accounts = append(e.cfg.Accounts, router.Account{ID: "acct-x", Capacity: 1})
	e.start()
	afterRestart := loaded(e.svc, "acct-x.json")

	if whileServing {
		t.Fatal("the SDK loaded a credential added while the service was running")
	}
	if !afterRestart {
		t.Fatal("the restarted service did not load the enrolled credential")
	}
}

func loaded(s *Service, file string) bool {
	for _, a := range s.core.List() {
		if a != nil && baseName(a.FileName) == file {
			return true
		}
	}
	return false
}
