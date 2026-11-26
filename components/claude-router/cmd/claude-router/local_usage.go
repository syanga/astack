package main

import (
	"slices"
	"strings"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

var quotaHistoryWindow = router.Weekly.Period() + 24*time.Hour

const (
	// Anthropic's resets_at jitters by sub-second to few-second deltas
	// between response headers and /api/oauth/usage.
	resetTolerance = 5 * time.Minute

	// Anthropic's /api/oauth/usage and response headers can disagree by 1%;
	// a larger drop within the same reset window marks a manual reset.
	manualResetDrop = 0.05
)

type windowID struct {
	Account router.AccountID
	Kind    router.WindowKind
	Model   string
}

type quotaSample struct {
	At       time.Time
	Used     float64
	ResetsAt time.Time
}

type requestSpan struct {
	Start time.Time
	End   time.Time
	Model string
}

func (d digest) localUsage(account router.AccountID, kind router.WindowKind, model string, currentUsed float64, currentResetsAt time.Time) (float64, bool) {
	raw := d.QuotaSamples[windowID{Account: account, Kind: kind, Model: model}]
	if len(raw) == 0 {
		return 0, false
	}
	samples := slices.Clone(raw)
	slices.SortStableFunc(samples, func(a, b quotaSample) int {
		return a.At.Compare(b.At)
	})
	spans := d.RequestSpans[account]
	period := kind.Period()
	var windowStart time.Time
	if !currentResetsAt.IsZero() && period > 0 {
		windowStart = currentResetsAt.Add(-period)
	}

	var (
		sawPrior   bool
		priorAt    time.Time
		priorReset time.Time
		inCurrent  bool
		curReset   time.Time
		peak       float64
		lastAt     time.Time
		localUsed  float64
	)
	for _, s := range samples {
		if !currentResetsAt.IsZero() {
			if !s.ResetsAt.IsZero() && !sameReset(s.ResetsAt, currentResetsAt) {
				if s.ResetsAt.Before(currentResetsAt) {
					sawPrior, priorAt, priorReset = true, s.At, s.ResetsAt
				}
				continue
			}
			if s.ResetsAt.IsZero() && !windowStart.IsZero() && s.At.Before(windowStart.Add(-resetTolerance)) {
				sawPrior, priorAt, priorReset = true, s.At, time.Time{}
				continue
			}
		} else if inCurrent && !curReset.IsZero() && !s.ResetsAt.IsZero() && !sameReset(s.ResetsAt, curReset) {
			sawPrior, priorAt, priorReset = true, lastAt, curReset
			inCurrent, curReset, localUsed = false, time.Time{}, 0
		}

		if !inCurrent {
			inCurrent = true
			if !s.ResetsAt.IsZero() {
				curReset = s.ResetsAt
			}
			if s.Used > 0 && sawPrior {
				from := priorAt
				if !windowStart.IsZero() && windowStart.After(from) && !windowStart.After(s.At) {
					from = windowStart
				}
				if !priorReset.IsZero() && priorReset.After(from) && !priorReset.After(s.At) {
					from = priorReset
				}
				if hasLocalActivity(spans, model, from, s.At) {
					localUsed = s.Used
				}
			}
			peak, lastAt = s.Used, s.At
			continue
		}

		if curReset.IsZero() && !s.ResetsAt.IsZero() {
			curReset = s.ResetsAt
		}
		if (s.Used == 0 && peak > 0) || s.Used+manualResetDrop <= peak {
			localUsed = 0
			peak, lastAt = s.Used, s.At
			continue
		}
		if s.Used > peak {
			if hasLocalActivity(spans, model, lastAt, s.At) {
				localUsed += s.Used - peak
			}
			peak = s.Used
		}
		if s.At.After(lastAt) {
			lastAt = s.At
		}
	}
	if !inCurrent {
		return 0, false
	}
	currentUsed = min(max(currentUsed, 0), 1)
	return min(max(localUsed, 0), currentUsed), true
}

func sameReset(a, b time.Time) bool {
	return a.Sub(b).Abs() <= resetTolerance
}

func hasLocalActivity(spans []requestSpan, winModel string, from, to time.Time) bool {
	for _, sp := range spans {
		if !matchesWindowModel(sp.Model, winModel) {
			continue
		}
		if !sp.Start.After(to) && sp.End.After(from) {
			return true
		}
	}
	return false
}

func matchesWindowModel(reqModel, winModel string) bool {
	if winModel == "" {
		return true
	}
	if reqModel == "" {
		return false
	}
	return strings.Contains(normalizeModelName(reqModel), normalizeModelName(winModel))
}

func normalizeModelName(s string) string {
	s = strings.ToLower(strings.TrimSpace(s))
	return strings.NewReplacer(" ", "-", ".", "-", "_", "-").Replace(s)
}
