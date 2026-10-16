package main

import (
	"bufio"
	"bytes"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"os/user"
	"path/filepath"
	"strings"
)

type foreignJob struct {
	name, path string
}

func (e foreignJob) Error() string {
	return fmt.Sprintf("%s is loaded from %s, not from this installation", e.name, e.path)
}

func job(in installation) (loaded bool, path string, err error) {
	switch in.Manager {
	case "launchd":
		out, err := exec.Command("launchctl", "print", launchdTarget(in)).Output()
		if err != nil {
			return false, "", nil
		}
		path, ok := field(string(out), "\tpath = ")
		if !ok {
			return true, "", fmt.Errorf("launchctl print %s: no path", launchdTarget(in))
		}
		return true, path, nil
	case "systemd":
		out, err := exec.Command("systemctl", "--user", "show", "-p", "LoadState", "-p", "FragmentPath", in.Name).Output()
		if err != nil {
			return false, "", fmt.Errorf("systemctl --user show %s: %v", in.Name, err)
		}
		state, _ := field(string(out), "LoadState=")
		path, _ := field(string(out), "FragmentPath=")
		return state != "not-found" && path != "", path, nil
	}
	return false, "", nil
}

func field(text, prefix string) (string, bool) {
	for sc := bufio.NewScanner(strings.NewReader(text)); sc.Scan(); {
		if line := sc.Text(); strings.HasPrefix(line, prefix) {
			return strings.TrimSpace(line[len(prefix):]), true
		}
	}
	return "", false
}

func samePath(a, b string) bool {
	return resolved(a) == resolved(b)
}

func resolved(path string) string {
	if r, err := filepath.EvalSymlinks(path); err == nil {
		return r
	}
	if dir, err := filepath.EvalSymlinks(filepath.Dir(path)); err == nil {
		return filepath.Join(dir, filepath.Base(path))
	}
	return filepath.Clean(path)
}

func ownJob(in installation) (bool, error) {
	loaded, path, err := job(in)
	if err != nil || !loaded {
		return false, err
	}
	if !samePath(path, in.Descriptor) {
		return false, foreignJob{name: in.Name, path: path}
	}
	return true, nil
}

type labelHeld struct {
	name, path string
}

func (e labelHeld) Error() string {
	return fmt.Sprintf("%s is turned off for the installation at %s", e.name, e.path)
}

type stopMode int

const (
	keepOff stopMode = iota
	release
)

var loginAgents = func() string {
	u, err := user.Current()
	if err != nil {
		return ""
	}
	return filepath.Join(u.HomeDir, "Library", "LaunchAgents")
}

func otherLoginJob(in installation) (string, bool) {
	dir := loginAgents()
	if dir == "" {
		return "", false
	}
	plist := filepath.Join(dir, in.Name+".plist")
	if _, err := os.Stat(plist); err != nil || samePath(plist, in.Descriptor) {
		return "", false
	}
	return plist, true
}

func launchdDisabled(in installation) (bool, error) {
	out, err := exec.Command("launchctl", "print-disabled", launchdDomain()).Output()
	if err != nil {
		return false, fmt.Errorf("launchctl print-disabled %s: %v", launchdDomain(), err)
	}
	prefix := fmt.Sprintf("%q => ", in.Name)
	for sc := bufio.NewScanner(bytes.NewReader(out)); sc.Scan(); {
		if value, ok := strings.CutPrefix(strings.TrimSpace(sc.Text()), prefix); ok {
			return value == "disabled" || value == "true", nil
		}
	}
	return false, nil
}

func enableLaunchd(in installation, mode stopMode) error {
	off, err := launchdDisabled(in)
	if err != nil || !off {
		return err
	}
	if plist, other := otherLoginJob(in); other {
		if mode == release {
			return nil
		}
		return labelHeld{name: in.Name, path: plist}
	}
	return run("launchctl", "enable", launchdTarget(in))
}

func startService(in installation) error {
	switch in.Manager {
	case "launchd":
		loaded, err := ownJob(in)
		if err != nil {
			return err
		}
		if err := enableLaunchd(in, keepOff); err != nil {
			return err
		}
		if loaded {
			return run("launchctl", "kickstart", launchdTarget(in))
		}
		return run("launchctl", "bootstrap", launchdDomain(), in.Descriptor)
	case "systemd":
		if err := run("systemctl", "--user", "daemon-reload"); err != nil {
			return err
		}
		loaded, err := ownJob(in)
		if err != nil {
			return err
		}
		if !loaded {
			return fmt.Errorf("systemd does not load %s from %s; it reads units only from its own directories", in.Name, in.Descriptor)
		}
		if err := run("systemctl", "--user", "enable", in.Name); err != nil {
			return err
		}
		_ = run("systemctl", "--user", "reset-failed", in.Name)
		return run("systemctl", "--user", "start", in.Name)
	}
	return fmt.Errorf("the router runs in the foreground here; run: claude-router serve -supervised -config %s", in.Config)
}

func stopService(in installation, mode stopMode) error {
	switch in.Manager {
	case "launchd":
		loaded, err := ownJob(in)
		if err != nil {
			return err
		}
		if loaded {
			if err := run("launchctl", "bootout", launchdTarget(in)); err != nil {
				return err
			}
		}
		if mode == release {
			return enableLaunchd(in, release)
		}
		if _, other := otherLoginJob(in); other {
			return nil
		}
		return run("launchctl", "disable", launchdTarget(in))
	case "systemd":
		loaded, err := ownJob(in)
		if err != nil || !loaded {
			return err
		}
		if err := run("systemctl", "--user", "stop", in.Name); err != nil {
			return err
		}
		return run("systemctl", "--user", "disable", in.Name)
	}
	return errors.New("the router runs in the foreground here; stop it with Ctrl-C or SIGTERM")
}

func launchdDomain() string { return fmt.Sprintf("gui/%d", os.Getuid()) }

func launchdTarget(in installation) string { return launchdDomain() + "/" + in.Name }

func run(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %s: %v: %s", name, strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return nil
}
