package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"math/rand/v2"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

// serveProc is a claude-router-sim serve process. With
// CLAUDE_ROUTER_SIM_BIN set it runs that binary; otherwise it re-executes
// this test binary as the simulator.
type serveProc struct {
	t      *testing.T
	cmd    *exec.Cmd
	stdin  io.WriteCloser
	mu     sync.Mutex
	lines  []string
	notify chan struct{}
	exited chan struct{}
}

func startServe(t *testing.T, state string, flags ...string) *serveProc {
	t.Helper()
	args := append([]string{"serve", "-state", state}, flags...)
	bin := os.Getenv("CLAUDE_ROUTER_SIM_BIN")
	var cmd *exec.Cmd
	if bin != "" {
		cmd = exec.Command(bin, args...)
	} else {
		cmd = exec.Command(os.Args[0], args...)
		cmd.Env = append(os.Environ(), "CLAUDE_ROUTER_SIM_AS_MAIN=1")
	}
	stdin, err := cmd.StdinPipe()
	if err != nil {
		t.Fatal(err)
	}
	stdout, err := cmd.StdoutPipe()
	if err != nil {
		t.Fatal(err)
	}
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	p := &serveProc{t: t, cmd: cmd, stdin: stdin, notify: make(chan struct{}, 1), exited: make(chan struct{})}
	go func() {
		sc := bufio.NewScanner(stdout)
		for sc.Scan() {
			p.mu.Lock()
			p.lines = append(p.lines, sc.Text())
			p.mu.Unlock()
			select {
			case p.notify <- struct{}{}:
			default:
			}
		}
		cmd.Wait()
		close(p.exited)
	}()
	t.Cleanup(func() { p.kill() })
	p.await("READY", 1)
	return p
}

func (p *serveProc) send(line string) {
	fmt.Fprintln(p.stdin, line)
}

func (p *serveProc) matching(prefix string) []string {
	p.mu.Lock()
	defer p.mu.Unlock()
	var out []string
	for _, l := range p.lines {
		if strings.HasPrefix(l, prefix) {
			out = append(out, l)
		}
	}
	return out
}

func (p *serveProc) await(prefix string, n int) []string {
	p.t.Helper()
	deadline := time.After(20 * time.Second)
	for {
		if got := p.matching(prefix); len(got) >= n {
			return got
		}
		if errs := p.matching("ERROR"); len(errs) > 0 {
			p.t.Fatalf("simulator error: %v", errs)
		}
		select {
		case <-p.notify:
		case <-p.exited:
			if got := p.matching(prefix); len(got) >= n {
				return got
			}
			p.t.Fatalf("simulator exited waiting for %d %q lines; output %v", n, prefix, p.matching(""))
		case <-deadline:
			p.t.Fatalf("timed out waiting for %d %q lines; output %v", n, prefix, p.matching(""))
		}
	}
}

func (p *serveProc) kill() {
	_ = p.cmd.Process.Signal(syscall.SIGKILL)
	<-p.exited
}

// awaitSelfKill waits for a process told to SIGKILL itself and reports
// whether it died by SIGKILL.
func (p *serveProc) awaitSelfKill() bool {
	select {
	case <-p.exited:
	case <-time.After(20 * time.Second):
		p.t.Fatal("simulator did not kill itself")
	}
	ws, ok := p.cmd.ProcessState.Sys().(syscall.WaitStatus)
	return ok && ws.Signaled() && ws.Signal() == syscall.SIGKILL
}

// ack is one acknowledged route: ACK conversation agent kind account from.
type ack struct {
	Conversation string `json:"conversation"`
	Agent        string `json:"agent"`
	Kind         string `json:"kind"`
	Account      string `json:"account"`
	From         string `json:"from"`
}

func parseAcks(lines []string) []ack {
	var out []ack
	for _, l := range lines {
		f := strings.Fields(l)
		if len(f) == 6 && f[0] == "ACK" {
			out = append(out, ack{Conversation: f[1], Agent: f[2], Kind: f[3], Account: f[4], From: f[5]})
		}
	}
	return out
}

func dump(p *serveProc) map[string]string {
	p.send("dump")
	p.await("END", 1)
	out := map[string]string{}
	for _, l := range p.matching("BINDING ") {
		f := strings.Fields(l)
		out[f[1]] = f[2]
	}
	return out
}

// Phase records one simulator process in the crash lane.
type Phase struct {
	Name     string            `json:"name"`
	Flags    []string          `json:"flags,omitempty"`
	Acks     []ack             `json:"acks"`
	Bindings map[string]string `json:"bindings_after,omitempty"`
	Killed   string            `json:"killed"`
	Checks   map[string]bool   `json:"checks"`
}

// Round records one random-kill round.
type Round struct {
	Round          int `json:"round"`
	KillAfterMs    int `json:"kill_after_ms"`
	Acks           int `json:"acks"`
	Conversations  int `json:"conversations_acknowledged"`
	Contested      int `json:"conversations_with_parallel_acks"`
	Lost           int `json:"acknowledged_but_not_recovered"`
	Conflicting    int `json:"conversations_with_conflicting_acks"`
	RecoveredExtra int `json:"recovered_without_ack"`
}

func check(t *testing.T, checks map[string]bool, name string, ok bool) {
	t.Helper()
	checks[name] = ok
	if !ok {
		t.Errorf("check failed: %s", name)
	}
}

func TestKilledSimulatorRecoversAcknowledgedAssignments(t *testing.T) {
	state := t.TempDir()
	var phases []Phase

	p1 := startServe(t, state)
	p1.send("overage 0 disabled")
	p1.send("burst 8 0 s-parent claude-sonnet-4-5")
	p1.send("route 0 s-two - - claude-sonnet-4-5")
	p1.send("route 0 s-three - - claude-sonnet-4-5")
	acks1 := parseAcks(p1.await("ACK", 10))
	p1.kill()
	ph := Phase{Name: "concurrent first requests, then SIGKILL", Acks: acks1, Killed: "SIGKILL from the driver after 10 ACKs", Checks: map[string]bool{}}
	parentAcks := 0
	for _, a := range acks1 {
		if a.Conversation == "s-parent" && a.Account == "acct-a" {
			parentAcks++
		}
	}
	check(t, ph.Checks, "all 8 parallel first requests for s-parent acknowledged acct-a", parentAcks == 8)
	phases = append(phases, ph)

	p2 := startServe(t, state, "-kill-before", "3")
	p2.send("overage 180 disabled")
	p2.send("route 180 s-parent agent-1 - claude-opus-4-5")
	p2.send("route 180 s-parent agent-2 agent-1 claude-opus-4-5")
	p2.send("report 181 acct-a claude-opus-4-5 exhausted five_hour:rejected:240")
	p2.send("route 181 s-parent - - claude-opus-4-5")
	acks2 := parseAcks(p2.await("ACK", 2))
	killed2 := p2.awaitSelfKill()
	ph = Phase{Name: "restart after 3 idle hours; children and a model change; SIGKILL before the migration commit", Flags: []string{"-kill-before", "3"}, Acks: acks2, Killed: fmt.Sprintf("self SIGKILL before route 3: %v", killed2), Checks: map[string]bool{}}
	check(t, ph.Checks, "process died by SIGKILL", killed2)
	check(t, ph.Checks, "child and nested child dispatched on acct-a after restart",
		len(acks2) == 2 && acks2[0].Kind == "dispatch" && acks2[0].Account == "acct-a" && acks2[1].Kind == "dispatch" && acks2[1].Account == "acct-a")
	phases = append(phases, ph)

	p3 := startServe(t, state, "-kill-after", "1")
	before := dump(p3)
	p3.send("overage 182 disabled")
	p3.send("report 182 acct-a claude-opus-4-5 exhausted five_hour:rejected:240")
	p3.send("route 182 s-parent - - claude-opus-4-5")
	killed3 := p3.awaitSelfKill()
	acks3 := parseAcks(p3.matching("ACK"))
	ph = Phase{Name: "restart; SIGKILL after the migration commit, before its ACK", Flags: []string{"-kill-after", "1"}, Acks: acks3, Bindings: before, Killed: fmt.Sprintf("self SIGKILL after route 1 committed: %v", killed3), Checks: map[string]bool{}}
	check(t, ph.Checks, "process died by SIGKILL", killed3)
	check(t, ph.Checks, "a crash before the migration commit left s-parent on acct-a", before["s-parent"] == "acct-a")
	check(t, ph.Checks, "acknowledged first assignments survived two kills",
		before["s-two"] == "acct-b" && before["s-three"] == "acct-c" && len(before) == 3)
	check(t, ph.Checks, "the migration was not acknowledged", len(acks3) == 0)
	phases = append(phases, ph)

	p4 := startServe(t, state)
	recovered := dump(p4)
	p4.send("overage 300 disabled")
	p4.send("observe 300 acct-a five_hour:0:320")
	p4.send("route 300 s-parent - - claude-opus-4-5")
	p4.send("route 300 s-parent agent-1 - claude-sonnet-4-5")
	p4.send("route 300 s-parent agent-3 agent-1 claude-sonnet-4-5")
	p4.send("route 300 s-new - - claude-sonnet-4-5")
	acks4 := parseAcks(p4.await("ACK", 4))
	ph = Phase{Name: "restart; acct-a recovered", Acks: acks4, Bindings: recovered, Killed: "SIGKILL from the driver at the end", Checks: map[string]bool{}}
	check(t, ph.Checks, "the committed but unacknowledged migration recovered as one assignment on acct-b", recovered["s-parent"] == "acct-b")
	check(t, ph.Checks, "the migrated conversation and its children stay on acct-b after acct-a recovers",
		len(acks4) == 4 && acks4[0].Account == "acct-b" && acks4[0].Kind == "dispatch" && acks4[1].Account == "acct-b" && acks4[2].Account == "acct-b")
	check(t, ph.Checks, "a new conversation goes to the recovered acct-a", len(acks4) == 4 && acks4[3].Kind == "place" && acks4[3].Account == "acct-a")
	p4.kill()
	phases = append(phases, ph)

	rounds := 8
	if os.Getenv("CLAUDE_ROUTER_LANE2_OUT") != "" {
		rounds = 25
	}
	rng := rand.New(rand.NewPCG(2, 0))
	var results []Round
	randomState := t.TempDir()
	for round := range rounds {
		p := startServe(t, randomState)
		p.send("overage 0 disabled")
		stop := make(chan struct{})
		go func() {
			for i := 0; ; i++ {
				select {
				case <-stop:
					return
				default:
				}
				if _, err := fmt.Fprintf(p.stdin, "burst 4 0 r%d-c%d claude-sonnet-4-5\n", round, i); err != nil {
					return
				}
			}
		}()
		wait := 5 + rng.IntN(60)
		time.Sleep(time.Duration(wait) * time.Millisecond)
		p.kill()
		close(stop)
		acks := parseAcks(p.matching("ACK"))
		q := startServe(t, randomState)
		got := dump(q)
		q.kill()
		res := Round{Round: round, KillAfterMs: wait, Acks: len(acks)}
		byConv := map[string][]string{}
		for _, a := range acks {
			byConv[a.Conversation] = append(byConv[a.Conversation], a.Account)
		}
		res.Conversations = len(byConv)
		for conv, accts := range byConv {
			if len(accts) > 1 {
				res.Contested++
			}
			for _, a := range accts {
				if a != accts[0] {
					res.Conflicting++
					break
				}
			}
			if got[conv] != accts[0] {
				res.Lost++
			}
		}
		prefix := fmt.Sprintf("r%d-", round)
		for conv := range got {
			if strings.HasPrefix(conv, prefix) && byConv[conv] == nil {
				res.RecoveredExtra++
			}
		}
		if res.Lost != 0 || res.Conflicting != 0 {
			t.Errorf("round %d: %d acknowledged assignments lost, %d conversations with conflicting acks", round, res.Lost, res.Conflicting)
		}
		results = append(results, res)
	}
	contested := 0
	for _, r := range results {
		contested += r.Contested
	}
	if contested == 0 {
		t.Error("no conversation received parallel acknowledged first requests; the random rounds did not exercise the race")
	}

	if out := os.Getenv("CLAUDE_ROUTER_LANE2_OUT"); out != "" {
		data, err := json.MarshalIndent(map[string]any{
			"probe":         "PR2.live.2",
			"accounts":      "acct-a:1,acct-b:1,acct-c:1 in tie-break order; default policy configuration",
			"binary":        os.Getenv("CLAUDE_ROUTER_SIM_BIN"),
			"phases":        phases,
			"random_rounds": results,
			"pass":          !t.Failed(),
		}, "", "  ")
		if err != nil {
			t.Fatal(err)
		}
		if err := os.WriteFile(out, append(data, '\n'), 0o644); err != nil {
			t.Fatal(err)
		}
	}
}
