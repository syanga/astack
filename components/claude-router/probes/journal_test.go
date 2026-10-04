package probes

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"math/rand"
	"os"
	"os/exec"
	"strings"
	"sync"
	"syscall"
	"testing"
	"time"
)

const journalWriterEnv = "CLAUDE_ROUTER_JOURNAL_WRITER_DIR"

func TestJournalWriterProcess(t *testing.T) {
	dir := os.Getenv(journalWriterEnv)
	if dir == "" {
		t.Skip("child process for TestJournalKeepsAcknowledgedAssignmentsAcrossKill9")
	}
	j, err := OpenJournal(dir)
	if err != nil {
		fmt.Fprintf(os.Stdout, "OPEN-ERROR %v\n", err)
		os.Exit(3)
	}
	round := os.Getenv("CLAUDE_ROUTER_JOURNAL_ROUND")
	var out sync.Mutex
	fmt.Fprintln(os.Stdout, "READY")
	var wg sync.WaitGroup
	for w := 0; w < 8; w++ {
		wg.Add(1)
		go func(w int) {
			defer wg.Done()
			for i := 0; ; i++ {
				conv := fmt.Sprintf("r%s-c%d", round, i)
				account, err := j.Assign(conv, fmt.Sprintf("acct-%d", w))
				if err != nil {
					fmt.Fprintf(os.Stdout, "ASSIGN-ERROR %v\n", err)
					os.Exit(4)
				}
				out.Lock()
				fmt.Fprintf(os.Stdout, "ACK %s %s\n", conv, account)
				out.Unlock()
			}
		}(w)
	}
	wg.Wait()
}

func TestJournalKeepsAcknowledgedAssignmentsAcrossKill9(t *testing.T) {
	dir := t.TempDir()
	rng := rand.New(rand.NewSource(1))
	const rounds = 25
	totalAcks, contested := 0, 0
	for round := 0; round < rounds; round++ {
		cmd := exec.Command(os.Args[0], "-test.run=^TestJournalWriterProcess$")
		cmd.Env = append(os.Environ(), journalWriterEnv+"="+dir, fmt.Sprintf("CLAUDE_ROUTER_JOURNAL_ROUND=%d", round))
		stdout, err := cmd.StdoutPipe()
		if err != nil {
			t.Fatal(err)
		}
		if err := cmd.Start(); err != nil {
			t.Fatal(err)
		}
		var buf bytes.Buffer
		copied := make(chan struct{})
		ready := make(chan struct{})
		go func() {
			defer close(copied)
			sc := bufio.NewScanner(stdout)
			signaled := false
			for sc.Scan() {
				line := sc.Text()
				if line == "READY" && !signaled {
					signaled = true
					close(ready)
				}
				buf.WriteString(line + "\n")
			}
		}()
		select {
		case <-ready:
		case <-time.After(20 * time.Second):
			_ = cmd.Process.Kill()
			t.Fatalf("round %d: writer never became ready: %s", round, buf.String())
		}
		if _, err := OpenJournal(dir); !errors.Is(err, ErrJournalLocked) {
			t.Fatalf("round %d: second opener got %v, want ErrJournalLocked", round, err)
		}
		time.Sleep(time.Duration(5+rng.Intn(60)) * time.Millisecond)
		if err := cmd.Process.Signal(syscall.SIGKILL); err != nil {
			t.Fatal(err)
		}
		<-copied
		_ = cmd.Wait()
		if strings.Contains(buf.String(), "ERROR") {
			t.Fatalf("round %d: writer failed: %s", round, buf.String())
		}

		acks := ReadAcks(&buf)
		j, err := OpenJournal(dir)
		if err != nil {
			t.Fatalf("round %d: reopen after kill: %v", round, err)
		}
		for conv, accounts := range acks {
			for _, a := range accounts[1:] {
				if a != accounts[0] {
					t.Fatalf("round %d: conversation %s acknowledged as %v", round, conv, accounts)
				}
			}
			got, ok := j.Lookup(conv)
			if !ok || got != accounts[0] {
				t.Fatalf("round %d: conversation %s acknowledged as %s, recovered as %q (present=%v)", round, conv, accounts[0], got, ok)
			}
			totalAcks += len(accounts)
			if len(accounts) > 1 {
				contested++
			}
		}
		j.Close()
	}
	t.Logf("verified %d acknowledgments over %d kill -9 rounds; %d conversations had concurrent first requests", totalAcks, rounds, contested)
	if contested == 0 {
		t.Fatal("no conversation received concurrent first requests; the experiment did not exercise the race")
	}
}

func TestJournalTruncatesTornFinalRecord(t *testing.T) {
	dir := t.TempDir()
	j, err := OpenJournal(dir)
	if err != nil {
		t.Fatal(err)
	}
	j.Assign("conv-1", "acct-a")
	j.Assign("conv-2", "acct-b")
	j.Close()
	f, err := os.OpenFile(JournalPath(dir), os.O_WRONLY|os.O_APPEND, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	f.WriteString(`{"c":"conv-3","a":"ac`)
	f.Close()

	j, err = OpenJournal(dir)
	if err != nil {
		t.Fatalf("reopen with torn tail: %v", err)
	}
	_, tornPresent := j.Lookup("conv-3")
	got, _ := j.Assign("conv-3", "acct-c")
	j.Close()
	j, err = OpenJournal(dir)
	if err != nil {
		t.Fatal(err)
	}
	defer j.Close()

	if tornPresent {
		t.Fatal("torn record was recovered as an assignment")
	}
	if got != "acct-c" || j.Len() != 3 {
		t.Fatalf("after re-assigning conv-3: got %s, %d records; want acct-c and 3", got, j.Len())
	}
	a1, _ := j.Lookup("conv-1")
	a3, _ := j.Lookup("conv-3")
	if a1 != "acct-a" || a3 != "acct-c" {
		t.Fatalf("recovered conv-1=%s conv-3=%s, want acct-a and acct-c", a1, a3)
	}
}

func TestJournalRefusesCorruptInteriorRecord(t *testing.T) {
	dir := t.TempDir()
	content := "{\"c\":\"conv-1\",\"a\":\"acct-a\"}\nnot json\n{\"c\":\"conv-2\",\"a\":\"acct-b\"}\n"
	if err := os.WriteFile(JournalPath(dir), []byte(content), 0o600); err != nil {
		t.Fatal(err)
	}

	_, err := OpenJournal(dir)

	if err == nil || !strings.Contains(err.Error(), "corrupt journal record") {
		t.Fatalf("open = %v, want a corrupt-record error", err)
	}
}
