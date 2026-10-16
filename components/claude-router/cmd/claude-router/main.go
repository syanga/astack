// Command claude-router serves Claude Code clients across subscription
// accounts through the pinned CLIProxyAPI SDK, and operates the service the
// astack installer set up.
package main

import (
	"bufio"
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"
	"time"

	sdkauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/auth"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/config"
	log "github.com/sirupsen/logrus"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	commands := map[string]func([]string) error{
		"serve": serve, "login": login, "token": token, "status": status,
		"start": start, "stop": stop, "move": move, "direct": direct,
	}
	cmd, ok := commands[os.Args[1]]
	if !ok {
		usage()
	}
	if err := cmd(os.Args[2:]); err != nil {
		var n nextError
		if errors.As(err, &n) {
			fmt.Fprintf(os.Stderr, "claude-router: %s: %v\n", os.Args[1], n.err)
			if n.detail != "" {
				fmt.Fprintf(os.Stderr, "        %s\n", n.detail)
			}
			fmt.Fprintf(os.Stderr, "next    %s\n", n.next)
		} else {
			fmt.Fprintf(os.Stderr, "claude-router: %s: %v\n", os.Args[1], err)
		}
		os.Exit(1)
	}
}

type nextError struct {
	err    error
	detail string
	next   string
}

func (e nextError) Error() string { return e.err.Error() }

func usage() {
	fmt.Fprintln(os.Stderr, `usage:
  claude-router status [-v] [-state DIR]
  claude-router start [-state DIR]
  claude-router stop [-state DIR]
  claude-router login -account ID [-no-browser] [-state DIR]
  claude-router move -conversation HASH -to ID [-state DIR]
  claude-router direct [-prepare] [-state DIR]
  claude-router serve -config FILE [-supervised]
  claude-router token -out FILE

-state defaults to $XDG_STATE_HOME/astack/claude-router or
~/.local/state/astack/claude-router.`)
	os.Exit(2)
}

func stateFlag(fs *flag.FlagSet) *string {
	return fs.String("state", defaultState(), "router state directory")
}

func serve(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	path := fs.String("config", "", "service configuration (JSON)")
	supervised := fs.Bool("supervised", false, "run under a service manager: bound restarts and record failures in the state directory")
	_ = fs.Parse(args)
	if *path == "" {
		return errors.New("-config is required")
	}
	begun := time.Now().Round(0)
	dir := filepath.Dir(*path)
	if *supervised {
		if !filepath.IsAbs(*path) {
			return errors.New("-supervised needs an absolute -config path")
		}
		sup, err := loadSupervision(dir)
		if err != nil {
			fmt.Fprintf(os.Stderr, "claude-router: %s unreadable, starting a new record: %v\n", supervisionFile, err)
			sup = supervision{}
		}
		allowed := sup.begin(begun)
		if err := sup.save(dir); err != nil {
			fmt.Fprintf(os.Stderr, "claude-router: cannot record this start in %s: %v; not starting\n", supervisionFile, err)
			return nil
		}
		if !allowed {
			fmt.Fprintf(os.Stderr, "claude-router: %d starts in %s without a clean stop; not starting again until claude-router start\n", crashLimit, shortDuration(crashWindow))
			return nil
		}
	}
	failed := func(err error) error {
		if *supervised {
			if s, lerr := loadSupervision(dir); lerr == nil {
				s.fail(time.Now(), err)
				_ = s.save(dir)
			}
		}
		return err
	}
	cfg, err := claude.LoadConfig(*path)
	if err != nil {
		return failed(err)
	}
	// The service needs Info entries enabled: it waits for an SDK Info
	// entry during startup. Only warnings and errors reach stderr.
	log.SetLevel(log.InfoLevel)
	log.SetOutput(io.Discard)
	log.AddHook(stderrHook{})
	svc, err := claude.Start(cfg, claude.Options{})
	if err != nil {
		return failed(err)
	}
	if *supervised {
		fmt.Fprintf(os.Stderr, "claude-router: serving on http://%s\n", svc.Addr())
	} else {
		fmt.Fprintf(os.Stderr, "claude-router: serving on http://%s in the foreground; nothing restarts it if it exits\n", svc.Addr())
	}
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	svc.Close()
	if *supervised {
		if s, err := loadSupervision(dir); err == nil {
			s.stopped(begun)
			_ = s.save(dir)
		}
	}
	return nil
}

type stderrHook struct{}

func (stderrHook) Levels() []log.Level {
	return []log.Level{log.PanicLevel, log.FatalLevel, log.ErrorLevel, log.WarnLevel}
}

func (stderrHook) Fire(e *log.Entry) error {
	line, err := e.String()
	if err != nil {
		return err
	}
	_, err = io.WriteString(os.Stderr, line)
	return err
}

// login runs the SDK's Claude OAuth login into a staging directory and
// installs the credential as auths/<account>.json. It refuses while a
// service holds the state directory: a running service writes its
// in-memory credentials back to disk and would overwrite the new one.
func login(args []string) error {
	fs := flag.NewFlagSet("login", flag.ExitOnError)
	state := stateFlag(fs)
	account := fs.String("account", "", "account ID to enroll")
	noBrowser := fs.Bool("no-browser", false, "print the login URL instead of opening a browser")
	_ = fs.Parse(args)
	if *state == "" || !filepath.IsAbs(*state) || *account == "" {
		return errors.New("-state (absolute) and -account are required")
	}
	if !claude.ValidAccountID(*account) {
		return fmt.Errorf("%q is not a valid account ID (letters, digits, '-', '_', '.')", *account)
	}
	lock, err := router.OpenStore(filepath.Join(*state, "assignments"))
	if errors.Is(err, router.ErrLocked) {
		pid := "pid unknown"
		if in, err := loadInstallation(*state); err == nil {
			if st, err := fetchStatus(in); err == nil {
				pid = fmt.Sprintf("pid %d", st.PID)
			}
		}
		return nextError{err: fmt.Errorf("router running on this state directory (%s)", pid), next: "claude-router stop"}
	}
	if err != nil {
		return err
	}
	defer lock.Close()

	auths := filepath.Join(*state, "auths")
	if err := os.MkdirAll(auths, 0o700); err != nil {
		return err
	}
	staging, err := os.MkdirTemp(*state, ".login-")
	if err != nil {
		return err
	}
	defer os.RemoveAll(staging)

	store := sdkauth.NewFileTokenStore()
	store.SetBaseDir(staging)
	mgr := sdkauth.NewManager(store, sdkauth.NewClaudeAuthenticator())
	cfg := &config.Config{}
	cfg.AuthDir = staging
	reader := bufio.NewReader(os.Stdin)
	opts := &sdkauth.LoginOptions{
		NoBrowser: *noBrowser,
		Prompt: func(p string) (string, error) {
			fmt.Fprint(os.Stderr, p)
			line, err := reader.ReadString('\n')
			return strings.TrimSpace(line), err
		},
	}
	if *noBrowser {
		fmt.Printf("login   open the URL below; sign in as %s (Ctrl-C cancels)\n", *account)
	} else {
		fmt.Printf("login   browser opened; sign in as %s (Ctrl-C cancels)\n", *account)
	}
	if _, _, err := mgr.Login(context.Background(), "claude", cfg, opts); err != nil {
		return err
	}
	entries, err := filepath.Glob(filepath.Join(staging, "*.json"))
	if err != nil || len(entries) != 1 {
		return fmt.Errorf("expected one credential file, found %d", len(entries))
	}
	if err := os.Chmod(entries[0], 0o600); err != nil {
		return err
	}
	if err := claude.CheckCredential(entries[0]); err != nil {
		return err
	}
	dest := filepath.Join(auths, *account+".json")
	if err := os.Rename(entries[0], dest); err != nil {
		return err
	}
	if err := lock.ClearNeedsLogin(router.AccountID(*account)); err != nil {
		return fmt.Errorf("enrolled %s, but could not clear its login requirement: %w", *account, err)
	}
	kept := 0
	if bindings, err := lock.Bindings(); err == nil {
		for _, b := range bindings {
			if b.Account == router.AccountID(*account) {
				kept++
			}
		}
	}
	fmt.Printf("login   %s enrolled, login requirement cleared, %d conversations kept\n", *account, kept)
	if in, err := loadInstallation(*state); err == nil {
		if c, err := in.config(); err == nil && !enrolled(c, *account) {
			fmt.Printf("next    add {\"id\": %q, \"capacity\": 1} to the accounts in %s, then claude-router start\n", *account, in.Config)
			return nil
		}
	}
	fmt.Println("next    claude-router start")
	return nil
}

func enrolled(c claude.Config, account string) bool {
	for _, a := range c.Accounts {
		if string(a.ID) == account {
			return true
		}
	}
	return false
}

func token(args []string) error {
	fs := flag.NewFlagSet("token", flag.ExitOnError)
	out := fs.String("out", "", "token file to create")
	_ = fs.Parse(args)
	if *out == "" {
		return errors.New("token: -out is required")
	}
	b := make([]byte, 32)
	if _, err := rand.Read(b); err != nil {
		return err
	}
	f, err := os.OpenFile(*out, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o600)
	if err != nil {
		return err
	}
	defer f.Close()
	_, err = fmt.Fprintln(f, hex.EncodeToString(b))
	return err
}
