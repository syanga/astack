package main

import (
	"bytes"
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

func quotaEvent(at time.Time, kind string, account router.AccountID, model string, status, attempts int, durMS int64, qAt time.Time, windows ...claude.UsageWindow) claude.Event {
	e := claude.Event{
		At:       at,
		Kind:     kind,
		Account:  account,
		Model:    model,
		Status:   status,
		Attempts: attempts,
	}
	if durMS > 0 {
		e.DurationMS = &durMS
	}
	if len(windows) > 0 {
		if qAt.IsZero() {
			qAt = at
		}
		e.Quota = &claude.UsageReading{At: qAt, Windows: windows}
	}
	return e
}

func feedEvents(t *testing.T, quotaSince time.Time, events ...claude.Event) digest {
	t.Helper()
	var buf bytes.Buffer
	enc := json.NewEncoder(&buf)
	for _, e := range events {
		if err := enc.Encode(e); err != nil {
			t.Fatal(err)
		}
	}
	d := digest{
		Accounts:      map[router.AccountID]*usageTotals{},
		Conversations: map[string]*conversationDigest{},
		QuotaSince:    quotaSince,
		QuotaSamples:  map[windowID][]quotaSample{},
		RequestSpans:  map[router.AccountID][]requestSpan{},
	}
	if err := d.read(&buf); err != nil {
		t.Fatal(err)
	}
	return d
}

func assertNear(t *testing.T, got, want float64) {
	t.Helper()
	if math.Abs(got-want) > 1e-6 {
		t.Fatalf("got %.4f, want %.4f", got, want)
	}
}

func TestLocalUsageAttributesLocalActivityAndIgnoresIdleIncreases(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0), ResetsAt: reset}),
		quotaEvent(t0.Add(5*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 10_000, t0.Add(5*time.Minute-8*time.Second), claude.UsageWindow{Kind: router.Weekly, Used: util(0.10), ResetsAt: reset}),
		quotaEvent(t0.Add(10*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(10*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.15), ResetsAt: reset}),
		quotaEvent(t0.Add(20*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(20*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.25), ResetsAt: reset}),
		quotaEvent(t0.Add(30*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(30*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.35), ResetsAt: reset}),
		quotaEvent(t0.Add(35*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(35*time.Minute-4*time.Second), claude.UsageWindow{Kind: router.Weekly, Used: util(0.45), ResetsAt: reset}),
	)

	got, ok := d.localUsage("work", router.Weekly, "", 0.45, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.25)
}

func TestLocalUsageIgnoresJitterBetweenHeadersAndUsageReads(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0.20), ResetsAt: reset}),
		quotaEvent(t0.Add(time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(time.Minute-4*time.Second), claude.UsageWindow{Kind: router.Weekly, Used: util(0.244), ResetsAt: reset}),
		quotaEvent(t0.Add(2*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(2*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.240), ResetsAt: reset}),
		quotaEvent(t0.Add(3*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(3*time.Minute-4*time.Second), claude.UsageWindow{Kind: router.Weekly, Used: util(0.244), ResetsAt: reset}),
		quotaEvent(t0.Add(4*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(4*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.240), ResetsAt: reset}),
		quotaEvent(t0.Add(5*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(5*time.Minute-4*time.Second), claude.UsageWindow{Kind: router.Weekly, Used: util(0.250), ResetsAt: reset}),
	)

	got, ok := d.localUsage("work", router.Weekly, "", 0.250, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.050)
}

func TestLocalUsagePreservesSubPercentUsageAcrossRoundedZeroPoll(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0), ResetsAt: reset}),
		quotaEvent(t0.Add(time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.004), ResetsAt: reset}),
		quotaEvent(t0.Add(2*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(2*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0), ResetsAt: reset}),
		quotaEvent(t0.Add(3*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(3*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.004), ResetsAt: reset}),
	)

	got, ok := d.localUsage("work", router.Weekly, "", 0.004, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.004)
}

func TestLocalUsageSortsStreamingHeadersByReadingTimestamp(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0.20), ResetsAt: reset}),
		quotaEvent(t0.Add(2*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(2*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.25), ResetsAt: reset}),
		quotaEvent(t0.Add(3*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 120_000, t0.Add(time.Minute+2*time.Second), claude.UsageWindow{Kind: router.Weekly, Used: util(0.20), ResetsAt: reset}),
		quotaEvent(t0.Add(4*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(4*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.28), ResetsAt: reset}),
		quotaEvent(t0.Add(14*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(14*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.40), ResetsAt: reset}),
	)

	got, ok := d.localUsage("work", router.Weekly, "", 0.40, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.08)
}

func TestLocalUsageResetsAcrossWindowBoundaryAndManualResets(t *testing.T) {
	oldReset := at(0, 12, 0)
	newReset := at(0, 17, 0)

	t.Run("local request immediately after reset", func(t *testing.T) {
		d := feedEvents(t, time.Time{},
			quotaEvent(at(0, 11, 55), "overage_read", "work", "", 0, 0, 0, at(0, 11, 55), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.80), ResetsAt: oldReset}),
			quotaEvent(at(0, 12, 2), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 12, 2), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.06), ResetsAt: newReset}),
		)
		got, ok := d.localUsage("work", router.FiveHour, "", 0.06, newReset, time.Time{})
		if !ok {
			t.Fatal("expected local usage to be tracked")
		}
		assertNear(t, got, 0.06)
	})

	t.Run("local request starting an unused 5-hour window whose reset rounds to the next hour", func(t *testing.T) {
		roundedReset := at(0, 20, 0)
		d := feedEvents(t, time.Time{},
			quotaEvent(at(0, 13, 55), "overage_read", "work", "", 0, 0, 0, at(0, 13, 55), claude.UsageWindow{Kind: router.FiveHour, Used: util(0)}),
			quotaEvent(at(0, 14, 5), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 14, 5), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.04), ResetsAt: roundedReset}),
		)
		got, ok := d.localUsage("work", router.FiveHour, "", 0.04, roundedReset, time.Time{})
		if !ok {
			t.Fatal("expected local usage to be tracked")
		}
		assertNear(t, got, 0.04)
	})

	t.Run("idle across reset does not claim other machine usage", func(t *testing.T) {
		d := feedEvents(t, time.Time{},
			quotaEvent(at(0, 11, 55), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 11, 55), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.80), ResetsAt: oldReset}),
			quotaEvent(at(0, 12, 5), "overage_read", "work", "", 0, 0, 0, at(0, 12, 5), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.20), ResetsAt: newReset}),
		)
		got, ok := d.localUsage("work", router.FiveHour, "", 0.20, newReset, time.Time{})
		if !ok {
			t.Fatal("expected local usage to be tracked")
		}
		assertNear(t, got, 0)
	})

	t.Run("manual reset mid-window resets accumulated local share", func(t *testing.T) {
		reset := at(-3, 9, 0)
		d := feedEvents(t, time.Time{},
			quotaEvent(at(0, 10, 0), "overage_read", "work", "", 0, 0, 0, at(0, 10, 0), claude.UsageWindow{Kind: router.Weekly, Used: util(0), ResetsAt: reset}),
			quotaEvent(at(0, 10, 5), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 10, 5), claude.UsageWindow{Kind: router.Weekly, Used: util(0.90), ResetsAt: reset}),
			quotaEvent(at(0, 10, 15), "overage_read", "work", "", 0, 0, 0, at(0, 10, 15), claude.UsageWindow{Kind: router.Weekly, Used: util(0), ResetsAt: reset}),
			quotaEvent(at(0, 10, 20), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 10, 20), claude.UsageWindow{Kind: router.Weekly, Used: util(0.15), ResetsAt: reset}),
		)
		got, ok := d.localUsage("work", router.Weekly, "", 0.15, reset, time.Time{})
		if !ok {
			t.Fatal("expected local usage to be tracked")
		}
		assertNear(t, got, 0.15)
	})

	t.Run("local request after manual reset without an intervening zero poll credits its jump", func(t *testing.T) {
		reset := at(-3, 9, 0)
		d := feedEvents(t, time.Time{},
			quotaEvent(at(0, 10, 0), "overage_read", "work", "", 0, 0, 0, at(0, 10, 0), claude.UsageWindow{Kind: router.Weekly, Used: util(0.90), ResetsAt: reset}),
			quotaEvent(at(0, 10, 6), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 10, 6), claude.UsageWindow{Kind: router.Weekly, Used: util(0.04), ResetsAt: reset}),
		)
		got, ok := d.localUsage("work", router.Weekly, "", 0.04, reset, time.Time{})
		if !ok {
			t.Fatal("expected local usage to be tracked")
		}
		assertNear(t, got, 0.04)
	})
}

func TestLocalUsageRespectsStatusReadingTimestampAcrossManualReset(t *testing.T) {
	reset := at(-3, 9, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(at(0, 10, 0), "overage_read", "work", "", 0, 0, 0, at(0, 10, 0), claude.UsageWindow{Kind: router.Weekly, Used: util(0.00), ResetsAt: reset}),
		quotaEvent(at(0, 10, 5), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 10, 5), claude.UsageWindow{Kind: router.Weekly, Used: util(0.90), ResetsAt: reset}),
		quotaEvent(at(0, 10, 6), "overage_read", "work", "", 0, 0, 0, at(0, 10, 6), claude.UsageWindow{Kind: router.Weekly, Used: util(0.00), ResetsAt: reset}),
	)

	got, ok := d.localUsage("work", router.Weekly, "", 0.90, reset, at(0, 10, 5))
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.90)
}

func TestLocalUsageMatchesModelScopedWindowsByModelName(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0,
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.10), ResetsAt: reset},
			claude.UsageWindow{Kind: router.Weekly, Model: "Fable", Used: util(0.00), ResetsAt: reset}),
		quotaEvent(t0.Add(5*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(5*time.Minute),
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.15), ResetsAt: reset}),
		quotaEvent(t0.Add(10*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(10*time.Minute),
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.20), ResetsAt: reset},
			claude.UsageWindow{Kind: router.Weekly, Model: "Fable", Used: util(0.30), ResetsAt: reset}),
		quotaEvent(t0.Add(15*time.Minute), "request", "work", "claude-fable-1", 200, 1, 5_000, t0.Add(15*time.Minute)),
		quotaEvent(t0.Add(20*time.Minute), "overage_read", "work", "", 0, 0, 0, t0.Add(20*time.Minute),
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.25), ResetsAt: reset},
			claude.UsageWindow{Kind: router.Weekly, Model: "Fable", Used: util(0.42), ResetsAt: reset}),
	)

	fable, ok := d.localUsage("work", router.Weekly, "Fable", 0.42, reset, time.Time{})
	if !ok {
		t.Fatal("expected Fable local usage to be tracked")
	}
	assertNear(t, fable, 0.12)
}

func TestLocalUsageFirstReadingMidWindowDoesNotClaimPriorUsage(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0.60), ResetsAt: reset}),
		quotaEvent(t0.Add(5*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(5*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.75), ResetsAt: reset}),
	)

	got, ok := d.localUsage("work", router.Weekly, "", 0.75, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.15)
}

func TestLocalUsageCreditsExhaustionFailureAndRespectsQuotaSince(t *testing.T) {
	reset := at(0, 16, 40)
	t0 := at(0, 13, 0)
	d := feedEvents(t, at(0, 12, 0),
		quotaEvent(at(0, 11, 0), "overage_read", "work", "", 0, 0, 0, at(0, 11, 0), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.10), ResetsAt: reset}),
		quotaEvent(at(0, 11, 30), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, at(0, 11, 30), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.40), ResetsAt: reset}),
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.FiveHour, Used: util(0.40), ResetsAt: reset}),
		claude.Event{
			At: t0.Add(50 * time.Minute), Kind: "attempt_failed", Account: "work", Model: "claude-sonnet-4-6",
			Class: router.ClassExhausted, Status: 429,
			Quota: &claude.UsageReading{At: t0.Add(50 * time.Minute), Windows: []claude.UsageWindow{{Kind: router.FiveHour, Used: util(1.0), ResetsAt: reset}}},
		},
	)

	got, ok := d.localUsage("work", router.FiveHour, "", 1.0, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.60)

	gotUnknown, ok := d.localUsage("work", router.FiveHour, "", 1.0, time.Time{}, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked with unknown reset")
	}
	assertNear(t, gotUnknown, 0.60)
}

func TestLocalUsageIgnoresFailedMigrationRefusalOnUntriedAccount(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "alumni", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0.20), ResetsAt: reset}),
		claude.Event{
			At: t0.Add(5 * time.Minute), Kind: "request", Account: "alumni", Model: "claude-sonnet-4-6",
			Decision: router.Refuse, Reason: string(router.ReasonOverageEnabled), Status: 503, Attempts: 1,
		},
		quotaEvent(t0.Add(10*time.Minute), "overage_read", "alumni", "", 0, 0, 0, t0.Add(10*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.45), ResetsAt: reset}),
	)
	got, ok := d.localUsage("alumni", router.Weekly, "", 0.45, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0)
}

func TestLocalUsageMigrationUsesDestinationAttemptStartNotPriorAccountDuration(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	dur := int64((5 * time.Minute).Milliseconds())
	d := feedEvents(t, time.Time{},
		quotaEvent(t0.Add(time.Minute), "overage_read", "alumni", "", 0, 0, 0, t0.Add(time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.20), ResetsAt: reset}),
		quotaEvent(t0.Add(4*time.Minute), "overage_read", "alumni", "", 0, 0, 0, t0.Add(4*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.60), ResetsAt: reset}),
		claude.Event{
			At: t0.Add(5 * time.Minute), Kind: "request", Account: "alumni", Model: "claude-sonnet-4-6",
			Decision: router.Migrate, Reason: string(router.ReasonExhausted), Status: 200,
			Attempt: 2, Attempts: 2, AttemptStart: t0.Add(4*time.Minute + 30*time.Second),
			DurationMS: &dur,
			Quota:      &claude.UsageReading{At: t0.Add(4*time.Minute + 45*time.Second), Windows: []claude.UsageWindow{{Kind: router.Weekly, Used: util(0.61), ResetsAt: reset}}},
		},
	)
	got, ok := d.localUsage("alumni", router.Weekly, "", 0.61, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.01)
}

func TestDigestSkipsOversizedEventLineWithoutFailing(t *testing.T) {
	reset := at(-3, 9, 0)
	t0 := at(0, 10, 0)
	var raw bytes.Buffer
	enc := json.NewEncoder(&raw)
	_ = enc.Encode(quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.Weekly, Used: util(0.10), ResetsAt: reset}))
	raw.WriteString(`{"at":"2026-10-15T10:02:00Z","kind":"request","model":"`)
	raw.Write(bytes.Repeat([]byte("x"), 5<<20))
	raw.WriteString("\"}\n")
	_ = enc.Encode(quotaEvent(t0.Add(5*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(5*time.Minute), claude.UsageWindow{Kind: router.Weekly, Used: util(0.25), ResetsAt: reset}))

	d := digest{
		Accounts:      map[router.AccountID]*usageTotals{},
		Conversations: map[string]*conversationDigest{},
		QuotaSamples:  map[windowID][]quotaSample{},
		RequestSpans:  map[router.AccountID][]requestSpan{},
	}
	if err := d.read(&raw); err != nil {
		t.Fatalf("digest.read failed on oversized line: %v", err)
	}
	got, ok := d.localUsage("work", router.Weekly, "", 0.25, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked after skipping oversized line")
	}
	assertNear(t, got, 0.15)
}

func TestNarrowTerminalStackedBarPreservesOtherCells(t *testing.T) {
	r := healthyReport()
	work := &r.Status.Accounts[0]
	reset := at(0, 16, 40)
	work.Quota = router.Observation{At: at(0, 14, 15), Windows: []router.Window{
		window(router.FiveHour, 0.49, reset),
	}}
	r.Status.Accounts = r.Status.Accounts[:1]
	t0 := at(0, 13, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.FiveHour, Used: util(0.13), ResetsAt: reset}),
		quotaEvent(t0.Add(30*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(30*time.Minute), claude.UsageWindow{Kind: router.FiveHour, Used: util(0.49), ResetsAt: reset}),
	)
	r.Digest.QuotaSamples = d.QuotaSamples
	r.Digest.RequestSpans = d.RequestSpans

	got := render(onScreen(r, 63, false))
	var found bool
	for _, line := range strings.Split(got, "\n") {
		if strings.Contains(line, "51% left   36% here") {
			found = true
			if !strings.Contains(line, "▒") || !strings.Contains(line, "╱") {
				t.Fatalf("10-column bar for left=51%% local=36%% other=13%% should keep both ▒ and ╱: %q", line)
			}
		}
	}
	if !found {
		t.Fatalf("status at 63 cols\n%s\nmissing expected 5-hour row", got)
	}
}

func TestLocalUsageIncludesSubMillisecondRequestWhoseQuotaPrecedesTruncatedStart(t *testing.T) {
	reset := at(0, 16, 40)
	t0 := at(0, 13, 0)
	qAt := t0.Add(time.Minute + 500*time.Microsecond)
	evAt := t0.Add(time.Minute + 900*time.Microsecond)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.FiveHour, Used: util(0.10), ResetsAt: reset}),
		quotaEvent(evAt, "request", "work", "claude-sonnet-4-6", 200, 1, 0, qAt, claude.UsageWindow{Kind: router.FiveHour, Used: util(0.15), ResetsAt: reset}),
	)
	got, ok := d.localUsage("work", router.FiveHour, "", 0.15, reset, time.Time{})
	if !ok {
		t.Fatal("expected local usage to be tracked")
	}
	assertNear(t, got, 0.05)
}

func withLocalUsage(t *testing.T, r report) report {
	t.Helper()
	r = withUsage(r)
	work5h := at(0, 16, 40)
	workWk := at(-3, 9, 0)
	alumni5h := at(0, 17, 55)
	alumniWk := at(-2, 6, 0)
	t0 := at(0, 13, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0,
			claude.UsageWindow{Kind: router.FiveHour, Used: util(0.20), ResetsAt: work5h},
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.00), ResetsAt: workWk},
			claude.UsageWindow{Kind: router.Weekly, Model: "Fable", Used: util(0.12), ResetsAt: workWk}),
		quotaEvent(t0.Add(30*time.Minute), "request", "work", "claude-sonnet-4-6", 200, 1, 5_000, t0.Add(30*time.Minute),
			claude.UsageWindow{Kind: router.FiveHour, Used: util(0.38), ResetsAt: work5h},
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.21), ResetsAt: workWk}),
		quotaEvent(t0, "overage_read", "alumni", "", 0, 0, 0, t0,
			claude.UsageWindow{Kind: router.FiveHour, Used: util(0.00), ResetsAt: alumni5h},
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.40), ResetsAt: alumniWk},
			claude.UsageWindow{Kind: router.Weekly, Model: "Fable", Used: util(0.25), ResetsAt: alumniWk}),
		quotaEvent(t0.Add(30*time.Minute), "request", "alumni", "claude-fable-1", 200, 1, 5_000, t0.Add(30*time.Minute),
			claude.UsageWindow{Kind: router.FiveHour, Used: util(0.09), ResetsAt: alumni5h},
			claude.UsageWindow{Kind: router.Weekly, Used: util(0.64), ResetsAt: alumniWk}),
		quotaEvent(t0.Add(35*time.Minute), "overage_read", "alumni", "", 0, 0, 0, t0.Add(35*time.Minute),
			claude.UsageWindow{Kind: router.Weekly, Model: "Fable", Used: util(0.40), ResetsAt: alumniWk}),
	)
	r.Digest.QuotaSamples = d.QuotaSamples
	r.Digest.RequestSpans = d.RequestSpans
	return r
}

func TestStatusDrawsStackedLocalBarsLegendAndVerboseBreakdown(t *testing.T) {
	r := withLocalUsage(t, healthyReport())

	got := render(onScreen(r, 100, false))
	for _, want := range []string{
		"        █ left   ▒ this machine   ╱ other   │ even pace",
		"  62% left   18% here  16:40 (2h20m)",
		"  79% left   21% here  Sun 09:00 (2d 18h)",
		"  88% left    0% here  Sun 09:00 (2d 18h)",
		"  91% left    9% here  17:55 (3h35m)",
		"  36% left   24% here  Sat 06:00 (1d 15h)",
		"  60% left   15% here  Sat 06:00 (1d 15h)",
	} {
		if !strings.Contains(got, want) {
			t.Fatalf("status\n%s\nmissing expected line fragment %q", got, want)
		}
	}
	lines := strings.Split(got, "\n")
	if !strings.HasPrefix(lines[1], "pool    5h ") || !strings.Contains(lines[1], "▒") {
		t.Fatalf("pool line %q should show stacked local usage cells", lines[1])
	}
	for _, line := range lines {
		if strings.Contains(line, "79% left   21% here") {
			if !strings.Contains(line, "▒") || strings.Contains(line, "╱") {
				t.Fatalf("100%% local bar should have ▒ and no ╱: %q", line)
			}
		}
		if strings.Contains(line, "88% left    0% here") {
			if strings.Contains(line, "▒") || !strings.Contains(line, "╱") {
				t.Fatalf("0%% local bar should have ╱ and no ▒: %q", line)
			}
		}
	}

	colored := render(onScreen(r, 100, true))
	if colored == got {
		t.Fatal("colored output has no ANSI escapes")
	}
	if stripped := sgr.ReplaceAllString(colored, ""); stripped != got {
		t.Fatalf("colored output stripped differs from plain\n--- stripped\n%s--- plain\n%s", stripped, got)
	}

	for width := 60; width <= 140; width++ {
		want := min(width, maxBoxWidth)
		for _, line := range strings.Split(render(onScreen(r, width, false)), "\n") {
			if strings.HasPrefix(line, "╭") || strings.HasPrefix(line, "│") || strings.HasPrefix(line, "╰") {
				if n := utf8.RuneCountInString(line); n != want {
					t.Fatalf("at width %d, a box line of %d columns, want %d: %q", width, n, want, line)
				}
			}
		}
	}

	var verbose bytes.Buffer
	writeStatus(&verbose, r, true)
	for _, want := range []string{
		"5-hour       38% used (18% here), resets 16:40",
		"weekly       21% used (21% here), resets Sun 09:00",
		"weekly Fable 12% used (0% here), resets Sun 09:00",
		"weekly       64% used (24% here), resets Sat 06:00",
		"weekly Fable 40% used (15% here), resets Sat 06:00",
	} {
		if !strings.Contains(verbose.String(), want) {
			t.Fatalf("status -v\n%s\nmissing %q", verbose.String(), want)
		}
	}
}

func TestExhaustedBarShowsStackedLocalShare(t *testing.T) {
	r := withUsage(waitingReport())
	reset := at(0, 16, 40)
	t0 := at(0, 13, 0)
	d := feedEvents(t, time.Time{},
		quotaEvent(t0, "overage_read", "work", "", 0, 0, 0, t0, claude.UsageWindow{Kind: router.FiveHour, Used: util(0.40), ResetsAt: reset}),
		claude.Event{
			At: at(0, 13, 52), Kind: "attempt_failed", Account: "work", Model: "claude-sonnet-4-6",
			Class: router.ClassExhausted, Status: 429,
			Quota: &claude.UsageReading{At: at(0, 13, 52), Windows: []claude.UsageWindow{{Kind: router.FiveHour, Used: util(1.0), ResetsAt: reset}}},
		},
	)
	r.Digest.QuotaSamples = d.QuotaSamples
	r.Digest.RequestSpans = d.RequestSpans

	got := render(onScreen(r, 100, false))
	var found bool
	for _, line := range strings.Split(got, "\n") {
		if strings.Contains(line, "out   60% here") {
			found = true
			if !strings.Contains(line, "▒") || !strings.Contains(line, "╱") {
				t.Fatalf("exhausted bar with 60%% local should stack ▒ and ╱: %q", line)
			}
		}
	}
	if !found {
		t.Fatalf("status\n%s\nmissing exhausted row with 60%% here", got)
	}

	var verbose bytes.Buffer
	writeStatus(&verbose, r, true)
	if !strings.Contains(verbose.String(), "rejected at 13:52 (60% here), resets 16:40") {
		t.Fatalf("status -v\n%s\nmissing rejected (60%% here)", verbose.String())
	}
}
