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
	// by its owner.
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
	// TestUpstream redirects requests for api.anthropic.com to a loopback
	// URL. It exists for controlled-upstream lanes and is reported as an
	// event at start.
	TestUpstream string `json:"test_upstream,omitempty"`
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
	return cfg, nil
}

func (c Config) withDefaults() Config {
	if c.OverageFreshFor == 0 {
		c.OverageFreshFor = Duration(defaultOverageFreshFor)
	}
	if c.OverageCheckEvery == 0 {
		c.OverageCheckEvery = Duration(defaultOverageCheckEvery)
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
