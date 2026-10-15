// Command claude-router serves Claude Code clients across subscription
// accounts through the pinned CLIProxyAPI SDK.
//
//	claude-router serve -config FILE
//	claude-router login -state DIR -account ID [-no-browser]
//	claude-router token -out FILE
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
	var err error
	switch os.Args[1] {
	case "serve":
		err = serve(os.Args[2:])
	case "login":
		err = login(os.Args[2:])
	case "token":
		err = token(os.Args[2:])
	default:
		usage()
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "claude-router:", err)
		os.Exit(1)
	}
}

func usage() {
	fmt.Fprintln(os.Stderr, `usage:
  claude-router serve -config FILE
  claude-router login -state DIR -account ID [-no-browser]
  claude-router token -out FILE`)
	os.Exit(2)
}

func serve(args []string) error {
	fs := flag.NewFlagSet("serve", flag.ExitOnError)
	path := fs.String("config", "", "service configuration (JSON)")
	_ = fs.Parse(args)
	if *path == "" {
		return errors.New("serve: -config is required")
	}
	cfg, err := claude.LoadConfig(*path)
	if err != nil {
		return err
	}
	// The service needs Info entries enabled: it waits for an SDK Info
	// entry during startup. Only warnings and errors reach stderr.
	log.SetLevel(log.InfoLevel)
	log.SetOutput(io.Discard)
	log.AddHook(stderrHook{})
	svc, err := claude.Start(cfg, claude.Options{})
	if err != nil {
		return err
	}
	fmt.Fprintf(os.Stderr, "claude-router: serving on http://%s\n", svc.Addr())
	sig := make(chan os.Signal, 1)
	signal.Notify(sig, syscall.SIGINT, syscall.SIGTERM)
	<-sig
	svc.Close()
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
	state := fs.String("state", "", "router state directory")
	account := fs.String("account", "", "account ID to enroll")
	noBrowser := fs.Bool("no-browser", false, "print the login URL instead of opening a browser")
	_ = fs.Parse(args)
	if *state == "" || !filepath.IsAbs(*state) || *account == "" {
		return errors.New("login: -state (absolute) and -account are required")
	}
	if !claude.ValidAccountID(*account) {
		return fmt.Errorf("login: %q is not a valid account ID (letters, digits, '-', '_', '.')", *account)
	}
	lock, err := router.OpenStore(filepath.Join(*state, "assignments"))
	if errors.Is(err, router.ErrLocked) {
		return errors.New("login: the router is running on this state directory; stop it first")
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
	if _, _, err := mgr.Login(context.Background(), "claude", cfg, opts); err != nil {
		return fmt.Errorf("login: %w", err)
	}
	entries, err := filepath.Glob(filepath.Join(staging, "*.json"))
	if err != nil || len(entries) != 1 {
		return fmt.Errorf("login: expected one credential file, found %d", len(entries))
	}
	if err := os.Chmod(entries[0], 0o600); err != nil {
		return err
	}
	if err := claude.CheckCredential(entries[0]); err != nil {
		return fmt.Errorf("login: %w", err)
	}
	dest := filepath.Join(auths, *account+".json")
	if err := os.Rename(entries[0], dest); err != nil {
		return err
	}
	if err := lock.ClearNeedsLogin(router.AccountID(*account)); err != nil {
		return fmt.Errorf("login: enrolled %s, but could not clear its login requirement: %w", *account, err)
	}
	fmt.Fprintf(os.Stderr, "claude-router: enrolled %s; restart the router to apply\n", *account)
	return nil
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
