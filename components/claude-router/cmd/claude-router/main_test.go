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

func withinFiveSeconds(t *testing.T, f func()) {
	t.Helper()
	done := make(chan struct{})
	go func() {
		defer close(done)
		f()
	}()
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("the sign-in still waits after 5 s")
	}
}

func answerOnce(answer *string) sdkLogin {
	return func(prompt func(string) (string, error)) error {
		var err error
		*answer, err = prompt("Paste the Claude callback URL (or press Enter to keep waiting): ")
		return err
	}
}

func TestABlankEnterLeavesTheCallbackPromptWaitingForThePastedURL(t *testing.T) {
	stdin, typing := io.Pipe()
	defer typing.Close()
	go func() {
		fmt.Fprintln(typing)
		fmt.Fprintln(typing, "  "+pastedCallback+"  ")
	}()
	var answer string
	var err error

	withinFiveSeconds(t, func() { err = awaitCallback(readLines(stdin), io.Discard, answerOnce(&answer)) })

	if answer != pastedCallback || err != nil {
		t.Fatalf("after Enter, then the pasted callback URL, the SDK got %q, %v; want the URL", answer, err)
	}
}

type writeSignal chan struct{}

func (s writeSignal) Write(p []byte) (int, error) {
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
	wrote := make(writeSignal, 1)
	var first, second error
	var answer string

	withinFiveSeconds(t, func() {
		first = awaitCallback(lines, wrote, func(prompt func(string) (string, error)) error {
			select {
			case <-wrote:
			default:
			}
			go prompt("")
			<-wrote
			return nil
		})
	})
	go fmt.Fprintln(typing, pastedCallback)
	withinFiveSeconds(t, func() { second = awaitCallback(lines, io.Discard, answerOnce(&answer)) })

	if first != nil || second != nil || answer != pastedCallback {
		t.Fatalf("sign-ins returned %v and %v, and the second SDK got %q; want the URL pasted after the first sign-in ended", first, second, answer)
	}
}

func TestASignInThatTimesOutSaysTheAttemptEndedAndHowToRetry(t *testing.T) {
	sdkTimeout := errors.New("callback_timeout: Timeout waiting for OAuth callback (caused by: timeout waiting for OAuth callback)")
	var out bytes.Buffer

	reportError(&out, "account", awaitCallback(readLines(strings.NewReader("")), io.Discard,
		func(func(string) (string, error)) error { return sdkTimeout }))

	want := "claude-router: account: sign-in timed out; this attempt ended\n" +
		"          its sign-in URL and callback address no longer work\n" +
		"next      run the same command again and open the new sign-in URL\n"
	if out.String() != want {
		t.Fatalf("timeout report\n%s\nwant\n%s", out.String(), want)
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
