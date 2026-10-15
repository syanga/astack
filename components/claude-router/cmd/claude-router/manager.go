package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"strings"
)

// startService asks the service manager to run the installed job. launchd
// loads the descriptor, which starts the job at once; an already loaded job
// is started again if it is not running. systemd reloads unit files and
// clears a previous start-limit failure first.
func startService(in installation) error {
	switch in.Manager {
	case "launchd":
		if launchdLoaded(in) {
			return run("launchctl", "kickstart", launchdTarget(in))
		}
		return run("launchctl", "bootstrap", launchdDomain(), in.Descriptor)
	case "systemd":
		if err := run("systemctl", "--user", "daemon-reload"); err != nil {
			return err
		}
		_ = run("systemctl", "--user", "reset-failed", in.Name)
		return run("systemctl", "--user", "start", in.Name)
	}
	return fmt.Errorf("the router runs in the foreground here; run: claude-router serve -supervised -config %s", in.Config)
}

func stopService(in installation) error {
	switch in.Manager {
	case "launchd":
		if !launchdLoaded(in) {
			return nil
		}
		return run("launchctl", "bootout", launchdTarget(in))
	case "systemd":
		return run("systemctl", "--user", "stop", in.Name)
	}
	return errors.New("the router runs in the foreground here; stop it with Ctrl-C or SIGTERM")
}

func launchdDomain() string { return fmt.Sprintf("gui/%d", os.Getuid()) }

func launchdTarget(in installation) string { return launchdDomain() + "/" + in.Name }

func launchdLoaded(in installation) bool {
	return exec.Command("launchctl", "print", launchdTarget(in)).Run() == nil
}

func run(name string, args ...string) error {
	out, err := exec.Command(name, args...).CombinedOutput()
	if err != nil {
		return fmt.Errorf("%s %s: %v: %s", name, strings.Join(args, " "), err, strings.TrimSpace(string(out)))
	}
	return nil
}
