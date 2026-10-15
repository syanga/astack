package main

import (
	"bytes"
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestSupervisionGivesUpAfterFiveUncleanStartsInTenMinutes(t *testing.T) {
	state := t.TempDir()
	t0 := time.Date(2026, 10, 15, 14, 0, 0, 0, time.UTC)
	for i := range 5 {
		s, err := loadSupervision(state)
		if err != nil {
			t.Fatal(err)
		}
		if !s.begin(t0.Add(time.Duration(i) * time.Minute)) {
			t.Fatalf("start %d refused, want it allowed", i+1)
		}
		s.fail(t0.Add(time.Duration(i)*time.Minute), errors.New("listen tcp 127.0.0.1:18787: bind: address already in use"))
		if err := s.save(state); err != nil {
			t.Fatal(err)
		}
	}
	s, _ := loadSupervision(state)
	sixth := t0.Add(5 * time.Minute)
	if s.begin(sixth) || !s.GaveUpAt.Equal(sixth) || s.Runs != 5 {
		t.Fatalf("sixth start: allowed or wrong record %+v; want refused, gave up at %v, 5 runs", s, sixth)
	}
	later := t0.Add(10*time.Minute + time.Second)
	if !s.begin(later) || !s.GaveUpAt.IsZero() || len(s.Unclean) != 5 {
		t.Fatalf("start after the first unclean start left the window: %+v; want allowed with 5 unclean starts", s)
	}
}

func TestACleanStopDoesNotCountTowardTheLimit(t *testing.T) {
	var s supervision
	t0 := time.Date(2026, 10, 15, 14, 0, 0, 0, time.UTC)
	for i := range 10 {
		start := t0.Add(time.Duration(i) * time.Second)
		if !s.begin(start) {
			t.Fatalf("start %d refused after clean stops", i+1)
		}
		s.stopped(start)
	}
	if len(s.Unclean) != 0 || s.Runs != 10 {
		t.Fatalf("record %+v, want no unclean starts and 10 runs", s)
	}
}

func TestAConfigThatFailsToLoadCountsTowardTheLimit(t *testing.T) {
	state := t.TempDir()
	config := filepath.Join(state, "config.json")
	if err := os.WriteFile(config, []byte(`{"listen": "127.0.0.1:18787", "capcity": 5}`), 0o600); err != nil {
		t.Fatal(err)
	}
	args := []string{"-supervised", "-config", config}
	for i := range crashLimit {
		if err := serve(args); err == nil || !strings.Contains(err.Error(), `unknown field "capcity"`) {
			t.Fatalf("start %d: %v, want the config error", i+1, err)
		}
	}
	if err := serve(args); err != nil {
		t.Fatalf("start %d: %v, want a clean exit that the service manager does not restart", crashLimit+1, err)
	}
	s, err := loadSupervision(state)
	if err != nil || s.GaveUpAt.IsZero() || s.Runs != crashLimit || !strings.Contains(s.LastError, `unknown field "capcity"`) {
		t.Fatalf("record %+v (%v), want gave up after %d runs with the config error", s, err, crashLimit)
	}
	var out bytes.Buffer
	writeStatus(&out, report{Now: time.Now(), Manager: "launchd", Sup: s, ConfigErr: s.LastError}, false)
	if line := strings.Split(out.String(), "\n")[1]; !strings.HasPrefix(line, "        last error: "+config+": json: unknown field \"capcity\"") {
		t.Fatalf("status line %q, want the recorded config error", line)
	}
}

func TestAnUnreadableSupervisionRecordStillBoundsRestarts(t *testing.T) {
	state := t.TempDir()
	config := filepath.Join(state, "config.json")
	if err := os.WriteFile(config, []byte(`{`), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(state, supervisionFile), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	args := []string{"-supervised", "-config", config}
	starts := 0
	for ; starts < 2*crashLimit && serve(args) != nil; starts++ {
	}
	if starts != crashLimit {
		t.Fatalf("%d failed starts before giving up, want %d", starts, crashLimit)
	}
}

func TestStatusShowsAConfigThatDoesNotLoad(t *testing.T) {
	var out bytes.Buffer
	writeStatus(&out, report{Now: sampleNow, Manager: "launchd",
		ConfigErr: `/s/config.json: json: unknown field "capcity"`}, false)
	want := "router  stopped  launchd                                             14:20 PDT\n" +
		"        config error: /s/config.json: json: unknown field \"capcity\"\n" +
		"next    fix the configuration, then claude-router start\n"
	if out.String() != want {
		t.Fatalf("status:\n%s\nwant:\n%s", out.String(), want)
	}
}
