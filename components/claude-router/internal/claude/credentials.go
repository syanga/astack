package claude

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// credentialOverrides are credential metadata keys that change how the SDK
// dispatches or where it sends a request. A proxy bypasses the router's
// transport, and retry or cooling overrides break the single-attempt posture
// (CONTRACT.md, "Executor posture").
var credentialOverrides = []string{
	"proxy_url", "base_url",
	"request_retry", "request-retry",
	"disable_cooling", "disable-cooling",
	"request_scoped_errors", "request-scoped-errors",
	"disabled",
}

func credentialFile(stateDir string, id router.AccountID) string {
	return filepath.Join(authDir(stateDir), string(id)+".json")
}

func authDir(stateDir string) string { return filepath.Join(stateDir, "auths") }

func checkCredentials(stateDir string, accounts []router.Account) error {
	enrolled := map[string]bool{}
	for _, a := range accounts {
		enrolled[string(a.ID)+".json"] = true
		if err := CheckCredential(credentialFile(stateDir, a.ID)); err != nil {
			return fmt.Errorf("account %s: %w", a.ID, err)
		}
	}
	entries, err := os.ReadDir(authDir(stateDir))
	if err != nil {
		return err
	}
	for _, e := range entries {
		if strings.HasSuffix(e.Name(), ".json") && !enrolled[e.Name()] {
			return fmt.Errorf("auth directory holds %s, which is not an enrolled account", e.Name())
		}
	}
	return nil
}

// CheckCredential validates one SDK credential file for enrollment.
func CheckCredential(path string) error {
	info, err := os.Stat(path)
	if err != nil {
		return err
	}
	if info.Mode().Perm()&0o077 != 0 {
		return fmt.Errorf("%s: mode %v lets other users read it; use 0600", filepath.Base(path), info.Mode().Perm())
	}
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	var meta map[string]any
	if err := json.Unmarshal(data, &meta); err != nil {
		return fmt.Errorf("%s: not a JSON credential", filepath.Base(path))
	}
	if t, _ := meta["type"].(string); t != "claude" {
		return fmt.Errorf("%s: type %q, want claude", filepath.Base(path), t)
	}
	if tok, _ := meta["access_token"].(string); tok == "" {
		return fmt.Errorf("%s: no access token", filepath.Base(path))
	}
	for _, key := range credentialOverrides {
		if v, set := meta[key]; set && !zeroValue(v) {
			return fmt.Errorf("%s: carries %q, which the router does not allow", filepath.Base(path), key)
		}
	}
	return nil
}

func zeroValue(v any) bool {
	switch t := v.(type) {
	case nil:
		return true
	case string:
		return strings.TrimSpace(t) == ""
	case bool:
		return !t
	case float64:
		return t == 0
	}
	return false
}
