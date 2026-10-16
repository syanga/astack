package main

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"slices"
	"time"
)

// A supervised service gives up after crashLimit starts that did not end in
// a clean stop within crashWindow. It then exits 0, which the launchd and
// systemd descriptors treat as final, so restarts stay bounded and the
// failure stays visible in status until the next explicit start.
const (
	crashLimit      = 5
	crashWindow     = 10 * time.Minute
	supervisionFile = "supervision.json"
)

type supervision struct {
	Runs        int         `json:"runs"`
	Unclean     []time.Time `json:"unclean,omitempty"`
	GaveUpAt    time.Time   `json:"gave_up_at,omitzero"`
	LastError   string      `json:"last_error,omitempty"`
	LastErrorAt time.Time   `json:"last_error_at,omitzero"`
}

func loadSupervision(state string) (supervision, error) {
	var s supervision
	data, err := os.ReadFile(filepath.Join(state, supervisionFile))
	if errors.Is(err, os.ErrNotExist) {
		return s, nil
	}
	if err != nil {
		return s, err
	}
	if err := json.Unmarshal(data, &s); err != nil {
		return s, fmt.Errorf("%s: %w", filepath.Join(state, supervisionFile), err)
	}
	return s, nil
}

func (s supervision) save(state string) error {
	data, err := json.MarshalIndent(s, "", "  ")
	if err != nil {
		return err
	}
	tmp := filepath.Join(state, supervisionFile+".tmp")
	if err := os.WriteFile(tmp, append(data, '\n'), 0o600); err != nil {
		return err
	}
	return os.Rename(tmp, filepath.Join(state, supervisionFile))
}

func (s *supervision) begin(now time.Time) bool {
	s.Unclean = slices.DeleteFunc(s.Unclean, func(t time.Time) bool { return now.Sub(t) >= crashWindow })
	if len(s.Unclean) >= crashLimit {
		s.GaveUpAt = now
		return false
	}
	s.Unclean = append(s.Unclean, now)
	s.Runs++
	s.GaveUpAt = time.Time{}
	return true
}

func (s *supervision) fail(now time.Time, err error) {
	s.LastError, s.LastErrorAt = err.Error(), now
}

func (s *supervision) stopped(start time.Time) {
	s.Unclean = slices.DeleteFunc(s.Unclean, func(t time.Time) bool { return t.Equal(start) })
}
