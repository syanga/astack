// Package claude serves Claude Code clients through the pinned CLIProxyAPI
// SDK. It authenticates clients with a private token, binds each
// conversation to an account through internal/router, pins every upstream
// attempt to that account, and refuses dispatch when an account's
// included-only state is unverified.
package claude

import (
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// Config is the service configuration. The service reads it once at start;
// changing it takes a restart.
type Config struct {
	// Listen is the client-facing address. It must be a loopback IP and port.
	Listen string `json:"listen"`
	// StateDir holds the SDK credentials (auths/), the assignment journal
	// (assignments/), the generated SDK configuration, and events.jsonl.
	StateDir string `json:"state_dir"`
	// ClientTokenFile holds the private client token. It must be readable only
	// by its owner. LoadConfig resolves a relative path against the directory
	// of the configuration file.
	ClientTokenFile string `json:"client_token_file"`
	// Accounts lists enrolled accounts in tie-break order. Each account's
	// credential is auths/<id>.json in StateDir.
	Accounts []router.Account `json:"accounts"`
	// OverageFreshFor is how long a reading that paid overflow is disabled
	// lets the router dispatch to the account. Default 30 minutes.
	OverageFreshFor Duration `json:"overage_fresh_for,omitzero"`
	// OverageCheckEvery is the interval of the background settings read.
	// Default 10 minutes. It must be shorter than OverageFreshFor.
	OverageCheckEvery Duration `json:"overage_check_every,omitzero"`
	// MaxUpstreamAttempts caps the upstream attempts of one client request,
	// from 1 to 4. Default 4: three router dispatches and the SDK's
	// redispatch after a 401. With 1 the router never resends a request that
	// may have reached the provider, so the number of upstream attempts
	// equals the number of client requests. A network failure before the
	// request was written is not an upstream attempt.
	MaxUpstreamAttempts int `json:"max_upstream_attempts,omitempty"`
	// TestUpstream redirects requests for api.anthropic.com to a loopback
	// URL. It exists for controlled-upstream lanes and is reported as an
	// event at start.
	TestUpstream string `json:"test_upstream,omitempty"`
	// MoveCacheWarnPercent is the share of the last 24 hours' cache writes
	// that account moves may cause before claude-router status warns. It
	// runs from 0 to 100, and 0 turns the warning off. Default 10.
	MoveCacheWarnPercent *int `json:"move_cache_warn_percent,omitempty"`
}

func (c Config) MoveCacheWarn() (int, error) {
	if c.MoveCacheWarnPercent == nil {
		return defaultMoveCacheWarn, nil
	}
	if p := *c.MoveCacheWarnPercent; p < 0 || p > 100 {
		return 0, fmt.Errorf("move_cache_warn_percent: %d is outside 0 to 100", p)
	}
	return *c.MoveCacheWarnPercent, nil
}

// Duration is a time.Duration that reads and writes Go duration strings.
type Duration time.Duration

func (d Duration) MarshalJSON() ([]byte, error) { return json.Marshal(time.Duration(d).String()) }

func (d *Duration) UnmarshalJSON(b []byte) error {
	var s string
	if err := json.Unmarshal(b, &s); err != nil {
		return err
	}
	v, err := time.ParseDuration(s)
	if err != nil {
		return err
	}
	*d = Duration(v)
	return nil
}

const (
	defaultOverageFreshFor   = 30 * time.Minute
	defaultOverageCheckEvery = 10 * time.Minute
	minClientTokenLength     = 32
	defaultMaxAttempts       = 4
	defaultMoveCacheWarn     = 10
)

// LoadConfig reads a JSON configuration file.
func LoadConfig(path string) (Config, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Config{}, err
	}
	var cfg Config
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&cfg); err != nil {
		return Config{}, fmt.Errorf("%s: %w", path, err)
	}
	if cfg.ClientTokenFile != "" && !filepath.IsAbs(cfg.ClientTokenFile) {
		abs, err := filepath.Abs(path)
		if err != nil {
			return Config{}, err
		}
		cfg.ClientTokenFile = filepath.Join(filepath.Dir(abs), cfg.ClientTokenFile)
	}
	return cfg, nil
}

// EditConfig applies edit to the configuration file at path and replaces the
// file atomically. It refuses an edit that fails or leaves an invalid
// configuration, and then leaves the file unchanged. published reports that
// the new file has replaced the old one; an error with published set comes
// from syncing the directory afterwards, and the edit is in effect.
func EditConfig(path string, edit func(*Config) error) (published bool, err error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return false, err
	}
	var cfg Config
	dec := json.NewDecoder(strings.NewReader(string(data)))
	dec.DisallowUnknownFields()
	if err := dec.Decode(&cfg); err != nil {
		return false, fmt.Errorf("%s: %w", path, err)
	}
	if err := edit(&cfg); err != nil {
		return false, err
	}
	if err := cfg.withDefaults().validate(); err != nil {
		return false, fmt.Errorf("%s: %w", path, err)
	}
	out, err := json.MarshalIndent(cfg, "", "  ")
	if err != nil {
		return false, err
	}
	tmp, err := os.CreateTemp(filepath.Dir(path), ".config-*.json")
	if err != nil {
		return false, err
	}
	defer os.Remove(tmp.Name())
	if _, err := tmp.Write(append(out, '\n')); err != nil {
		tmp.Close()
		return false, err
	}
	if err := tmp.Chmod(0o600); err != nil {
		tmp.Close()
		return false, err
	}
	if err := tmp.Sync(); err != nil {
		tmp.Close()
		return false, err
	}
	if err := tmp.Close(); err != nil {
		return false, err
	}
	if err := os.Rename(tmp.Name(), path); err != nil {
		return false, err
	}
	if err := syncDir(filepath.Dir(path)); err != nil {
		return true, fmt.Errorf("%s is written, but its directory did not sync: %w", path, err)
	}
	return true, nil
}

func syncDir(path string) error {
	dir, err := os.Open(path)
	if err != nil {
		return err
	}
	defer dir.Close()
	return dir.Sync()
}

func (c Config) withDefaults() Config {
	if c.OverageFreshFor == 0 {
		c.OverageFreshFor = Duration(defaultOverageFreshFor)
	}
	if c.OverageCheckEvery == 0 {
		c.OverageCheckEvery = Duration(defaultOverageCheckEvery)
	}
	if c.MaxUpstreamAttempts == 0 {
		c.MaxUpstreamAttempts = defaultMaxAttempts
	}
	return c
}

func (c Config) validate() error {
	host, _, err := net.SplitHostPort(c.Listen)
	if err != nil {
		return fmt.Errorf("listen: %w", err)
	}
	if ip := net.ParseIP(host); ip == nil || !ip.IsLoopback() {
		return fmt.Errorf("listen: %q is not a loopback IP address", host)
	}
	if c.StateDir == "" || !filepath.IsAbs(c.StateDir) {
		return errors.New("state_dir: need an absolute path")
	}
	if len(c.Accounts) == 0 {
		return errors.New("accounts: enroll at least one account")
	}
	for _, a := range c.Accounts {
		if !validAccountID(string(a.ID)) {
			return fmt.Errorf("accounts: %q is not a valid account ID (letters, digits, '-', '_', '.')", a.ID)
		}
	}
	if c.OverageFreshFor <= 0 || c.OverageCheckEvery <= 0 {
		return errors.New("overage_fresh_for and overage_check_every must be positive")
	}
	if c.OverageCheckEvery >= c.OverageFreshFor {
		return errors.New("overage_check_every must be shorter than overage_fresh_for")
	}
	if c.MaxUpstreamAttempts < 1 || c.MaxUpstreamAttempts > defaultMaxAttempts {
		return fmt.Errorf("max_upstream_attempts: %d is outside 1 to %d", c.MaxUpstreamAttempts, defaultMaxAttempts)
	}
	if _, err := c.MoveCacheWarn(); err != nil {
		return err
	}
	if c.TestUpstream != "" {
		u, err := url.Parse(c.TestUpstream)
		if err != nil || u.Scheme != "http" {
			return fmt.Errorf("test_upstream: need an http:// loopback URL")
		}
		if ip := net.ParseIP(u.Hostname()); ip == nil || !ip.IsLoopback() {
			return fmt.Errorf("test_upstream: %q is not a loopback IP address", u.Hostname())
		}
	}
	return nil
}

// ValidAccountID reports whether id can name an account and its credential
// file: letters, digits, '-', '_', and '.', and not "." or "..".
func ValidAccountID(id string) bool { return validAccountID(id) }

func validAccountID(id string) bool {
	if id == "" || id == "." || id == ".." {
		return false
	}
	for _, r := range id {
		ok := r >= 'a' && r <= 'z' || r >= 'A' && r <= 'Z' || r >= '0' && r <= '9' || r == '-' || r == '_' || r == '.'
		if !ok {
			return false
		}
	}
	return true
}

func readClientToken(path string) (string, error) {
	info, err := os.Stat(path)
	if err != nil {
		return "", fmt.Errorf("client_token_file: %w", err)
	}
	if info.Mode().Perm()&0o077 != 0 {
		return "", fmt.Errorf("client_token_file: mode %v lets other users read it; use 0600", info.Mode().Perm())
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return "", fmt.Errorf("client_token_file: %w", err)
	}
	token := strings.TrimSpace(string(data))
	if len(token) < minClientTokenLength {
		return "", fmt.Errorf("client_token_file: token shorter than %d characters", minClientTokenLength)
	}
	return token, nil
}
