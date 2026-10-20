package main

import (
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
)

const fakeLaunchctl = `#!/bin/sh
[ "$1" = "$FAKE_FAIL" ] && exit 1
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
[ "$1" = "$FAKE_FAIL" ] && exit 1
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

	_, stopB := stopService(b, keepOff)
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
	if _, err := stopService(a, keepOff); err != nil || loadedFrom(t, jobs, a.Name) != "" {
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

	_, stopB := stopService(b, keepOff)
	startB := startService(b)
	_, stopA := stopService(a, keepOff)

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

	if _, err := stopService(a, keepOff); err != nil {
		t.Fatal(err)
	}
	if loadedFrom(t, jobs, a.Name) != "" || loginLoad(t, a) {
		t.Fatal("after stop, the job is loaded or loads at login; want it off until start")
	}
	if err := startService(a); err != nil || !samePath(loadedFrom(t, jobs, a.Name), a.Descriptor) {
		t.Fatalf("start after stop: %v, loaded from %q; want home A's job loaded", err, loadedFrom(t, jobs, a.Name))
	}
	if _, err := stopService(a, keepOff); err != nil {
		t.Fatal(err)
	}
	if _, err := stopService(a, release); err != nil {
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
	if _, err := stopService(b, keepOff); err != nil {
		t.Fatal(err)
	}
	if loadedFrom(t, jobs, b.Name) != "" || !loginLoad(t, a) {
		t.Fatal("home B's stop left its job loaded or kept home A's job from loading at login")
	}
	if _, err := stopService(a, keepOff); err != nil {
		t.Fatal(err)
	}
	startB := startService(b)
	_, releaseB := stopService(b, release)

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

	_, err := stopService(in, keepOff)

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
	_, stopB := stopService(b, keepOff)
	enabledAfterForeignStop := enabled()
	_, stopA := stopService(a, keepOff)

	var f foreignJob
	if startA != nil || !enabledAfterStart || !errors.As(stopB, &f) || !enabledAfterForeignStop || stopA != nil || enabled() {
		t.Fatalf("start A %v (enabled %v), stop B %v (enabled %v), stop A %v (enabled %v); want A enabled by start, B refused and the unit kept enabled, A disabled by its own stop",
			startA, enabledAfterStart, stopB, enabledAfterForeignStop, stopA, enabled())
	}
}

func stdoutOf(t *testing.T, f func()) string {
	t.Helper()
	r, w, err := os.Pipe()
	if err != nil {
		t.Fatal(err)
	}
	saved := os.Stdout
	os.Stdout = w
	defer func() { os.Stdout = saved }()
	f()
	w.Close()
	out, _ := io.ReadAll(r)
	return string(out)
}

func switchOf(t *testing.T, jobs, name string) string {
	t.Helper()
	data, _ := os.ReadFile(filepath.Join(jobs, name+".switch"))
	return string(data)
}

func TestTheLoginOwnersStopTurnsItsSwitchOffWhileAnotherHomesJobRuns(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))
	if err := startService(b); err != nil {
		t.Fatal(err)
	}
	loadedB := loadedFrom(t, jobs, b.Name)

	var stopA error
	out := stdoutOf(t, func() { _, stopA = stopOwn(a, keepOff) })

	want := fmt.Sprintf("router  not stopped: %s is loaded from %s, not from this installation; left running\n"+
		"        this installation stays off until claude-router start\n", a.Name, loadedB)
	if stopA != nil || out != want {
		t.Fatalf("home A's stop: %v, printed:\n%s\nwant:\n%s", stopA, out, want)
	}
	if loadedFrom(t, jobs, b.Name) != loadedB {
		t.Fatal("home A's stop touched home B's job")
	}
	if _, err := stopService(b, keepOff); err != nil {
		t.Fatal(err)
	}
	if loginLoad(t, a) {
		t.Fatal("once home B's job is gone, home A's job loads at login although home A ran stop")
	}
	if err := startService(a); err != nil || !samePath(loadedFrom(t, jobs, a.Name), a.Descriptor) {
		t.Fatalf("home A's start: %v, loaded from %q; want home A's job loaded", err, loadedFrom(t, jobs, a.Name))
	}
}

func TestAStopWhoseBootoutFailsStillKeepsTheJobOffAtLogin(t *testing.T) {
	fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))
	if err := startService(a); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FAKE_FAIL", "bootout")

	_, err := stopService(a, keepOff)

	t.Setenv("FAKE_FAIL", "")
	if err == nil {
		t.Fatal("stop with a failing bootout succeeded, want its error")
	}
	if err := run("launchctl", "bootout", launchdTarget(a)); err != nil || loginLoad(t, a) {
		t.Fatalf("after the failed stop and a logout (%v), the job loads at login; want it off until start", err)
	}
}

func TestASystemdStopWhoseStopFailsStillKeepsTheUnitOffAtLogin(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "systemd", "dev.astack.claude-router.test")
	if err := os.WriteFile(filepath.Join(jobs, a.Name), []byte(a.Descriptor), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := startService(a); err != nil {
		t.Fatal(err)
	}
	t.Setenv("FAKE_FAIL", "stop")

	_, err := stopService(a, keepOff)

	if _, statErr := os.Stat(filepath.Join(jobs, a.Name+".enabled")); err == nil || !errors.Is(statErr, os.ErrNotExist) {
		t.Fatalf("stop with a failing systemctl stop: %v, unit still enabled %v; want the error and the unit disabled", err, statErr == nil)
	}
}

func TestAnUnreadableLoginDirectoryRefusesToTouchTheSwitch(t *testing.T) {
	if os.Geteuid() == 0 {
		t.Skip("root reads any directory")
	}
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	login := filepath.Dir(a.Descriptor)
	loginAt(t, login)
	if err := os.Chmod(login, 0); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(login, 0o700) })

	_, stopB := stopService(b, keepOff)
	switchAfterStop := switchOf(t, jobs, a.Name)
	if err := run("launchctl", "disable", launchdTarget(a)); err != nil {
		t.Fatal(err)
	}
	startB := startService(b)

	for _, err := range []error{stopB, startB} {
		if err == nil || !strings.Contains(err.Error(), "cannot tell which installation turns "+a.Name+" on and off at login: stat "+filepath.Join(login, a.Name+".plist")+": permission denied") {
			t.Fatalf("home B's stop: %v; start: %v; want both refused naming the unreadable login plist", stopB, startB)
		}
	}
	if switchAfterStop != "" || switchOf(t, jobs, a.Name) != "disabled" || loadedFrom(t, jobs, b.Name) != "" {
		t.Fatalf("switch %q after home B's stop, %q after its start, loaded from %q; want the switch untouched and nothing loaded",
			switchAfterStop, switchOf(t, jobs, a.Name), loadedFrom(t, jobs, b.Name))
	}
}

func answering(t *testing.T, in installation) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Write([]byte("{}"))
	}))
	t.Cleanup(srv.Close)
	if err := os.MkdirAll(in.State, 0o700); err != nil {
		t.Fatal(err)
	}
	token := filepath.Join(in.State, "client-token")
	config, _ := json.Marshal(map[string]string{"listen": srv.Listener.Addr().String(), "client_token_file": token})
	record, _ := json.Marshal(in)
	for path, data := range map[string][]byte{token: []byte("t"), in.Config: config, filepath.Join(in.State, serviceRecord): record} {
		if err := os.WriteFile(path, data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
}

func TestStartTurnsTheSwitchOnWhenTheRouterStillRunsAfterAFailedStop(t *testing.T) {
	for _, c := range []struct{ manager, fail string }{{"launchd", "bootout"}, {"systemd", "stop"}} {
		t.Run(c.manager, func(t *testing.T) {
			jobs := fakeManager(t)
			a := installationIn(t, c.manager, "dev.astack.claude-router.test")
			loginAt(t, filepath.Dir(a.Descriptor))
			if c.manager == "systemd" {
				if err := os.WriteFile(filepath.Join(jobs, a.Name), []byte(a.Descriptor), 0o600); err != nil {
					t.Fatal(err)
				}
			}
			if err := startService(a); err != nil {
				t.Fatal(err)
			}
			answering(t, a)
			t.Setenv("FAKE_FAIL", c.fail)
			if _, err := stopService(a, keepOff); err == nil {
				t.Fatalf("stop with a failing %s succeeded", c.fail)
			}
			t.Setenv("FAKE_FAIL", "")

			var err error
			out := stdoutOf(t, func() { err = start([]string{"-state", a.State}) })

			if err != nil || !strings.HasPrefix(out, "router  running  ") {
				t.Fatalf("start: %v, printed %q; want router running", err, out)
			}
			switch c.manager {
			case "launchd":
				if err := run("launchctl", "bootout", launchdTarget(a)); err != nil || !loginLoad(t, a) {
					t.Fatalf("after start and a logout (%v), the job does not load at login", err)
				}
			case "systemd":
				if _, err := os.Stat(filepath.Join(jobs, a.Name+".enabled")); err != nil {
					t.Fatalf("after start, the unit is not enabled: %v", err)
				}
			}
		})
	}
}

func TestStartOfAnotherInstallationsRunningRouterLeavesTheOwnersSwitchOff(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))
	if err := startService(b); err != nil {
		t.Fatal(err)
	}
	answering(t, b)
	if _, err := stopService(a, keepOff); !errors.As(err, new(foreignJob)) {
		t.Fatalf("home A's stop beside home B's job: %v, want it left running", err)
	}

	var err error
	out := stdoutOf(t, func() { err = start([]string{"-state", b.State}) })

	if err != nil || !strings.HasPrefix(out, "router  running  ") || switchOf(t, jobs, a.Name) != "disabled" {
		t.Fatalf("home B's start: %v, printed %q, switch %q; want router running and home A's switch left disabled", err, out, switchOf(t, jobs, a.Name))
	}
}

func TestTheLoginOwnersUninstallBesideAnotherHomesJobTurnsTheSwitchBackOn(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))
	if err := startService(b); err != nil {
		t.Fatal(err)
	}
	loadedB := loadedFrom(t, jobs, b.Name)
	if _, err := stopOwn(a, keepOff); err != nil {
		t.Fatal(err)
	}

	var stopped bool
	var err error
	out := stdoutOf(t, func() { stopped, err = stopOwn(a, release) })

	want := fmt.Sprintf("router  not stopped: %s is loaded from %s, not from this installation; left running\n", a.Name, loadedB)
	if err != nil || stopped || out != want {
		t.Fatalf("home A's uninstall stop: stopped %v, %v, printed:\n%s\nwant:\n%s", stopped, err, out, want)
	}
	if loadedFrom(t, jobs, b.Name) != loadedB {
		t.Fatal("home A's uninstall touched home B's job")
	}
	if err := run("launchctl", "bootout", launchdTarget(b)); err != nil || !loginLoad(t, a) {
		t.Fatalf("after home A's uninstall and a logout (%v), a plist in the login directory does not load at login", err)
	}
}

func TestAnotherInstallationsUninstallLeavesTheOwnersSwitchOn(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	loginAt(t, filepath.Dir(a.Descriptor))
	if err := startService(a); err != nil {
		t.Fatal(err)
	}

	if _, err := stopOwn(b, release); err != nil {
		t.Fatal(err)
	}

	if !samePath(loadedFrom(t, jobs, a.Name), a.Descriptor) {
		t.Fatal("home B's uninstall stopped home A's job")
	}
	if err := run("launchctl", "bootout", launchdTarget(a)); err != nil || !loginLoad(t, a) {
		t.Fatalf("after home B's uninstall and a logout (%v), home A's job does not load at login", err)
	}
}

func TestAStopBesideAnotherHomesJobWithNoLoginOwnerLeavesTheSwitchOn(t *testing.T) {
	jobs := fakeManager(t)
	a := installationIn(t, "launchd", "dev.astack.claude-router.test")
	b := installationIn(t, "launchd", "dev.astack.claude-router.test")
	if err := startService(a); err != nil {
		t.Fatal(err)
	}

	var err error
	out := stdoutOf(t, func() { _, err = stopOwn(b, keepOff) })

	want := fmt.Sprintf("router  not stopped: %s is loaded from %s, not from this installation; left running\n", b.Name, loadedFrom(t, jobs, a.Name))
	if err != nil || out != want {
		t.Fatalf("home B's stop: %v, printed:\n%s\nwant:\n%s", err, out, want)
	}
	if err := run("launchctl", "bootout", launchdTarget(a)); err != nil || !loginLoad(t, a) {
		t.Fatalf("after home B's stop and a logout (%v), home A's plist does not bootstrap", err)
	}
}
