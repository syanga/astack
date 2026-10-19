package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fakeActivate = `#!/bin/sh
echo "$*" >> "$FAKE_HELPER_LOG"
cat "$FAKE_CHECK_LINES"
exit "${FAKE_ACTIVATE_EXIT:-0}"
`

const checkLines = `ok        router running on 127.0.0.1:18787 (launchd)
ok        work, alumni ready
ok        paid overflow off on both accounts
ok        Claude Code settings point at the router
ok        code matches the accepted record (darwin/arm64)
ok        test message answered 200 on work in 5.8s
activated Claude Code routes through the router
`

func setupLane(t *testing.T) (*lane, string) {
	t.Helper()
	l := newLane(t, "launchd")
	dir := t.TempDir()
	python, lines, log := filepath.Join(dir, "python3"), filepath.Join(dir, "check.txt"), filepath.Join(dir, "helper.log")
	if err := os.WriteFile(python, []byte(fakeActivate), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(lines, []byte(checkLines), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FAKE_CHECK_LINES", lines)
	t.Setenv("FAKE_HELPER_LOG", log)
	l.in.Python, l.in.Helper, l.in.Manifest = python, "/lib/claude_router.py", "/state/manifest.json"
	return l, log
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
	l, log := setupLane(t)

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
	helper, _ := os.ReadFile(log)
	if string(helper) != "/lib/claude_router.py activate --brief --manifest /state/manifest.json\n" {
		t.Fatalf("helper ran with %q, want activate --brief with the recorded manifest", helper)
	}
}

func TestSetupRefusesWithTheAccountNameWhenPaidOverflowIsOn(t *testing.T) {
	l, log := setupLane(t)
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
	if _, err := os.Stat(log); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("setup ran activation after the refusal")
	}
}

func TestSetupRefusesWhenTheRouterIsAlreadyActivated(t *testing.T) {
	l, _ := setupLane(t)
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
	l, _ := setupLane(t)
	names := []string{"abcdefgh", "ijklmnop", "qrstuvwx"}
	l.overage["qrstuvwx"] = "enabled"

	out := stdoutOf(t, func() { _ = runSetup(l.in, names, true, os.Stdout) })

	if !strings.Contains(out, "refuse    qrstuvwx has paid overflow on") {
		t.Fatalf("output\n%s\nwant the refusal for qrstuvwx", out)
	}
	for _, line := range strings.Split(strings.TrimSpace(l.shown(out)), "\n") {
		if len(line) > 80 {
			t.Errorf("line of %d columns: %s", len(line), line)
		}
	}
}

func TestLoginShowsOnlyTheSignInURLFromTheSDK(t *testing.T) {
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
