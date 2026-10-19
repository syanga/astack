package main

import (
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"slices"
	"strings"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

type accountList []string

func (a *accountList) String() string { return strings.Join(*a, ",") }

func (a *accountList) Set(name string) error {
	if !claude.ValidAccountID(name) {
		return fmt.Errorf("%q is not a valid account ID (letters, digits, '-', '_', '.')", name)
	}
	*a = append(*a, name)
	return nil
}

func setup(args []string) error {
	fs := flag.NewFlagSet("setup", flag.ExitOnError)
	state := stateFlag(fs)
	var names accountList
	fs.Var(&names, "account", "account to sign in and add; repeat for each account")
	noBrowser := fs.Bool("no-browser", false, "print each sign-in URL instead of opening a browser")
	_ = fs.Parse(args)
	in, err := loadInstallation(*state)
	if err != nil {
		return err
	}
	return runSetup(in, names, *noBrowser, os.Stdout)
}

func activated(state string) (bool, error) {
	data, err := os.ReadFile(filepath.Join(state, "activation.json"))
	if errors.Is(err, os.ErrNotExist) {
		return false, nil
	}
	if err != nil {
		return false, err
	}
	var journal struct {
		Phase string `json:"phase"`
	}
	if err := json.Unmarshal(data, &journal); err != nil {
		return false, fmt.Errorf("activation.json: %w", err)
	}
	return journal.Phase == "active", nil
}

func runSetup(in installation, names []string, noBrowser bool, out io.Writer) error {
	lock, err := lockActivation(in.State)
	if err != nil {
		return err
	}
	defer lock.Close()
	if on, err := activated(in.State); err != nil {
		return err
	} else if on {
		return nextError{err: errors.New("Claude Code already routes through the router"), next: "claude-router account add NAME"}
	}
	if err := in.checkHelper("setup"); err != nil {
		return err
	}
	cfg, err := in.config()
	if err != nil {
		return err
	}
	var added []string
	for _, name := range names {
		if !enrolled(cfg, name) && !slices.Contains(added, name) {
			added = append(added, name)
		}
	}
	if len(cfg.Accounts) == 0 && len(added) == 0 {
		return nextError{err: errors.New("config.json lists no accounts"), next: "claude-router setup -account NAME"}
	}
	if len(added) > 0 {
		ran, err := stopForChange(in, out)
		if err != nil {
			return restoreRunning(in, ran, out, err)
		}
		var signed []string
		for _, name := range added {
			signInLines(out, name, noBrowser)
			if err := signInNew(in, name, noBrowser); err != nil {
				if published, aerr := addToConfig(in, signed, out); published && aerr != nil {
					err = fmt.Errorf("%w; added %s to config.json: %v", err, joined(signed), aerr)
				} else if aerr != nil {
					err = fmt.Errorf("%w; adding %s to config.json also failed: %v", err, joined(signed), aerr)
				}
				return restoreRunning(in, ran, out, err)
			}
			line(out, "login", name+" signed in")
			signed = append(signed, name)
		}
		if _, err := addToConfig(in, signed, out); err != nil {
			return restoreRunning(in, ran, out, err)
		}
	}
	if err := startAgain(in, out); err != nil {
		return err
	}
	if cfg, err = in.config(); err != nil {
		return err
	}
	all := make([]string, len(cfg.Accounts))
	for i, a := range cfg.Accounts {
		all[i] = string(a.ID)
	}
	overflow, err := waitReady(in, all, readyWait)
	if err != nil {
		return err
	}
	if len(overflow) > 0 {
		verb, whose := "has", overflow[0]
		if len(overflow) > 1 {
			verb, whose = "have", "their"
		}
		if len(overflow) > 2 {
			line(out, "refuse", fmt.Sprintf("%s have paid overflow on", joined(overflow)))
			line(out, "", "Claude Code stays direct")
		} else {
			line(out, "refuse", fmt.Sprintf("%s %s paid overflow on; Claude Code stays direct", joined(overflow), verb))
		}
		line(out, "next", "turn off "+whose+" paid overflow on claude.ai; claude-router setup")
		return silentExit(1)
	}
	line(out, "ready", strings.Join(all, ", ")+" ready")
	code, err := runHelperTo(in, []string{in.Helper, "activate", "--brief", "--manifest", in.Manifest, "--lock-fd", "3"}, lock, out, relayed...)
	if err != nil {
		return err
	}
	if code != 0 {
		return silentExit(code)
	}
	line(out, "next", "claude-router status")
	return nil
}

func addToConfig(in installation, names []string, out io.Writer) (published bool, err error) {
	if len(names) == 0 {
		return false, nil
	}
	published, err = editConfig(in.Config, func(c *claude.Config) error {
		for _, name := range names {
			c.Accounts = append(c.Accounts, router.Account{ID: router.AccountID(name), Capacity: 1})
		}
		return nil
	})
	if !published {
		return false, dropCredentials(in.State, names, err)
	}
	each := ", capacity 1"
	if len(names) > 1 {
		each += " each"
	}
	line(out, "config", "added "+joined(names)+each)
	return true, err
}
