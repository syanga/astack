package main

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fakeLaunchctl = `#!/bin/sh
jobs="$FAKE_JOBS"
label=${2##*/}
case "$1" in
print)
	[ -f "$jobs/$label" ] || exit 113
	printf '%s = {\n\tactive count = 1\n\tpath = %s\n\tstate = running\n}\n' "$2" "$(cat "$jobs/$label")" ;;
bootstrap)
	label=$(basename "$3" .plist)
	[ -f "$jobs/$label" ] && exit 5
	printf '%s' "$3" > "$jobs/$label" ;;
bootout)
	rm "$jobs/$label" || exit 3 ;;
kickstart)
	echo kickstart >> "$jobs/$label.log" ;;
esac
`

const fakeSystemctl = `#!/bin/sh
jobs="$FAKE_JOBS"
shift
case "$1" in
show)
	unit="$6"
	if [ -f "$jobs/$unit" ]; then
		printf 'LoadState=loaded\nFragmentPath=%s\n' "$(cat "$jobs/$unit")"
	else
		printf 'LoadState=not-found\nFragmentPath=\n'
	fi ;;
daemon-reload|reset-failed) ;;
start|stop)
	echo "$1" >> "$jobs/$2.log" ;;
esac
`

func fakeManager(t *testing.T) string {
	t.Helper()
	bin, jobs := t.TempDir(), t.TempDir()
	for name, script := range map[string]string{"launchctl": fakeLaunchctl, "systemctl": fakeSystemctl} {
		if err := os.WriteFile(filepath.Join(bin, name), []byte(script), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	t.Setenv("PATH", bin+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv("FAKE_JOBS", jobs)
	return jobs
}

func installationIn(t *testing.T, manager, name string) installation {
	home := t.TempDir()
	descriptor := filepath.Join(home, "Library", "LaunchAgents", name+".plist")
	if manager == "systemd" {
		descriptor = filepath.Join(home, ".config", "systemd", "user", name+".service")
	}
	if err := os.MkdirAll(filepath.Dir(descriptor), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(descriptor, nil, 0o644); err != nil {
		t.Fatal(err)
	}
	state := filepath.Join(home, "state")
	return installation{State: state, Manager: manager, Name: name, Descriptor: descriptor, Config: filepath.Join(state, "config.json")}
}

func loadedFrom(t *testing.T, jobs, name string) string {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(jobs, name))
	if errors.Is(err, os.ErrNotExist) {
		return ""
	}
	if err != nil {
		t.Fatal(err)
	}
	return string(data)
}

func TestStopAndStartLeaveAJobLoadedFromAnotherHome(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	if err := startService(a); err != nil {
		t.Fatal(err)
	}

	stopB := stopService(b)
	startB := startService(b)

	var f foreignJob
	if !errors.As(stopB, &f) || !samePath(f.path, a.Descriptor) {
		t.Fatalf("stop from home B: %v, want a refusal naming home A's descriptor %s", stopB, a.Descriptor)
	}
	if !errors.As(startB, &f) {
		t.Fatalf("start from home B: %v, want a refusal", startB)
	}
	if got := loadedFrom(t, jobs, a.Name); !samePath(got, a.Descriptor) {
		t.Fatalf("job loaded from %q after home B's stop and start, want home A's %s", got, a.Descriptor)
	}
	if _, err := os.Stat(filepath.Join(jobs, a.Name+".log")); !errors.Is(err, os.ErrNotExist) {
		t.Fatal("home B's start kickstarted home A's job")
	}
	if err := startService(a); err != nil {
		t.Fatal(err)
	}
	if log, _ := os.ReadFile(filepath.Join(jobs, a.Name+".log")); string(log) != "kickstart\n" {
		t.Fatalf("home A's start of its loaded job ran %q, want one kickstart", log)
	}
	if err := stopService(a); err != nil || loadedFrom(t, jobs, a.Name) != "" {
		t.Fatalf("home A's stop: %v, loaded from %q; want its job booted out", err, loadedFrom(t, jobs, a.Name))
	}
}

func TestSystemdStopAndStartActOnlyOnTheirOwnUnit(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "systemd", "dev.astack.claude-router.test")
	b := installationIn(t, "systemd", "dev.astack.claude-router.test")
	if err := os.WriteFile(filepath.Join(jobs, a.Name), []byte(a.Descriptor), 0o600); err != nil {
		t.Fatal(err)
	}

	stopB := stopService(b)
	startB := startService(b)
	stopA := stopService(a)

	var f foreignJob
	if !errors.As(stopB, &f) || !errors.As(startB, &f) || stopA != nil {
		t.Fatalf("stop B %v, start B %v, stop A %v; want B refused twice and A stopped", stopB, startB, stopA)
	}
	if log, _ := os.ReadFile(filepath.Join(jobs, a.Name+".log")); string(log) != "stop\n" {
		t.Fatalf("systemctl ran %q on the unit, want only home A's stop", log)
	}
	os.Remove(filepath.Join(jobs, a.Name))
	if err := startService(b); err == nil || !strings.Contains(err.Error(), "systemd does not load") {
		t.Fatalf("start of a unit systemd does not see: %v, want a refusal", err)
	}
}
