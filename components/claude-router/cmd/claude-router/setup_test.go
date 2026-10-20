package main

import (
	"encoding/json"
	"errors"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"

	log "github.com/sirupsen/logrus"
)

const fakeHelper = `#!/bin/sh
echo "helper    $*"
`

func setupLane(t *testing.T) *lane {
	t.Helper()
	l := newLane(t, "launchd")
	python := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(python, []byte(fakeHelper), 0o755); err != nil {
		t.Fatal(err)
	}
	l.in.Python, l.in.Helper, l.in.Manifest = python, "/lib/claude_router.py", "/state/manifest.json"
	return l
}

func golden(t *testing.T, name string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join("testdata", "setup", name))
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestSetupSignsInEachAccountStartsTheRouterAndActivates(t *testing.T) {
	l := setupLane(t)

	var err error
	out := stdoutOf(t, func() { err = runSetup(l.in, []string{"work", "alumni"}, false, os.Stdout) })

	if err != nil {
		t.Fatal(err)
	}
	if got, want := l.shown(out), golden(t, "activated.txt"); got != want {
		t.Fatalf("setup output\n%s\nwant\n%s", got, want)
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1 alumni:1" {
		t.Fatalf("accounts %s, want work:1 alumni:1", got)
	}
}

func TestSetupRefusesWithTheAccountNameWhenPaidOverflowIsOn(t *testing.T) {
	l := setupLane(t)
	l.overage["alumni"] = "enabled"

	var err error
	out := stdoutOf(t, func() { err = runSetup(l.in, []string{"work", "alumni"}, false, os.Stdout) })

	var exit silentExit
	if !errors.As(err, &exit) || exit != 1 {
		t.Fatalf("setup: %v, want exit 1 after the refusal lines", err)
	}
	if got, want := l.shown(out), golden(t, "refused.txt"); got != want {
		t.Fatalf("setup output\n%s\nwant\n%s", got, want)
	}
}

func TestSetupRefusesWhenTheRouterIsAlreadyActivated(t *testing.T) {
	l := setupLane(t)
	journal, _ := json.Marshal(map[string]string{"phase": "active", "settings": "/home/.claude/settings.json"})
	l.write("activation.json", string(journal))
	before := l.read("config.json")

	err := runSetup(l.in, []string{"school"}, false, os.Stdout)

	var n nextError
	if !errors.As(err, &n) || n.err.Error() != "Claude Code already routes through the router" || n.next != "claude-router account add NAME" {
		t.Fatalf("setup when activated: %#v, want a refusal that names account add", err)
	}
	if l.read("config.json") != before || len(l.signed) != 0 {
		t.Fatalf("setup changed config.json or signed in %v before refusing", l.signed)
	}
}

func TestSetupLinesFitEightyColumnsForEightCharacterAccounts(t *testing.T) {
	l := setupLane(t)
	names := []string{"abcdefgh", "ijklmnop", "qrstuvwx"}
	for _, name := range names {
		l.overage[name] = "enabled"
	}

	out := stdoutOf(t, func() { _ = runSetup(l.in, names, true, os.Stdout) })

	if !strings.HasSuffix(out, `refuse    abcdefgh, ijklmnop, and qrstuvwx have paid overflow on
          Claude Code stays direct
next      turn off their paid overflow on claude.ai; claude-router setup
`) {
		t.Fatalf("output\n%s\nwant the refusal for all three accounts", out)
	}
	for _, line := range strings.Split(strings.TrimSpace(l.shown(out)), "\n") {
		if len(line) > 80 {
			t.Errorf("line of %d columns: %s", len(line), line)
		}
	}
}

func TestCopySignInURLsKeepsOnlyTheURLLines(t *testing.T) {
	sdk := `Opening browser for Claude authentication
To authenticate from a remote machine, an SSH tunnel may be required.
================================================================================
  Run one of the following commands on your local machine (NOT the server):

  # Standard SSH command (assumes SSH port 22):
  ssh -L 54545:127.0.0.1:54545 root@203.0.113.7 -p 22
================================================================================
Visit the following URL to continue authentication:
https://claude.ai/oauth/authorize?code=true&client_id=x&state=y
Waiting for Claude authentication callback...
Claude authentication successful
`
	var out strings.Builder

	copySignInURLs(&out, strings.NewReader(sdk))

	if out.String() != "https://claude.ai/oauth/authorize?code=true&client_id=x&state=y\n" {
		t.Fatalf("shown %q, want only the URL", out.String())
	}
}

func TestSetupKeepsTheAccountsSignedInBeforeAFailedSignIn(t *testing.T) {
	l := setupLane(t)
	l.refuse["alumni"] = true

	var err error
	out := stdoutOf(t, func() { err = runSetup(l.in, []string{"work", "alumni"}, false, os.Stdout) })

	if err == nil || !strings.Contains(err.Error(), "the sign-in was cancelled") {
		t.Fatalf("setup: %v, want the sign-in error", err)
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1" {
		t.Fatalf("accounts %s, want work:1, the account whose credential setup wrote", got)
	}
	if !strings.HasSuffix(out, "config    added work, capacity 1\n") {
		t.Fatalf("output\n%s\nwant it to end with the config line for work", out)
	}
}

func TestShowingOnlySignInURLsRestoresTheLogOutputItFound(t *testing.T) {
	var logged strings.Builder
	saved := log.StandardLogger().Out
	log.SetOutput(&logged)
	t.Cleanup(func() { log.SetOutput(saved) })

	stdoutOf(t, func() {
		_ = showingOnlySignInURLs(func() error {
			log.Info("during the sign-in")
			return nil
		})
	})
	log.Info("after the sign-in")

	if got := logged.String(); strings.Contains(got, "during the sign-in") || !strings.Contains(got, "after the sign-in") {
		t.Fatalf("log output %q, want only the line logged after the sign-in", got)
	}
}

func TestSetupRemovesTheNewLoginsWhenTheConfigWriteFails(t *testing.T) {
	l := setupLane(t)
	l.write("auths/work.json", "{}\n")
	l.write("config.json", strings.Replace(l.read("config.json"), `"accounts": []`, `"accounts": [{"id": "work", "capacity": 1}]`, 1))
	l.start()
	failingConfigEdit(t)

	var err error
	stdoutOf(t, func() { err = runSetup(l.in, []string{"alumni", "school"}, false, os.Stdout) })

	if err == nil || !strings.Contains(err.Error(), "disk full") {
		t.Fatalf("setup: %v, want the config write error", err)
	}
	if got := strings.Join(l.logins(), " "); got != "work.json" || !l.running() {
		t.Fatalf("logins %s, running %v; want only work.json and the router running", got, l.running())
	}
}

const lockReportingHelper = `#!/usr/bin/env python3
import fcntl, os, sys
held = "no --lock-fd"
if "--lock-fd" in sys.argv:
    fd = int(sys.argv[sys.argv.index("--lock-fd") + 1])
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        other = os.open(os.path.join(os.environ["FAKE_STATE"], "activation.lock"), os.O_RDWR)
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            held = "lock free"
        except BlockingIOError:
            held = "lock held"
    except OSError:
        held = "descriptor {} is not the lock".format(fd)
print("helper    " + held)
`

func helperScript(t *testing.T, text string) string {
	t.Helper()
	python := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(python, []byte(text), 0o755); err != nil {
		t.Fatal(err)
	}
	return python
}

func TestSetupHandsItsLockToActivate(t *testing.T) {
	l := setupLane(t)
	l.in.Python = helperScript(t, lockReportingHelper)
	t.Setenv("FAKE_STATE", l.in.State)

	var err error
	out := stdoutOf(t, func() { err = runSetup(l.in, []string{"work"}, false, os.Stdout) })

	if err != nil || !strings.Contains(out, "helper    lock held\n") {
		t.Fatalf("setup: %v, output\n%s\nwant the helper to hold the lock through descriptor 3", err, out)
	}
}

const signalReportingHelper = `#!/usr/bin/env python3
import os, signal, sys, time
def stop(signum, frame):
    print("helper    stopped by " + signal.Signals(signum).name, flush=True)
    sys.exit(7)
for signum in (signal.SIGTERM, signal.SIGHUP):
    signal.signal(signum, stop)
open(os.environ["FAKE_READY"], "w").close()
time.sleep(5)
`

func TestSetupAndCheckPassTerminationSignalsToTheHelper(t *testing.T) {
	commands := map[string]func(t *testing.T, python string) func() error{
		"setup": func(t *testing.T, python string) func() error {
			l := setupLane(t)
			l.in.Python = python
			return func() error { return runSetup(l.in, []string{"work"}, false, os.Stdout) }
		},
		"check": func(t *testing.T, python string) func() error {
			in, _, _ := directSetup(t, python)
			return func() error { return check([]string{"-state", in.State}) }
		},
	}
	for _, sig := range []struct {
		signal syscall.Signal
		name   string
	}{{syscall.SIGTERM, "SIGTERM"}, {syscall.SIGHUP, "SIGHUP"}} {
		for command, prepare := range commands {
			t.Run(command+" "+sig.name, func(t *testing.T) {
				ready := filepath.Join(t.TempDir(), "ready")
				t.Setenv("FAKE_READY", ready)
				run := prepare(t, helperScript(t, signalReportingHelper))
				caught := make(chan os.Signal, 1)
				signal.Notify(caught, sig.signal)
				defer signal.Stop(caught)

				var err error
				printed := make(chan string, 1)
				go func() { printed <- stdoutOf(t, func() { err = run() }) }()
				for deadline := time.Now().Add(10 * time.Second); time.Now().Before(deadline); time.Sleep(20 * time.Millisecond) {
					if _, serr := os.Stat(ready); serr == nil {
						break
					}
				}
				if err := syscall.Kill(os.Getpid(), sig.signal); err != nil {
					t.Fatal(err)
				}
				out := <-printed

				if err != silentExit(7) || !strings.HasSuffix(out, "helper    stopped by "+sig.name+"\n") {
					t.Fatalf("%s: %v, output\n%s\nwant the helper stopped by %s and exit 7", command, err, out, sig.name)
				}
			})
		}
	}
}

func TestSetupSaysWhichAccountsItAddedWhenTheirConfigDirectoryFailsToSyncAfterAFailedSignIn(t *testing.T) {
	l := setupLane(t)
	l.refuse["alumni"] = true
	l.stateWritableButUnreadable()

	var err error
	stdoutOf(t, func() { err = runSetup(l.in, []string{"work", "alumni"}, false, os.Stdout) })

	want := "the sign-in was cancelled; added work to config.json: " + l.in.Config +
		" is written, but its directory did not sync: open " + l.in.State + ": permission denied"
	if err == nil || err.Error() != want {
		t.Fatalf("setup: %v\nwant %s", err, want)
	}
}
