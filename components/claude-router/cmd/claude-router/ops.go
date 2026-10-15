package main

import (
	"bytes"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
)

var client = &http.Client{Timeout: 5 * time.Second}

func call(in installation, method, path string, body []byte) (*http.Response, error) {
	cfg, err := in.config()
	if err != nil {
		return nil, err
	}
	token, err := os.ReadFile(cfg.ClientTokenFile)
	if err != nil {
		return nil, fmt.Errorf("client_token_file: %w", err)
	}
	req, err := http.NewRequest(method, "http://"+cfg.Listen+path, bytes.NewReader(body))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(string(token)))
	req.Header.Set("Content-Type", "application/json")
	return client.Do(req)
}

func fetchStatus(in installation) (claude.Status, error) {
	var st claude.Status
	resp, err := call(in, http.MethodGet, "/claude-router/status", nil)
	if err != nil {
		return st, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return st, fmt.Errorf("status answered %d", resp.StatusCode)
	}
	return st, json.NewDecoder(resp.Body).Decode(&st)
}

func status(args []string) error {
	fs := flag.NewFlagSet("status", flag.ExitOnError)
	state := stateFlag(fs)
	verbose := fs.Bool("v", false, "show every account reading, conversation, and migration")
	_ = fs.Parse(args)
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	r := report{Now: time.Now(), Manager: in.Manager, StateShown: tilde(in.State)}
	if r.Sup, err = loadSupervision(in.supervisionDir()); err != nil {
		return err
	}
	cfg, err := in.config()
	if err != nil {
		r.ConfigErr, r.Running = err.Error(), locked(in.State)
	} else {
		r.Listen = cfg.Listen
		if st, err := fetchStatus(in); err == nil {
			r.Running, r.Status = true, st
		}
	}
	if r.Digest, err = readDigest(filepath.Join(in.State, "events.jsonl")); err != nil {
		return err
	}
	writeStatus(os.Stdout, r, *verbose)
	return nil
}

func tilde(path string) string {
	home, err := os.UserHomeDir()
	if err == nil && home != "" && strings.HasPrefix(path, home+string(filepath.Separator)) {
		return "~" + path[len(home):]
	}
	return path
}

func start(args []string) error {
	fs := flag.NewFlagSet("start", flag.ExitOnError)
	state := stateFlag(fs)
	wait := fs.Duration("wait", 90*time.Second, "how long to wait for readiness")
	_ = fs.Parse(args)
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	cfg, err := in.config()
	if err != nil {
		return err
	}
	if _, err := fetchStatus(in); err == nil {
		fmt.Printf("router  running  %s  %s\n", cfg.Listen, in.Manager)
		return nil
	}
	begun := time.Now()
	if err := (supervision{}).save(in.supervisionDir()); err != nil {
		return err
	}
	if err := startService(in); err != nil {
		var f foreignJob
		if errors.As(err, &f) {
			return nextError{err: f, next: "stop the router that installation runs, or install this one with another --service"}
		}
		return err
	}
	for deadline := begun.Add(*wait); time.Now().Before(deadline); time.Sleep(250 * time.Millisecond) {
		if _, err := fetchStatus(in); err == nil {
			fmt.Printf("router  running  %s  %s\n", cfg.Listen, in.Manager)
			return nil
		}
		if sup, err := loadSupervision(in.supervisionDir()); err == nil && sup.LastErrorAt.After(begun) {
			return nextError{err: errors.New("router did not start"), detail: "last error: " + sup.LastError,
				next: fmt.Sprintf("fix the error; %s retries up to %d starts in %s, or run claude-router stop", in.Manager, crashLimit, shortDuration(crashWindow))}
		}
	}
	return nextError{err: fmt.Errorf("router not ready after %s", *wait), next: "claude-router status"}
}

func stop(args []string) error {
	fs := flag.NewFlagSet("stop", flag.ExitOnError)
	state := stateFlag(fs)
	_ = fs.Parse(args)
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	return stopOwn(in)
}

func stopOwn(in installation) error {
	var f foreignJob
	if err := stopService(in); errors.As(err, &f) {
		fmt.Printf("router  not stopped: %v; left running\n", f)
		return nil
	} else if err != nil {
		return err
	}
	if err := waitUnlocked(in.State, 40*time.Second); err != nil {
		return err
	}
	fmt.Println("router  stopped; assignments kept")
	return nil
}

func move(args []string) error {
	fs := flag.NewFlagSet("move", flag.ExitOnError)
	state := stateFlag(fs)
	ref := fs.String("conversation", "", "conversation hash from status, or session ID")
	to := fs.String("to", "", "account to move it to")
	_ = fs.Parse(args)
	if *ref == "" || *to == "" {
		return errors.New("-conversation and -to are required")
	}
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	body, _ := json.Marshal(map[string]string{"conversation": *ref, "to": *to})
	resp, err := call(in, http.MethodPost, "/claude-router/move", body)
	if err != nil {
		return nextError{err: errors.New("router not running"), next: "claude-router start"}
	}
	defer resp.Body.Close()
	data, _ := io.ReadAll(resp.Body)
	switch resp.StatusCode {
	case http.StatusOK:
	case http.StatusNotFound:
		return fmt.Errorf("%s has no assignment", *ref)
	default:
		var e struct {
			Error struct {
				Message string `json:"message"`
			} `json:"error"`
		}
		_ = json.Unmarshal(data, &e)
		return errors.New(strings.TrimPrefix(e.Error.Message, "claude-router: "))
	}
	var m struct {
		Conversation, From, Account string
	}
	if err := json.Unmarshal(data, &m); err != nil {
		return err
	}
	if m.From == m.Account {
		fmt.Printf("moved   %s  already on %s\n", m.Conversation, m.Account)
		return nil
	}
	line := fmt.Sprintf("moved   %s  %s to %s, manual", m.Conversation, m.From, m.Account)
	if d, err := readDigest(filepath.Join(in.State, "events.jsonl")); err == nil {
		if c := d.Conversations[m.Conversation]; c != nil && c.Context > 0 {
			line += fmt.Sprintf("; next request writes %s cache", tokens(c.Context))
		}
	}
	fmt.Println(line)
	return nil
}

func direct(args []string) error {
	fs := flag.NewFlagSet("direct", flag.ExitOnError)
	state := stateFlag(fs)
	prepare := fs.Bool("prepare", false, "show the changes without making them")
	_ = fs.Parse(args)
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	if in.Helper == "" || in.Manifest == "" {
		return errors.New("no astack installation records this state directory; direct recovery needs one")
	}
	if in.Python == "" {
		return nextError{err: errors.New("service.json names no Python for the direct-recovery helper"), next: "./install.sh --component claude-router"}
	}
	if _, err := os.Stat(in.Python); err != nil {
		return nextError{err: fmt.Errorf("the Python that installed claude-router is gone: %s", in.Python),
			next: "./install.sh --component claude-router, from the astack checkout, with a Python 3.10 or later"}
	}
	helper := []string{in.Helper, "direct", "--state", in.State, "--manifest", in.Manifest}
	if *prepare {
		return exitWith(runHelper(in, append(helper, "--prepare")))
	}
	switch code, err := runHelper(in, append(helper, "--check")); {
	case err != nil:
		return err
	case code == nothingOwned:
		return exitWith(runHelper(in, helper))
	case code != 0:
		os.Exit(code)
	}
	if in.Manager == "foreground" {
		return exitWith(runHelper(in, helper))
	}
	if err := stopOwn(in); err != nil {
		return err
	}
	return exitWith(runHelper(in, append(helper, "--stopped")))
}

const nothingOwned = 3

func runHelper(in installation, args []string) (int, error) {
	cmd := exec.Command(in.Python, args...)
	cmd.Env = append(os.Environ(), "PYTHONDONTWRITEBYTECODE=1")
	cmd.Stdout, cmd.Stderr = os.Stdout, os.Stderr
	err := cmd.Run()
	var exit *exec.ExitError
	if errors.As(err, &exit) {
		return exit.ExitCode(), nil
	}
	return 0, err
}

func exitWith(code int, err error) error {
	if err == nil && code != 0 {
		os.Exit(code)
	}
	return err
}

func locked(state string) bool {
	f, err := os.OpenFile(filepath.Join(state, "assignments", "lock"), os.O_RDWR, 0)
	if err != nil {
		return false
	}
	defer f.Close()
	return syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB) != nil
}

func waitUnlocked(state string, wait time.Duration) error {
	path := filepath.Join(state, "assignments", "lock")
	for deadline := time.Now().Add(wait); ; time.Sleep(200 * time.Millisecond) {
		f, err := os.OpenFile(path, os.O_RDWR, 0)
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err != nil {
			return err
		}
		err = syscall.Flock(int(f.Fd()), syscall.LOCK_EX|syscall.LOCK_NB)
		f.Close()
		if err == nil {
			return nil
		}
		if time.Now().After(deadline) {
			return fmt.Errorf("the router still holds %s after %s", path, wait)
		}
	}
}
