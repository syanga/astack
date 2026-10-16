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
print-disabled)
	printf 'disabled services = {\n'
	for f in "$jobs"/*.switch; do
		[ -f "$f" ] && printf '\t\t"%s" => %s\n' "$(basename "$f" .switch)" "$(cat "$f")"
	done
	printf '}\n' ;;
disable|enable)
	printf '%sd' "$1" > "$jobs/$label.switch" ;;
bootstrap)
	label=$(basename "$3" .plist)
	[ -f "$jobs/$label" ] && exit 5
	[ "$(cat "$jobs/$label.switch" 2>/dev/null)" = disabled ] && exit 5
	printf '%s/%s' "$(cd "$(dirname "$3")" && pwd -P)" "$(basename "$3")" > "$jobs/$label" ;;
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
enable)
	touch "$jobs/$2.enabled" ;;
disable)
	rm -f "$jobs/$2.enabled" ;;
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
	loginAt(t, t.TempDir())
	return jobs
}

func loginAt(t *testing.T, dir string) {
	saved := loginAgents
	loginAgents = func() string { return dir }
	t.Cleanup(func() { loginAgents = saved })
}

func loginLoad(t *testing.T, in installation) bool {
	t.Helper()
	return run("launchctl", "bootstrap", launchdDomain(), in.Descriptor) == nil
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

	stopB := stopService(b, keepOff)
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
	if err := stopService(a, keepOff); err != nil || loadedFrom(t, jobs, a.Name) != "" {
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

	stopB := stopService(b, keepOff)
	startB := startService(b)
	stopA := stopService(a, keepOff)

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

func TestStopKeepsTheJobOffAcrossLoginsUntilStart(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))
	if err := startService(a); err != nil {
		t.Fatal(err)
	}

	if err := stopService(a, keepOff); err != nil {
		t.Fatal(err)
	}
	if loadedFrom(t, jobs, a.Name) != "" || loginLoad(t, a) {
		t.Fatal("after stop, the job is loaded or loads at login; want it off until start")
	}
	if err := startService(a); err != nil || !samePath(loadedFrom(t, jobs, a.Name), a.Descriptor) {
		t.Fatalf("start after stop: %v, loaded from %q; want home A's job loaded", err, loadedFrom(t, jobs, a.Name))
	}
	if err := stopService(a, keepOff); err != nil {
		t.Fatal(err)
	}
	if err := stopService(a, release); err != nil {
		t.Fatal(err)
	}
	if !loginLoad(t, a) {
		t.Fatal("after the uninstaller's stop, a reinstalled job does not load at login")
	}
}

func TestStopAndStartLeaveTheLoginSwitchOfAnotherInstallation(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))

	if err := startService(b); err != nil {
		t.Fatal(err)
	}
	if err := stopService(b, keepOff); err != nil {
		t.Fatal(err)
	}
	if loadedFrom(t, jobs, b.Name) != "" || !loginLoad(t, a) {
		t.Fatal("home B's stop left its job loaded or kept home A's job from loading at login")
	}
	if err := stopService(a, keepOff); err != nil {
		t.Fatal(err)
	}
	startB := startService(b)
	releaseB := stopService(b, release)

	var h labelHeld
	if !errors.As(startB, &h) || !samePath(h.path, a.Descriptor) || releaseB != nil {
		t.Fatalf("home B start %v, uninstall stop %v; want start refused naming home A's plist and the stop to succeed", startB, releaseB)
	}
	if loadedFrom(t, jobs, a.Name) != "" || loginLoad(t, a) {
		t.Fatal("home B's start or uninstall turned home A's job back on")
	}
}

func TestStopActsOnItsOwnJobLoadedThroughASymlink(t *testing.T) {
	jobs := fakeManager(t)
	real := installationIn(t, "launchd", "dev.astack.claude-router.test")
	link := filepath.Join(t.TempDir(), "home")
	if err := os.Symlink(filepath.Dir(filepath.Dir(filepath.Dir(real.Descriptor))), link); err != nil {
		t.Fatal(err)
	}
	in := real
	in.Descriptor = filepath.Join(link, "Library", "LaunchAgents", in.Name+".plist")
	if err := startService(in); err != nil {
		t.Fatal(err)
	}

	err := stopService(in, keepOff)

	if err != nil || loadedFrom(t, jobs, in.Name) != "" {
		t.Fatalf("stop through a symlinked home: %v, loaded from %q; want its own job booted out", err, loadedFrom(t, jobs, in.Name))
	}
}

func TestSystemdStopDisablesAndStartEnablesOnlyItsOwnUnit(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "systemd", "dev.astack.claude-router.test")
	b := installationIn(t, "systemd", "dev.astack.claude-router.test")
	if err := os.WriteFile(filepath.Join(jobs, a.Name), []byte(a.Descriptor), 0o600); err != nil {
		t.Fatal(err)
	}
	enabled := func() bool {
		_, err := os.Stat(filepath.Join(jobs, a.Name+".enabled"))
		return err == nil
	}

	startA := startService(a)
	enabledAfterStart := enabled()
	stopB := stopService(b, keepOff)
	enabledAfterForeignStop := enabled()
	stopA := stopService(a, keepOff)

	if startA != nil || !enabledAfterStart || !enabledAfterForeignStop || stopA != nil || enabled() {
		t.Fatalf("start A %v (enabled %v), stop B %v (enabled %v), stop A %v (enabled %v); want A enabled by start, kept by B, disabled by its own stop",
			startA, enabledAfterStart, stopB, enabledAfterForeignStop, stopA, enabled())
	}
}
