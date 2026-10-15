package main

import (
	"errors"
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
