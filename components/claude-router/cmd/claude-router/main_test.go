package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoginRefusesAnAccountIDOutsideTheAuthDirectory(t *testing.T) {
	state := t.TempDir()

	err := login([]string{"-state", state, "-account", "../x"})

	if err == nil || !strings.Contains(err.Error(), "not a valid account ID") {
		t.Fatalf("login with -account ../x returned %v, want a refusal", err)
	}
	if _, statErr := os.Stat(filepath.Join(state, "assignments")); !os.IsNotExist(statErr) {
		t.Fatal("login touched the state directory before refusing the account ID")
	}
}
