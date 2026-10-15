package main

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const fakePython = `#!/bin/sh
echo "$*" >> "$FAKE_HELPER_LOG"
case "$*" in *--check*) exit "$FAKE_CHECK_EXIT" ;; esac
`

func directSetup(t *testing.T, python string) (installation, string, string) {
	t.Helper()
	jobs := fakeManager(t)
	in := installationIn(t, "launchd", "dev.astack.claude-router.test")
	if err := os.MkdirAll(in.State, 0o700); err != nil {
		t.Fatal(err)
	}
	if python == "" {
		python = filepath.Join(t.TempDir(), "python3")
		if err := os.WriteFile(python, []byte(fakePython), 0o755); err != nil {
			t.Fatal(err)
		}
	}
	record, _ := json.Marshal(map[string]string{"manager": in.Manager, "name": in.Name, "descriptor": in.Descriptor,
		"config": in.Config, "helper": "/lib/claude_router.py", "python": python, "manifest": "/m.json"})
	if err := os.WriteFile(filepath.Join(in.State, serviceRecord), record, 0o600); err != nil {
		t.Fatal(err)
	}
	log := filepath.Join(t.TempDir(), "helper.log")
	t.Setenv("FAKE_HELPER_LOG", log)
	if err := startService(in); err != nil {
		t.Fatal(err)
	}
	return in, jobs, log
}

func TestDirectStopsTheServiceOnlyWhenClientSettingsNameIt(t *testing.T) {
	for _, c := range []struct {
		name      string
		checkExit string
		loaded    bool
		apply     string
	}{
		{"nothing activated", "3", true, "/lib/claude_router.py direct --state %s --manifest /m.json"},
		{"activated", "0", false, "/lib/claude_router.py direct --state %s --manifest /m.json --stopped"},
	} {
		t.Run(c.name, func(t *testing.T) {
			in, jobs, log := directSetup(t, "")
			t.Setenv("FAKE_CHECK_EXIT", c.checkExit)

			err := direct([]string{"-state", in.State})

			calls, _ := os.ReadFile(log)
			check := strings.ReplaceAll("/lib/claude_router.py direct --state %s --manifest /m.json --check", "%s", in.State)
			want := check + "\n" + strings.ReplaceAll(c.apply, "%s", in.State) + "\n"
			if err != nil || string(calls) != want {
				t.Fatalf("direct: %v, helper ran:\n%s\nwant:\n%s", err, calls, want)
			}
			if loaded := loadedFrom(t, jobs, in.Name) != ""; loaded != c.loaded {
				t.Fatalf("job loaded %v after direct, want %v", loaded, c.loaded)
			}
		})
	}
}

func TestDirectNamesAMissingInstallerPython(t *testing.T) {
	in, jobs, _ := directSetup(t, "/nonexistent/python3")

	err := direct([]string{"-state", in.State})

	if err == nil || err.Error() != "the Python that installed claude-router is gone: /nonexistent/python3" {
		t.Fatalf("direct: %v, want the missing interpreter named", err)
	}
	if loadedFrom(t, jobs, in.Name) == "" {
		t.Fatal("direct stopped the service although it could not run the helper")
	}
}
