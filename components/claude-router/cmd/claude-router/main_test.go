package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestLoginRefusesAnAccountIDOutsideTheAuthDirectory(t *testing.T) {
	state := t.TempDir()

	err := login([]string{"-state", state, "-account", "../x"})

	if err == nil || !strings.Contains(err.Error(), "not a valid account ID") {
		t.Fatalf("login with -account ../x returned %v, want a refusal", err)
	}
	if _, statErr := os.Stat(filepath.Join(state, "assignments")); !os.IsNotExist(statErr) {
		t.Fatal("login touched the state directory before refusing the account ID")
	}
}

const pastedCallback = "http://localhost:54545/callback?code=c0de&state=5tate"

func TestABlankEnterLeavesTheCallbackPromptWaitingForThePastedURL(t *testing.T) {
	stdin, typing := io.Pipe()
	defer typing.Close()
	prompt := newCallbackPrompt(readLines(stdin), io.Discard)
	go func() {
		fmt.Fprintln(typing)
		fmt.Fprintln(typing, "  "+pastedCallback+"  ")
	}()

	answer, err := prompt.ask("Paste the Claude callback URL (or press Enter to keep waiting): ")

	if answer != pastedCallback || err != nil {
		t.Fatalf("after Enter, then the pasted callback URL, the prompt answered %q, %v; want the URL", answer, err)
	}
}

type promptScreen chan struct{}

func (s promptScreen) Write(p []byte) (int, error) {
	select {
	case s <- struct{}{}:
	default:
	}
	return len(p), nil
}

func TestAnAutomaticCallbackEndsTheWaitingPromptAndLeavesTheNextLineForTheNextSignIn(t *testing.T) {
	stdin, typing := io.Pipe()
	defer typing.Close()
	lines := readLines(stdin)
	shown := make(promptScreen, 1)
	first := newCallbackPrompt(lines, shown)
	firstAnswer := make(chan string, 1)
	go func() {
		answer, _ := first.ask("")
		firstAnswer <- answer
	}()
	<-shown

	first.end()

	select {
	case answer := <-firstAnswer:
		if answer != "" {
			t.Fatalf("the prompt of the sign-in that ended answered %q, want no answer", answer)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("the prompt still waits for input after its sign-in ended")
	}
	second := newCallbackPrompt(lines, io.Discard)
	go fmt.Fprintln(typing, pastedCallback)
	answer, err := second.ask("")
	if answer != pastedCallback || err != nil {
		t.Fatalf("the next sign-in's prompt answered %q, %v; want the URL pasted after the first sign-in ended", answer, err)
	}
}

func TestStatusFromAnotherDirectoryReadsARelativeTokenPathBesideTheConfig(t *testing.T) {
	token := strings.Repeat("fake-client-token-", 2)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		w.Write([]byte("{}"))
	}))
	defer srv.Close()
	state := t.TempDir()
	config, _ := json.Marshal(map[string]any{"listen": srv.Listener.Addr().String(),
		"state_dir": state, "client_token_file": "client-token", "accounts": []any{}})
	for name, data := range map[string][]byte{"client-token": []byte(token + "\n"), "config.json": config} {
		if err := os.WriteFile(filepath.Join(state, name), data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	in, err := loadInstallation(state)
	if err != nil {
		t.Fatal(err)
	}
	t.Chdir(t.TempDir())

	r, err := statusReport(in, time.Now())
	var out bytes.Buffer
	writeStatus(&out, r, false)

	if err != nil || !r.Running {
		t.Fatalf("status from outside the state directory: err %v, running %v, want the running router:\n%s", err, r.Running, out.String())
	}
}

func TestRefusalsOfStepCommandsPadTheirLabelsToTenColumns(t *testing.T) {
	refusal := nextError{err: errors.New("Claude Code already routes through the router"), detail: "since 10:02",
		next: "claude-router account add NAME"}
	for command, want := range map[string]string{
		"setup":   "claude-router: setup: Claude Code already routes through the router\n          since 10:02\nnext      claude-router account add NAME\n",
		"account": "claude-router: account: Claude Code already routes through the router\n          since 10:02\nnext      claude-router account add NAME\n",
		"check":   "claude-router: check: Claude Code already routes through the router\n          since 10:02\nnext      claude-router account add NAME\n",
		"start":   "claude-router: start: Claude Code already routes through the router\n        since 10:02\nnext    claude-router account add NAME\n",
	} {
		var out bytes.Buffer

		reportError(&out, command, refusal)

		if out.String() != want {
			t.Errorf("%s refusal\n%s\nwant\n%s", command, out.String(), want)
		}
	}
}
