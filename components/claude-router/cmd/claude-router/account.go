package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

const (
	readyWait    = 90 * time.Second
	recentWindow = 5 * time.Hour
)

var editConfig = claude.EditConfig

func line(out io.Writer, label, text string) {
	fmt.Fprintf(out, "%-9s %s\n", label, text)
}

func joined(names []string) string {
	switch len(names) {
	case 0:
		return ""
	case 1, 2:
		return strings.Join(names, " and ")
	}
	return strings.Join(names[:len(names)-1], ", ") + ", and " + names[len(names)-1]
}

func account(args []string) error {
	if len(args) == 0 || args[0] != "add" && args[0] != "remove" {
		usage()
	}
	fs := flag.NewFlagSet("account "+args[0], flag.ContinueOnError)
	state := stateFlag(fs)
	var noBrowser, force bool
	if args[0] == "add" {
		fs.BoolVar(&noBrowser, "no-browser", false, "print the sign-in URL instead of opening a browser")
	} else {
		fs.BoolVar(&force, "force", false, "remove an account that has conversations from the last 5 hours")
	}
	name, rest := "", args[1:]
	if len(rest) > 0 && !strings.HasPrefix(rest[0], "-") {
		name, rest = rest[0], rest[1:]
	}
	if err := fs.Parse(rest); errors.Is(err, flag.ErrHelp) {
		return silentExit(0)
	} else if err != nil {
		return silentExit(2)
	}
	if name == "" && fs.NArg() == 1 {
		name = fs.Arg(0)
	}
	if name == "" {
		return fmt.Errorf("account %s needs an account name", args[0])
	}
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	if args[0] == "add" {
		return addAccount(in, name, noBrowser, os.Stdout)
	}
	return removeAccount(in, name, force, time.Now(), os.Stdout)
}

func stopForChange(in installation, out io.Writer) (bool, error) {
	if _, err := fetchStatus(in); err != nil && !locked(in.State) {
		return false, nil
	}
	if in.Manager == "foreground" {
		return true, nextError{err: errors.New("the router runs in the foreground here"), next: "stop it with Ctrl-C or SIGTERM, then run again"}
	}
	stopped, err := stopOwn(in, keepOff, io.Discard)
	if err != nil {
		return true, err
	}
	if stopped {
		line(out, "stop", "router stopped; assignments kept")
	}
	return true, nil
}

func startAgain(in installation, out io.Writer) error {
	if in.Manager == "foreground" {
		return nil
	}
	if err := startRouter(in, readyWait); err != nil {
		return err
	}
	cfg, err := in.config()
	if err != nil {
		return err
	}
	line(out, "start", fmt.Sprintf("router running on %s (%s)", cfg.Listen, in.Manager))
	return nil
}

func restoreRunning(in installation, ran bool, out io.Writer, err error) error {
	if !ran {
		return err
	}
	if serr := startAgain(in, out); serr != nil {
		return fmt.Errorf("%w; starting the router again also failed: %v", err, serr)
	}
	return err
}

// dropCredentials deletes the logins of names, which config.json does not
// list, so that auths/ holds no login outside config.json.
func dropCredentials(state string, names []string, err error) error {
	for _, name := range names {
		if rerr := os.Remove(filepath.Join(state, "auths", name+".json")); rerr != nil && !errors.Is(rerr, os.ErrNotExist) {
			err = fmt.Errorf("%w; removing the new login of %s also failed: %v", err, name, rerr)
		}
	}
	return err
}

// signInNew signs in name, which config.json does not list. A sign-in that
// fails after it wrote auths/NAME.json deletes that login.
func signInNew(in installation, name string, noBrowser bool) error {
	published, err := signIn(in.State, name, noBrowser)
	if err != nil && published {
		return dropCredentials(in.State, []string{name}, err)
	}
	return err
}

func signInLines(out io.Writer, name string, noBrowser bool) {
	if noBrowser {
		line(out, "login", name+": open the URL below and sign in")
	} else {
		line(out, "login", name+": sign in in the browser window that opened")
	}
}

func waitReady(in installation, names []string, wait time.Duration) ([]string, error) {
	var missing []string
	for deadline := time.Now().Add(wait); ; time.Sleep(250 * time.Millisecond) {
		st, err := fetchStatus(in)
		missing = slices.Clone(names)
		var overflow []string
		if err == nil {
			for _, a := range st.Accounts {
				i := slices.Index(missing, string(a.ID))
				if i < 0 || !a.Registered || a.NeedsLogin || a.Overage.State == "" {
					continue
				}
				missing = slices.Delete(missing, i, i+1)
				if a.Overage.State == router.OverageEnabled || a.Overage.State == router.OveragePaidUse {
					overflow = append(overflow, string(a.ID))
				}
			}
		}
		if len(missing) == 0 {
			return overflow, nil
		}
		if time.Now().After(deadline) {
			return nil, nextError{err: fmt.Errorf("%s not ready after %s", joined(missing), wait), next: "claude-router status"}
		}
	}
}

func addAccount(in installation, name string, noBrowser bool, out io.Writer) error {
	if !claude.ValidAccountID(name) {
		return fmt.Errorf("%q is not a valid account ID (letters, digits, '-', '_', '.')", name)
	}
	lock, err := lockActivation(in.State)
	if err != nil {
		return err
	}
	defer lock.Close()
	cfg, err := in.config()
	if err != nil {
		return err
	}
	if enrolled(cfg, name) {
		return nextError{err: fmt.Errorf("%s is already an account", name),
			next: fmt.Sprintf("to sign it in again: claude-router stop; claude-router login -account %s; claude-router start", name)}
	}
	ran, err := stopForChange(in, out)
	if err != nil {
		return restoreRunning(in, ran, out, err)
	}
	signInLines(out, name, noBrowser)
	if err := signInNew(in, name, noBrowser); err != nil {
		return restoreRunning(in, ran, out, err)
	}
	line(out, "login", name+" signed in")
	if err := addToConfig(in, []string{name}, out); err != nil {
		return restoreRunning(in, ran, out, err)
	}
	if !ran {
		line(out, "next", "claude-router start")
		return nil
	}
	if err := startAgain(in, out); err != nil {
		return err
	}
	overflow, err := waitReady(in, []string{name}, readyWait)
	if err != nil {
		return err
	}
	if len(overflow) > 0 {
		line(out, "warn", name+" has paid overflow on; the router sends it nothing")
		line(out, "next", "turn off "+name+" paid overflow on claude.ai")
		return nil
	}
	line(out, "ready", name+" ready")
	return nil
}

func removeAccount(in installation, name string, force bool, now time.Time, out io.Writer) error {
	lock, err := lockActivation(in.State)
	if err != nil {
		return err
	}
	defer lock.Close()
	cfg, err := in.config()
	if err != nil {
		return err
	}
	if !enrolled(cfg, name) {
		return fmt.Errorf("%s is not an account in %s", name, in.Config)
	}
	if len(cfg.Accounts) == 1 {
		return nextError{err: fmt.Errorf("%s is the only account", name), next: fmt.Sprintf("claude-router account add NAME, then remove %s", name)}
	}
	if !force {
		if err := refuseRecent(in.State, name, now); err != nil {
			return err
		}
	}
	ran, err := stopForChange(in, out)
	if err != nil {
		return restoreRunning(in, ran, out, err)
	}
	if !force {
		if err := refuseRecent(in.State, name, now); err != nil {
			return restoreRunning(in, ran, out, err)
		}
	}
	var removed router.Account
	at := -1
	published, err := editConfig(in.Config, func(c *claude.Config) error {
		at = slices.IndexFunc(c.Accounts, func(a router.Account) bool { return string(a.ID) == name })
		if at < 0 {
			return fmt.Errorf("%s is not an account in %s", name, in.Config)
		}
		removed = c.Accounts[at]
		c.Accounts = slices.Delete(c.Accounts, at, at+1)
		return nil
	})
	if !published {
		return restoreRunning(in, ran, out, err)
	}
	line(out, "config", "removed "+name)
	credential := filepath.Join("auths", name+".json")
	if rerr := os.Remove(filepath.Join(in.State, credential)); rerr == nil {
		line(out, "remove", credential)
	} else if !errors.Is(rerr, os.ErrNotExist) {
		return restoreRunning(in, ran, out, putBack(in, removed, at, out, errors.Join(err, rerr)))
	}
	if ran {
		if serr := startAgain(in, out); serr != nil {
			return errors.Join(err, serr)
		}
	}
	line(out, "note", name+" stays signed in on claude.ai until you sign it out there")
	return err
}

// putBack adds a, whose login account remove could not delete, back to
// config.json at index at, so that config.json lists the login again.
func putBack(in installation, a router.Account, at int, out io.Writer, err error) error {
	published, perr := editConfig(in.Config, func(c *claude.Config) error {
		c.Accounts = slices.Insert(c.Accounts, min(at, len(c.Accounts)), a)
		return nil
	})
	if !published {
		return nextError{err: fmt.Errorf("%w; adding %s back to config.json also failed: %v", err, a.ID, perr),
			next: fmt.Sprintf("delete %s, then claude-router start", filepath.Join(in.State, "auths", string(a.ID)+".json"))}
	}
	line(out, "config", "added "+string(a.ID)+" back")
	return errors.Join(err, perr)
}

func refuseRecent(state, name string, now time.Time) error {
	recent, err := recentConversations(state, router.AccountID(name), now.Add(-recentWindow))
	if err != nil || recent == 0 {
		return err
	}
	noun := "conversations"
	if recent == 1 {
		noun = "conversation"
	}
	return nextError{err: fmt.Errorf("%s has %d %s from the last 5 hours", name, recent, noun),
		next: fmt.Sprintf("claude-router account remove %s -force, and they move to another account", name)}
}

func recentConversations(state string, account router.AccountID, since time.Time) (int, error) {
	bindings, err := router.ReadBindings(filepath.Join(state, "assignments"))
	if err != nil {
		return 0, err
	}
	used, err := requestsSince(filepath.Join(state, "events.jsonl"), account, since)
	if err != nil {
		return 0, err
	}
	n := 0
	for conv, b := range bindings {
		if b.Account == account && (!b.Since.Before(since) || used[claude.ConversationHash(conv)]) {
			n++
		}
	}
	return n, nil
}

func requestsSince(path string, account router.AccountID, since time.Time) (map[string]bool, error) {
	f, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	defer f.Close()
	used := map[string]bool{}
	sc := bufio.NewScanner(f)
	sc.Buffer(nil, 1<<20)
	for sc.Scan() {
		var e claude.Event
		if json.Unmarshal(sc.Bytes(), &e) == nil && e.Kind == "request" && e.Account == account && !e.At.Before(since) {
			used[e.Conversation] = true
		}
	}
	return used, sc.Err()
}
