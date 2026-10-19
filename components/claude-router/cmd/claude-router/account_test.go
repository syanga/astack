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
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
)

type lane struct {
	t       *testing.T
	in      installation
	jobs    string
	listen  string
	overage map[string]string
	refuse  map[string]bool
	// unclear names accounts whose sign-in writes the login and then fails to clear its login requirement.
	unclear map[string]bool
	signed  []string
	queried func()
}

func newLane(t *testing.T, manager string, accounts ...string) *lane {
	t.Helper()
	l := &lane{t: t, jobs: fakeManager(t), overage: map[string]string{}, refuse: map[string]bool{}, unclear: map[string]bool{}}
	l.in = installationIn(t, manager, "dev.astack.claude-router.test")
	if err := os.MkdirAll(filepath.Join(l.in.State, "auths"), 0o700); err != nil {
		t.Fatal(err)
	}
	token := strings.Repeat("fake-client-token-", 2)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if l.queried != nil {
			l.queried()
		}
		if r.Header.Get("Authorization") != "Bearer "+token || !l.running() {
			w.WriteHeader(http.StatusServiceUnavailable)
			return
		}
		cfg, err := l.in.config()
		if err != nil {
			w.WriteHeader(http.StatusInternalServerError)
			return
		}
		var listed []map[string]any
		for _, a := range cfg.Accounts {
			_, err := os.Stat(filepath.Join(l.in.State, "auths", string(a.ID)+".json"))
			state := l.overage[string(a.ID)]
			if state == "" {
				state = "disabled"
			}
			listed = append(listed, map[string]any{"id": a.ID, "registered": err == nil, "overage": map[string]any{"state": state}})
		}
		json.NewEncoder(w).Encode(map[string]any{"journal": "ok", "accounts": listed})
	}))
	t.Cleanup(srv.Close)
	l.listen = srv.Listener.Addr().String()
	var configured []map[string]any
	for _, a := range accounts {
		configured = append(configured, map[string]any{"id": a, "capacity": 1})
		l.write(filepath.Join("auths", a+".json"), "{}\n")
	}
	if configured == nil {
		configured = []map[string]any{}
	}
	config, _ := json.MarshalIndent(map[string]any{"listen": l.listen, "state_dir": l.in.State,
		"client_token_file": "client-token", "accounts": configured}, "", "  ")
	l.write("config.json", string(config)+"\n")
	l.write("client-token", token+"\n")
	if manager == "systemd" {
		if err := os.WriteFile(filepath.Join(l.jobs, l.in.Name), []byte(l.in.Descriptor), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	saved := signIn
	signIn = func(state, account string, noBrowser bool) (bool, error) {
		if l.refuse[account] {
			return false, errors.New("the sign-in was cancelled")
		}
		l.signed = append(l.signed, account)
		if err := os.WriteFile(filepath.Join(state, "auths", account+".json"), []byte("{}\n"), 0o600); err != nil {
			return false, err
		}
		if l.unclear[account] {
			return true, fmt.Errorf("enrolled %s, but could not clear its login requirement: disk full", account)
		}
		return true, nil
	}
	t.Cleanup(func() { signIn = saved })
	return l
}

func (l *lane) write(name, text string) {
	l.t.Helper()
	if err := os.WriteFile(filepath.Join(l.in.State, name), []byte(text), 0o600); err != nil {
		l.t.Fatal(err)
	}
}

func (l *lane) read(name string) string {
	l.t.Helper()
	data, err := os.ReadFile(filepath.Join(l.in.State, name))
	if err != nil {
		l.t.Fatal(err)
	}
	return string(data)
}

func (l *lane) running() bool {
	if l.in.Manager == "systemd" {
		data, _ := os.ReadFile(filepath.Join(l.jobs, l.in.Name+".log"))
		lines := strings.Fields(string(data))
		return len(lines) > 0 && lines[len(lines)-1] == "start"
	}
	_, err := os.Stat(filepath.Join(l.jobs, l.in.Name))
	return err == nil
}

func (l *lane) start() {
	l.t.Helper()
	if err := startService(l.in); err != nil {
		l.t.Fatal(err)
	}
}

func (l *lane) accounts() []string {
	l.t.Helper()
	cfg, err := l.in.config()
	if err != nil {
		l.t.Fatal(err)
	}
	var ids []string
	for _, a := range cfg.Accounts {
		ids = append(ids, fmt.Sprintf("%s:%v", a.ID, a.Capacity))
	}
	return ids
}

func (l *lane) shown(text string) string {
	return strings.ReplaceAll(text, l.listen, "127.0.0.1:18787")
}

func TestAccountAddSignsInAddsTheAccountAndStartsTheRouterAgain(t *testing.T) {
	l := newLane(t, "launchd", "work")
	l.start()

	var err error
	out := stdoutOf(t, func() { err = addAccount(l.in, "school", false, os.Stdout) })

	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1 school:1" {
		t.Fatalf("accounts %s, want work:1 school:1", got)
	}
	want := `stop      router stopped; assignments kept
login     school: sign in in the browser window that opened
login     school signed in
config    added school, capacity 1
start     router running on 127.0.0.1:18787 (launchd)
ready     school ready
`
	if l.shown(out) != want || !l.running() {
		t.Fatalf("output\n%s\nwant\n%s\nrunning %v", l.shown(out), want, l.running())
	}
}

func TestAFailedSignInLeavesTheConfigUnchangedAndStartsTheRouterAgain(t *testing.T) {
	l := newLane(t, "launchd", "work")
	l.start()
	l.refuse["school"] = true
	before := l.read("config.json")

	var err error
	stdoutOf(t, func() { err = addAccount(l.in, "school", false, os.Stdout) })

	if err == nil || !strings.Contains(err.Error(), "the sign-in was cancelled") {
		t.Fatalf("account add: %v, want the sign-in error", err)
	}
	if l.read("config.json") != before || !l.running() {
		t.Fatalf("config.json changed or router stopped:\n%s\nrunning %v", l.read("config.json"), l.running())
	}
}

func TestAccountAddOnSystemdStopsAndStartsItsOwnUnit(t *testing.T) {
	l := newLane(t, "systemd", "work")
	l.start()

	var err error
	out := stdoutOf(t, func() { err = addAccount(l.in, "school", true, os.Stdout) })

	if err != nil {
		t.Fatal(err)
	}
	log, _ := os.ReadFile(filepath.Join(l.jobs, l.in.Name+".log"))
	if string(log) != "start\nstop\nstart\n" || !strings.Contains(l.shown(out), "start     router running on 127.0.0.1:18787 (systemd)\n") {
		t.Fatalf("systemctl log %q, output\n%s", log, l.shown(out))
	}
	if !strings.Contains(out, "login     school: open the URL below and sign in\n") {
		t.Fatalf("output\n%s\nwant the -no-browser sign-in line", out)
	}
	if _, err := os.Stat(filepath.Join(l.jobs, l.in.Name+".enabled")); err != nil {
		t.Fatalf("the unit is not enabled after account add: %v", err)
	}
}

func journalLine(op, conversation, account string, at time.Time) string {
	return fmt.Sprintf(`{"op":%q,"c":%q,"a":%q,"why":"capacity","t":%q}`+"\n", op, conversation, account, at.UTC().Format(time.RFC3339))
}

func TestAccountRemoveRefusesWithTheCountOfRecentConversationsUnlessForced(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	now := time.Now()
	if err := os.MkdirAll(filepath.Join(l.in.State, "assignments"), 0o700); err != nil {
		t.Fatal(err)
	}
	l.write("assignments/assignments.jsonl",
		journalLine("assign", "conv-recent", "school", now.Add(-time.Hour))+
			journalLine("assign", "conv-old-used", "school", now.Add(-10*time.Hour))+
			journalLine("assign", "conv-old-idle", "school", now.Add(-10*time.Hour))+
			journalLine("assign", "conv-work", "work", now.Add(-time.Hour))+
			journalLine("assign", "conv-moved", "school", now.Add(-2*time.Hour))+
			`{"op":"migrate","c":"conv-moved","a":"work","from":"school","why":"manual","t":"`+now.Add(-90*time.Minute).UTC().Format(time.RFC3339)+`"}`+"\n")
	l.write("events.jsonl", fmt.Sprintf(`{"at":%q,"kind":"request","conversation":%q,"account":"school","status":200,"outcome":"completed"}`+"\n",
		now.Add(-2*time.Hour).UTC().Format(time.RFC3339Nano), claude.ConversationHash("conv-old-used")))
	before := l.read("config.json")

	err := removeAccount(l.in, "school", false, now, os.Stdout)

	var n nextError
	if !errors.As(err, &n) || n.err.Error() != "school has 2 conversations from the last 5 hours" ||
		n.next != "claude-router account remove school -force, and they move to another account" {
		t.Fatalf("account remove: %#v, want the refusal with the count and -force", err)
	}
	if l.read("config.json") != before || !l.running() {
		t.Fatal("the refused remove changed config.json or stopped the router")
	}

	out := stdoutOf(t, func() { err = removeAccount(l.in, "school", true, now, os.Stdout) })

	if err != nil {
		t.Fatal(err)
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1" {
		t.Fatalf("accounts %s, want work:1", got)
	}
	if _, err := os.Stat(filepath.Join(l.in.State, "auths", "school.json")); !errors.Is(err, os.ErrNotExist) {
		t.Fatalf("auths/school.json: %v, want it deleted", err)
	}
	want := `stop      router stopped; assignments kept
config    removed school
remove    auths/school.json
start     router running on 127.0.0.1:18787 (launchd)
note      school stays signed in on claude.ai until you sign it out there
`
	if l.shown(out) != want || !l.running() {
		t.Fatalf("output\n%s\nwant\n%s", l.shown(out), want)
	}
}

func TestAccountRemoveRefusesTheLastAccount(t *testing.T) {
	l := newLane(t, "launchd", "work")
	l.start()

	err := removeAccount(l.in, "work", true, time.Now(), os.Stdout)

	var n nextError
	if !errors.As(err, &n) || n.err.Error() != "work is the only account" || n.next != "claude-router account add NAME, then remove work" {
		t.Fatalf("account remove of the last account: %#v, want a refusal", err)
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1" {
		t.Fatalf("accounts %s, want work:1", got)
	}
}

func failingConfigEdit(t *testing.T) {
	saved := editConfig
	editConfig = func(string, func(*claude.Config) error) (bool, error) { return false, errors.New("disk full") }
	t.Cleanup(func() { editConfig = saved })
}

func (l *lane) logins() []string {
	l.t.Helper()
	entries, err := os.ReadDir(filepath.Join(l.in.State, "auths"))
	if err != nil {
		l.t.Fatal(err)
	}
	var names []string
	for _, e := range entries {
		names = append(names, e.Name())
	}
	return names
}

func TestAFailedConfigWriteAfterSignInRemovesTheNewLoginAndStartsTheRouterAgain(t *testing.T) {
	l := newLane(t, "launchd", "work")
	l.start()
	failingConfigEdit(t)

	var err error
	stdoutOf(t, func() { err = addAccount(l.in, "school", false, os.Stdout) })

	if err == nil || !strings.Contains(err.Error(), "disk full") {
		t.Fatalf("account add: %v, want the config write error", err)
	}
	if got := strings.Join(l.logins(), " "); got != "work.json" || !l.running() {
		t.Fatalf("logins %s, running %v; want only work.json and the router running", got, l.running())
	}
}

func TestAFailedConfigWriteDuringRemoveKeepsTheLoginAndStartsTheRouterAgain(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	failingConfigEdit(t)

	var err error
	stdoutOf(t, func() { err = removeAccount(l.in, "school", true, time.Now(), os.Stdout) })

	if err == nil || !strings.Contains(err.Error(), "disk full") {
		t.Fatalf("account remove: %v, want the config write error", err)
	}
	if got := strings.Join(l.logins(), " "); got != "school.json work.json" || !l.running() {
		t.Fatalf("logins %s, running %v; want both logins and the router running", got, l.running())
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1 school:1" {
		t.Fatalf("accounts %s, want work:1 school:1", got)
	}
}

func TestAccountRemoveThatCannotDeleteTheLoginAddsTheAccountBackAndStartsTheRouterAgain(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	auths := filepath.Join(l.in.State, "auths")
	if err := os.Chmod(auths, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(auths, 0o700) })

	var err error
	stdoutOf(t, func() { err = removeAccount(l.in, "school", true, time.Now(), os.Stdout) })

	if !errors.Is(err, os.ErrPermission) {
		t.Fatalf("account remove: %v, want the permission error", err)
	}
	if got := strings.Join(l.accounts(), " "); got != "work:1 school:1" || !l.running() {
		t.Fatalf("accounts %s, running %v; want both accounts and the router running", got, l.running())
	}
}

func TestAccountChangesRefuseWhileAnotherChangeHoldsTheLock(t *testing.T) {
	l := newLane(t, "launchd", "work", "alumni")
	l.start()
	held, err := lockActivation(l.in.State)
	if err != nil {
		t.Fatal(err)
	}
	defer held.Close()
	before := l.read("config.json")

	errs := map[string]error{
		"add":    addAccount(l.in, "school", false, io.Discard),
		"remove": removeAccount(l.in, "alumni", true, time.Now(), io.Discard),
		"setup":  runSetup(l.in, []string{"school"}, false, io.Discard),
	}

	for command, err := range errs {
		var n nextError
		if !errors.As(err, &n) || n.err.Error() != "another claude-router setup, account, activate, direct, install, or uninstall is running" {
			t.Errorf("%s: %v, want the lock refusal", command, err)
		}
	}
	if l.read("config.json") != before || len(l.signed) != 0 || !l.running() {
		t.Fatalf("config changed, signed in %v, or router stopped (running %v)", l.signed, l.running())
	}
}

func TestAccountRemoveRefusesAConversationAssignedBeforeTheRouterStopped(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	now := time.Now()
	if err := os.MkdirAll(filepath.Join(l.in.State, "assignments"), 0o700); err != nil {
		t.Fatal(err)
	}
	l.write("assignments/assignments.jsonl", journalLine("assign", "conv-work", "work", now.Add(-time.Hour)))
	l.queried = func() {
		l.write("assignments/assignments.jsonl", journalLine("assign", "conv-work", "work", now.Add(-time.Hour))+
			journalLine("assign", "conv-late", "school", now.Add(-time.Second)))
	}
	before := l.read("config.json")

	var err error
	stdoutOf(t, func() { err = removeAccount(l.in, "school", false, now, os.Stdout) })

	var n nextError
	if !errors.As(err, &n) || n.err.Error() != "school has 1 conversation from the last 5 hours" {
		t.Fatalf("account remove: %v, want the refusal for the conversation assigned before the stop", err)
	}
	if l.read("config.json") != before || strings.Join(l.logins(), " ") != "school.json work.json" || !l.running() {
		t.Fatalf("config or logins changed, or the router stayed stopped (running %v)", l.running())
	}
}

func TestAccountAddAndRemoveLeaveAStoppedRouterStopped(t *testing.T) {
	l := newLane(t, "launchd", "work")

	var addErr, removeErr error
	added := stdoutOf(t, func() { addErr = addAccount(l.in, "school", false, os.Stdout) })
	removed := stdoutOf(t, func() { removeErr = removeAccount(l.in, "school", true, time.Now(), os.Stdout) })

	if addErr != nil || removeErr != nil {
		t.Fatalf("account add: %v; account remove: %v", addErr, removeErr)
	}
	if l.running() {
		t.Fatal("account add or remove started a router that was stopped")
	}
	want := `login     school: sign in in the browser window that opened
login     school signed in
config    added school, capacity 1
next      claude-router start
`
	if added != want {
		t.Fatalf("account add output\n%s\nwant\n%s", added, want)
	}
	want = `config    removed school
remove    auths/school.json
note      school stays signed in on claude.ai until you sign it out there
`
	if removed != want {
		t.Fatalf("account remove output\n%s\nwant\n%s", removed, want)
	}
}

func TestAccountRejectsTheFlagOfTheOtherSubcommand(t *testing.T) {
	state := t.TempDir()
	for _, args := range [][]string{{"add", "school", "-state", state, "-force"}, {"remove", "school", "-state", state, "-no-browser"}} {
		err := account(args)

		if err != silentExit(2) {
			t.Errorf("account %v: %v, want exit 2 for the flag", args, err)
		}
	}
}

// unsyncedState lets config.json be replaced but not reopened, so EditConfig fails after its rename.
func (l *lane) unsyncedState() {
	l.t.Helper()
	if err := os.Chmod(l.in.State, 0o300); err != nil {
		l.t.Fatal(err)
	}
	l.t.Cleanup(func() { os.Chmod(l.in.State, 0o700) })
}

func TestAConfigWriteThatFailsAfterItsRenameKeepsTheLoginsOfTheAccountsItAdded(t *testing.T) {
	t.Run("account add", func(t *testing.T) {
		l := newLane(t, "launchd", "work")
		l.start()
		l.unsyncedState()

		var err error
		stdoutOf(t, func() { err = addAccount(l.in, "school", false, os.Stdout) })

		if !errors.Is(err, os.ErrPermission) {
			t.Fatalf("account add: %v, want the directory sync error", err)
		}
		if got := strings.Join(l.accounts(), " ") + "; " + strings.Join(l.logins(), " "); got != "work:1 school:1; school.json work.json" || !l.running() {
			t.Fatalf("accounts; logins %s, running %v; want school in both and the router running", got, l.running())
		}
	})
	t.Run("setup", func(t *testing.T) {
		l := setupLane(t)
		l.unsyncedState()

		var err error
		stdoutOf(t, func() { err = runSetup(l.in, []string{"work", "alumni"}, false, os.Stdout) })

		if !errors.Is(err, os.ErrPermission) {
			t.Fatalf("setup: %v, want the directory sync error", err)
		}
		if got := strings.Join(l.accounts(), " ") + "; " + strings.Join(l.logins(), " "); got != "work:1 alumni:1; alumni.json work.json" {
			t.Fatalf("accounts; logins %s, want both accounts in both", got)
		}
	})
}

func TestAConfigWriteThatFailsAfterItsRenameStillDeletesTheRemovedLogin(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	l.unsyncedState()

	var err error
	stdoutOf(t, func() { err = removeAccount(l.in, "school", true, time.Now(), os.Stdout) })

	if !errors.Is(err, os.ErrPermission) {
		t.Fatalf("account remove: %v, want the directory sync error", err)
	}
	if got := strings.Join(l.accounts(), " ") + "; " + strings.Join(l.logins(), " "); got != "work:1; work.json" || !l.running() {
		t.Fatalf("accounts; logins %s, running %v; want only work in both and the router running", got, l.running())
	}
}

func TestARemoveWhoseConfigWriteFailsKeepsTheLoginWhenAuthsTurnsReadOnly(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	auths := filepath.Join(l.in.State, "auths")
	saved := editConfig
	editConfig = func(string, func(*claude.Config) error) (bool, error) {
		if err := os.Chmod(auths, 0o500); err != nil {
			t.Fatal(err)
		}
		return false, errors.New("disk full")
	}
	t.Cleanup(func() { editConfig = saved; os.Chmod(auths, 0o700) })

	var err error
	stdoutOf(t, func() { err = removeAccount(l.in, "school", true, time.Now(), os.Stdout) })

	if err == nil || !strings.Contains(err.Error(), "disk full") {
		t.Fatalf("account remove: %v, want the config write error", err)
	}
	if got := strings.Join(l.accounts(), " ") + "; " + strings.Join(l.logins(), " "); got != "work:1 school:1; school.json work.json" || !l.running() {
		t.Fatalf("accounts; logins %s, running %v; want both accounts with their logins and the router running", got, l.running())
	}
}

func (l *lane) enrolledWithoutLogin() []string {
	l.t.Helper()
	var missing []string
	for _, id := range l.accounts() {
		name := strings.Split(id, ":")[0]
		if _, err := os.Stat(filepath.Join(l.in.State, "auths", name+".json")); err != nil {
			missing = append(missing, name)
		}
	}
	return missing
}

type killed struct{}

func TestAnAccountRemoveKilledAroundItsConfigWriteLeavesEveryAccountWithItsLogin(t *testing.T) {
	for _, when := range []string{"before", "after"} {
		t.Run("killed "+when+" the config write", func(t *testing.T) {
			l := newLane(t, "launchd", "work", "school")
			l.start()
			saved := editConfig
			editConfig = func(path string, edit func(*claude.Config) error) (bool, error) {
				if when == "after" {
					if _, err := saved(path, edit); err != nil {
						t.Fatal(err)
					}
				}
				panic(killed{})
			}
			t.Cleanup(func() { editConfig = saved })

			func() {
				defer func() {
					if r := recover(); r != (killed{}) {
						panic(r)
					}
				}()
				_ = removeAccount(l.in, "school", true, time.Now(), io.Discard)
			}()

			if missing := l.enrolledWithoutLogin(); len(missing) > 0 {
				t.Fatalf("%v enrolled without a login in auths/", missing)
			}
		})
	}
}

func TestASignInThatFailsAfterWritingTheLoginRemovesIt(t *testing.T) {
	t.Run("account add", func(t *testing.T) {
		l := newLane(t, "launchd", "work")
		l.start()
		l.unclear["school"] = true
		before := l.read("config.json")

		var err error
		stdoutOf(t, func() { err = addAccount(l.in, "school", false, os.Stdout) })

		if err == nil || !strings.Contains(err.Error(), "could not clear its login requirement") {
			t.Fatalf("account add: %v, want the sign-in error", err)
		}
		if got := strings.Join(l.logins(), " "); got != "work.json" || l.read("config.json") != before || !l.running() {
			t.Fatalf("logins %s, config changed %v, running %v; want only work.json, config unchanged, router running",
				got, l.read("config.json") != before, l.running())
		}
	})
	t.Run("setup", func(t *testing.T) {
		l := setupLane(t)
		l.unclear["alumni"] = true

		var err error
		stdoutOf(t, func() { err = runSetup(l.in, []string{"work", "alumni"}, false, os.Stdout) })

		if err == nil || !strings.Contains(err.Error(), "could not clear its login requirement") {
			t.Fatalf("setup: %v, want the sign-in error", err)
		}
		if got := strings.Join(l.accounts(), " ") + "; " + strings.Join(l.logins(), " "); got != "work:1; work.json" {
			t.Fatalf("accounts; logins %s, want only work in both", got)
		}
	})
}

func TestAPartlyFailedStopRestoresTheEnabledRunningRouter(t *testing.T) {
	for name, change := range map[string]func(l *lane) error{
		"account add":    func(l *lane) error { return addAccount(l.in, "school", false, io.Discard) },
		"account remove": func(l *lane) error { return removeAccount(l.in, "alumni", true, time.Now(), io.Discard) },
		"setup":          func(l *lane) error { return runSetup(l.in, []string{"school"}, false, io.Discard) },
	} {
		t.Run(name, func(t *testing.T) {
			l := newLane(t, "systemd", "work", "alumni")
			l.in.Python, l.in.Helper, l.in.Manifest = "/bin/sh", "/lib/claude_router.py", "/state/manifest.json"
			l.start()
			before := l.read("config.json")
			t.Setenv("FAKE_FAIL", "stop")

			err := change(l)

			if err == nil {
				t.Fatal("the change succeeded although systemctl stop failed")
			}
			if _, statErr := os.Stat(filepath.Join(l.jobs, l.in.Name+".enabled")); statErr != nil || !l.running() {
				t.Fatalf("after %v: enabled %v, running %v; want the unit enabled and running", err, statErr == nil, l.running())
			}
			if l.read("config.json") != before || len(l.signed) != 0 {
				t.Fatalf("config changed or signed in %v", l.signed)
			}
		})
	}
}

func TestAccountRemoveThatCannotDeleteTheLoginOrAddTheAccountBackNamesTheLogin(t *testing.T) {
	l := newLane(t, "launchd", "work", "school")
	l.start()
	auths := filepath.Join(l.in.State, "auths")
	if err := os.Chmod(auths, 0o500); err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { os.Chmod(auths, 0o700) })
	saved, edits := editConfig, 0
	editConfig = func(path string, edit func(*claude.Config) error) (bool, error) {
		if edits++; edits > 1 {
			return false, errors.New("disk full")
		}
		return saved(path, edit)
	}
	t.Cleanup(func() { editConfig = saved })

	var err error
	stdoutOf(t, func() { err = removeAccount(l.in, "school", true, time.Now(), os.Stdout) })

	login := filepath.Join(auths, "school.json")
	var n nextError
	if !errors.As(err, &n) || !strings.Contains(err.Error(), "permission denied") || n.next != "delete "+login+", then claude-router start" {
		t.Fatalf("account remove: %#v, want the errors and the step that names %s", err, login)
	}
	if _, err := os.Stat(login); err != nil {
		t.Fatalf("the login is gone: %v", err)
	}
}
