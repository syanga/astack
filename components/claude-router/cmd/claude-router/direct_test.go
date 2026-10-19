package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
)

const fakePython = `#!/bin/sh
echo "$*" >> "$FAKE_HELPER_LOG"
case "$*" in *--check*) exit "$FAKE_CHECK_EXIT" ;; esac
`

func directSetup(t *testing.T, python string) (installation, string, string) {
	t.Helper()
	jobs := fakeManager(t)
	in := installationIn(t, "launchd", "dev.astack.claude-router.test")
	if err := os.MkdirAll(in.State, 0o700); err != nil {
		t.Fatal(err)
	}
	if python == "" {
		python = filepath.Join(t.TempDir(), "python3")
		if err := os.WriteFile(python, []byte(fakePython), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	record, _ := json.Marshal(map[string]string{"manager": in.Manager, "name": in.Name, "descriptor": in.Descriptor,
		"config": in.Config, "helper": "/lib/claude_router.py", "python": python, "manifest": "/m.json"})
	if err := os.WriteFile(filepath.Join(in.State, serviceRecord), record, 0o600); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(t.TempDir(), "helper.log")
	t.Setenv("FAKE_HELPER_LOG", log)
	if err := startService(in); err != nil {
		t.Fatal(err)
	}
	return in, jobs, log
}

func TestDirectStopsTheServiceOnlyWhenClientSettingsNameIt(t *testing.T) {
	for _, c := range []struct {
		name      string
		checkExit string
		loaded    bool
		apply     string
	}{
		{"nothing activated", "3", true, "/lib/claude_router.py direct --state %s --manifest /m.json --lock-fd 3"},
		{"activated", "0", false, "/lib/claude_router.py direct --state %s --manifest /m.json --lock-fd 3 --stopped"},
	} {
		t.Run(c.name, func(t *testing.T) {
			in, jobs, log := directSetup(t, "")
			t.Setenv("FAKE_CHECK_EXIT", c.checkExit)

			err := direct([]string{"-state", in.State})

			calls, _ := os.ReadFile(log)
			check := strings.ReplaceAll("/lib/claude_router.py direct --state %s --manifest /m.json --lock-fd 3 --check", "%s", in.State)
			want := check + "\n" + strings.ReplaceAll(c.apply, "%s", in.State) + "\n"
			if err != nil || string(calls) != want {
				t.Fatalf("direct: %v, helper ran:\n%s\nwant:\n%s", err, calls, want)
			}
			if loaded := loadedFrom(t, jobs, in.Name) != ""; loaded != c.loaded {
				t.Fatalf("job loaded %v after direct, want %v", loaded, c.loaded)
			}
		})
	}
}

func TestDirectNamesAMissingInstallerPython(t *testing.T) {
	in, jobs, _ := directSetup(t, "/nonexistent/python3")

	err := direct([]string{"-state", in.State})

	if err == nil || err.Error() != "the Python that installed claude-router is gone: /nonexistent/python3" {
		t.Fatalf("direct: %v, want the missing interpreter named", err)
	}
	if loadedFrom(t, jobs, in.Name) == "" {
		t.Fatal("direct stopped the service although it could not run the helper")
	}
}

func TestDirectWithAnotherInstallationsJobDoesNotClaimTheRouterStopped(t *testing.T) {
	for _, owner := range []string{"no installation", "this installation"} {
		t.Run(owner+" owns the login switch", func(t *testing.T) {
			in, jobs, log := directSetup(t, "")
			if owner == "this installation" {
				loginAt(t, filepath.Dir(in.Descriptor))
			}
			t.Setenv("FAKE_CHECK_EXIT", "0")
			if _, err := stopService(in, release); err != nil {
				t.Fatal(err)
			}
			other := installationIn(t, "launchd", in.Name)
			if err := startService(other); err != nil {
				t.Fatal(err)
			}

			err := direct([]string{"-state", in.State})

			calls, _ := os.ReadFile(log)
			apply := strings.ReplaceAll("/lib/claude_router.py direct --state %s --manifest /m.json --lock-fd 3", "%s", in.State)
			if err != nil || !strings.HasSuffix(string(calls), "\n"+apply+"\n") {
				t.Fatalf("direct: %v, helper ran:\n%s\nwant the last run to be %q, without --stopped", err, calls, apply)
			}
			if !samePath(loadedFrom(t, jobs, in.Name), other.Descriptor) {
				t.Fatal("direct stopped the other installation's job")
			}
		})
	}
}

func TestDirectRefusesWhileAnotherActivateOrDirectHoldsTheLock(t *testing.T) {
	in, jobs, log := directSetup(t, "")
	t.Setenv("FAKE_CHECK_EXIT", "0")
	held, err := os.OpenFile(filepath.Join(in.State, "activation.lock"), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()
	if err := syscall.Flock(int(held.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatal(err)
	}

	for _, args := range [][]string{{"-state", in.State}, {"-state", in.State, "-prepare"}} {
		err := direct(args)

		var n nextError
		if !errors.As(err, &n) || n.Error() != "another claude-router activate, direct, or uninstall is running" ||
			n.next != "wait for it to finish, then run again" {
			t.Fatalf("direct %v: %v, want the held lock named", args, err)
		}
	}
	if calls, _ := os.ReadFile(log); len(calls) != 0 {
		t.Fatalf("direct ran the helper while the lock was held:\n%s", calls)
	}
	if loadedFrom(t, jobs, in.Name) == "" {
		t.Fatal("direct stopped the service while the lock was held")
	}
}

const lockCheckingPython = `#!/usr/bin/env python3
import fcntl, os, sys
state = sys.argv[sys.argv.index("--state") + 1]
mode = "check" if "--check" in sys.argv else "apply"
held = "without a lock descriptor"
if "--lock-fd" in sys.argv:
    fcntl.flock(int(sys.argv[sys.argv.index("--lock-fd") + 1]), fcntl.LOCK_EX | fcntl.LOCK_NB)
    other = os.open(os.path.join(state, "activation.lock"), os.O_RDWR)
    try:
        fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
        held = "free"
    except BlockingIOError:
        held = "held"
with open(os.environ["FAKE_HELPER_LOG"], "a") as log:
    log.write("{} {}\n".format(mode, held))
`

func TestDirectHoldsTheLockAcrossTheCheckTheStopAndTheApplyAndHandsItToTheHelper(t *testing.T) {
	python := filepath.Join(t.TempDir(), "python3")
	if err := os.WriteFile(python, []byte(lockCheckingPython), 0o755); err != nil {
		t.Fatal(err)
	}
	in, jobs, log := directSetup(t, python)

	err := direct([]string{"-state", in.State})

	calls, _ := os.ReadFile(log)
	if err != nil || string(calls) != "check held\napply held\n" {
		t.Fatalf("direct: %v, helper saw:\n%s\nwant the lock held by its own descriptor in both runs", err, calls)
	}
	if loadedFrom(t, jobs, in.Name) != "" {
		t.Fatal("direct left the service loaded")
	}
	after, err := os.OpenFile(filepath.Join(in.State, "activation.lock"), os.O_RDWR, 0)
	if err != nil {
		t.Fatal(err)
	}
	defer after.Close()
	if err := syscall.Flock(int(after.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		t.Fatalf("direct kept the lock after it returned: %v", err)
	}
}
