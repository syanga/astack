package claude

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"sort"
	"strings"

	coreauth "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/auth"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// credentialKeys are the metadata keys the SDK's Claude login and its own
// persistence write. Any other key can change where or how the SDK sends a
// request: proxy_url and base_url bypass the router's transport, headers can
// replace Authorization, and retry or cooling keys break the single-attempt
// posture (CONTRACT.md, "Executor posture").
var credentialKeys = map[string]bool{
	"type": true, "email": true, "access_token": true, "refresh_token": true,
	"id_token": true, "last_refresh": true, "expired": true, "refreshed": true,
	"account_uuid": true, "organization_uuid": true, "organization_name": true,
	"claude_device_ids": true, "claude_account_profile_checked_at": true,
	"skip_account_profile": true, "disabled": true,
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
	coreauth.NormalizeCredentialMetadata(meta)
	var keys []string
	for key := range meta {
		keys = append(keys, key)
	}
	sort.Strings(keys)
	for _, key := range keys {
		if !credentialKeys[key] {
			return fmt.Errorf("%s: carries %q, which the router does not allow", filepath.Base(path), key)
		}
	}
	if disabled, _ := meta["disabled"].(bool); disabled {
		return fmt.Errorf("%s: the credential is disabled", filepath.Base(path))
	}
	return nil
}
