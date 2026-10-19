package claude

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func writeConfigFile(t *testing.T, text string) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "config.json")
	if err := os.WriteFile(path, []byte(text), 0o600); err != nil {
		t.Fatal(err)
	}
	return path
}

func TestEditConfigWritesTheEditAndKeepsEveryOtherField(t *testing.T) {
	path := writeConfigFile(t, `{"listen": "127.0.0.1:18787", "state_dir": "/state", "client_token_file": "client-token",
		"accounts": [{"id": "work", "capacity": 5}], "move_cache_warn_percent": 20}`)

	err := EditConfig(path, func(c *Config) error {
		c.Accounts = append(c.Accounts, router.Account{ID: "alumni", Capacity: 1})
		return nil
	})

	if err != nil {
		t.Fatal(err)
	}
	data, _ := os.ReadFile(path)
	want := `{
  "listen": "127.0.0.1:18787",
  "state_dir": "/state",
  "client_token_file": "client-token",
  "accounts": [
    {
      "id": "work",
      "capacity": 5
    },
    {
      "id": "alumni",
      "capacity": 1
    }
  ],
  "move_cache_warn_percent": 20
}
`
	if string(data) != want {
		t.Fatalf("config.json\n%s\nwant\n%s", data, want)
	}
	if info, _ := os.Stat(path); info.Mode().Perm() != 0o600 {
		t.Fatalf("mode %v, want 0600", info.Mode().Perm())
	}
}

func TestEditConfigLeavesTheFileUnchangedWhenTheEditIsInvalidOrFails(t *testing.T) {
	text := `{"listen": "127.0.0.1:18787", "state_dir": "/state", "client_token_file": "client-token", "accounts": [{"id": "work", "capacity": 1}]}`
	for name, edit := range map[string]func(*Config) error{
		"no accounts left": func(c *Config) error { c.Accounts = nil; return nil },
		"bad account ID":   func(c *Config) error { c.Accounts[0].ID = "../work"; return nil },
		"edit error":       func(c *Config) error { return errors.New("sign-in failed") },
	} {
		t.Run(name, func(t *testing.T) {
			path := writeConfigFile(t, text)
			if err := EditConfig(path, edit); err == nil {
				t.Fatal("EditConfig succeeded, want an error")
			}
			data, _ := os.ReadFile(path)
			if string(data) != text {
				t.Fatalf("config.json changed to\n%s", data)
			}
			entries, _ := os.ReadDir(filepath.Dir(path))
			if len(entries) != 1 {
				t.Fatalf("directory holds %d entries, want only config.json", len(entries))
			}
		})
	}
}

func TestEditConfigRefusesAFileWithAnUnknownField(t *testing.T) {
	path := writeConfigFile(t, `{"listen": "127.0.0.1:18787", "state_dir": "/state", "accounts": [{"id": "work"}], "colour": "red"}`)
	err := EditConfig(path, func(*Config) error { return nil })
	if err == nil || !strings.Contains(err.Error(), "colour") {
		t.Fatalf("EditConfig: %v, want a refusal naming the unknown field", err)
	}
}
