package claude

import (
	"net/http"
	"slices"
	"strconv"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// hasMonotonic reports whether t carries Go's monotonic clock reading,
// which time.Time.String shows as a final "m=" field.
func hasMonotonic(t time.Time) bool { return strings.Contains(t.String(), " m=") }

func TestThePolicyComparesStampsWithoutAMonotonicReading(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	startMu.Lock()
	svc, err := Start(e.cfg, Options{Upstream: e.upstream})
	startMu.Unlock()
	if err != nil {
		t.Fatal(err)
	}
	e.svc = svc
	t.Cleanup(svc.Close)
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(time.Hour)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})
	e.upstream.script("acct-b", reply{Status: 429, Header: exhaustedHeaders(reset)})
	e.send(msg{Session: sessionID(1)})

	stamps := map[string]time.Time{"service clock": svc.now(), "binding": e.binding(sessionID(1)).Since}
	for _, st := range svc.router.AccountStates() {
		stamps[string(st.ID)+" check"] = st.CheckAt
		if len(st.Rejections) != 1 {
			t.Fatalf("%s has %d rejections, want 1", st.ID, len(st.Rejections))
		}
		stamps[string(st.ID)+" rejection"] = st.Rejections[0].At
	}
	for name, ts := range stamps {
		if ts.IsZero() || hasMonotonic(ts) {
			t.Fatalf("%s stamp %v is zero or carries a monotonic reading, which stops while the machine sleeps", name, ts)
		}
	}
}

func TestTheServiceClockNeverStepsBackAndExpiresOldChecksWhileItHolds(t *testing.T) {
	base := time.Now().Round(0)
	reads := []time.Duration{2 * time.Minute, 0, 30 * time.Second, 10 * time.Second, 70 * time.Second, 3 * time.Minute}
	call := 0
	expiredAt := map[int]time.Time{}
	c := &wallClock{
		read:      func() time.Time { return base.Add(reads[call]) },
		markEvery: time.Minute,
		expire:    func(m router.Instant) { expiredAt[call] = m.Stamp },
	}

	var got []time.Time
	for call = range reads {
		got = append(got, c.now())
	}

	for i, ts := range got {
		if hasMonotonic(ts) || i > 0 && ts.Before(got[i-1]) {
			t.Fatalf("reading %d is %v after %v, want no step back and no monotonic reading", i, ts, got[max(i-1, 0)])
		}
	}
	if !got[len(got)-1].Equal(base.Add(3 * time.Minute)) {
		t.Fatalf("last reading %v, want the wall clock %v once it passes the held time", got[len(got)-1], base.Add(3*time.Minute))
	}
	var calls []int
	for i := range reads {
		if _, ok := expiredAt[i]; ok {
			calls = append(calls, i)
		}
	}
	if want := []int{1, 3, 4, 5}; !slices.Equal(calls, want) {
		t.Fatalf("checks expired at calls %v, want %v: at each backward step, after a minute of wall time in the hold, and when the hold ends", calls, want)
	}
	for _, i := range calls {
		if m := expiredAt[i]; !m.Equal(got[i]) || !m.After(got[i-1]) {
			t.Fatalf("call %d expired checks before %v, returned %v, and followed %v; want the returned time, later than the one before", i, m, got[i], got[i-1])
		}
	}
}

// The scheduler's timers stop while the machine sleeps, as Go's monotonic
// clock does, so these tests move the wall clock while no timer fires.
func TestAfterTheWallClockJumpsTheAccountIsReadBeforeDispatch(t *testing.T) {
	for _, tc := range []struct {
		name  string
		after time.Duration
		usage usageReply
	}{
		{"scheduled read overdue, reading still fresh", 15 * time.Minute, usageReply{Enabled: boolPtr(true)}},
		{"reading stale by wall time", 2 * time.Hour, usageReply{Status: http.StatusInternalServerError}},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t, "acct-a")
			t0 := time.Now()
			e.clock.set(t0)
			e.start()
			e.send(msg{Session: sessionID(1)})
			e.upstream.setUsage("acct-a", tc.usage)
			reads, inference := len(e.upstream.usageReads()), len(e.upstream.inference())

			e.clock.set(t0.Add(tc.after))
			r := e.send(msg{Session: sessionID(1)})

			if r.Status != http.StatusServiceUnavailable {
				t.Fatalf("got %d %q, want 503", r.Status, r.ErrMsg)
			}
			if n := len(e.upstream.usageReads()) - reads; n < 1 {
				t.Fatalf("%d settings reads, want the account read before the dispatch decision", n)
			}
			if n := len(e.upstream.inference()) - inference; n != 0 {
				t.Fatalf("%d inference attempts, want none", n)
			}
		})
	}
}

// The default overage_fresh_for is 30 minutes. In every case the last
// successful settings read is 31 minutes old by true time when the final
// request arrives, and every read after it fails.
func TestAfterTheWallClockStepsBackNoRequestDispatchesOnAnOldReading(t *testing.T) {
	for _, tc := range []struct {
		name       string
		step       time.Duration
		readInHold bool
	}{
		{name: "no read succeeds after the step", step: time.Hour},
		{name: "a read in the hold, then the hold outlasts the reading", step: time.Hour, readInHold: true},
		{name: "a read in the hold, then the hold ends", step: 20 * time.Minute, readInHold: true},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t, "acct-a")
			t0 := time.Now().Truncate(time.Second)
			e.clock.set(t0)
			e.start()
			e.send(msg{Session: sessionID(1)})
			e.clock.set(t0.Add(20 * time.Minute))
			if r := e.send(msg{Session: sessionID(1)}); r.Status != http.StatusOK {
				t.Fatalf("before the step: got %d %q, want 200", r.Status, r.ErrMsg)
			}

			stepped := t0.Add(20*time.Minute - tc.step)
			e.clock.set(stepped)
			if tc.readInHold {
				if r := e.send(msg{Session: sessionID(1)}); r.Status != http.StatusOK {
					t.Fatalf("after the step: got %d %q, want 200 on a reading taken after the step", r.Status, r.ErrMsg)
				}
			}
			e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
			e.clock.set(stepped.Add(31 * time.Minute))
			inference := len(e.upstream.inference())
			r := e.send(msg{Session: sessionID(1)})

			if r.Status != http.StatusServiceUnavailable || !strings.Contains(r.ErrMsg, "fresh reading") {
				t.Fatalf("got %d %q, want 503 naming the missing fresh reading", r.Status, r.ErrMsg)
			}
			if n := len(e.upstream.inference()) - inference; n != 0 {
				t.Fatalf("%d inference attempts, want none", n)
			}
		})
	}
}

func TestAfterTheWallClockStepsBackAFutureResetStillMovesTheConversation(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	t0 := time.Now().Truncate(time.Second)
	e.clock.set(t0)
	e.start()
	e.send(msg{Session: sessionID(1)})
	if got := e.binding(sessionID(1)).Account; got != "acct-a" {
		t.Fatalf("bound to %s, want acct-a", got)
	}
	stepped := t0.Add(-time.Hour)
	e.clock.set(stepped)
	var exhausted []reply
	for range 10 {
		exhausted = append(exhausted, reply{Status: http.StatusTooManyRequests, Header: exhaustedHeaders(stepped.Add(30 * time.Minute))})
	}
	e.upstream.script("acct-a", exhausted...)
	before := len(e.upstream.accountsServed())

	r := e.send(msg{Session: sessionID(1)})

	served := e.upstream.accountsServed()[before:]
	if r.Status != http.StatusOK || !slices.Equal(served, []string{"acct-a", "acct-b"}) {
		t.Fatalf("got %d %q with attempts %v, want 200 after one attempt on acct-a and one on acct-b", r.Status, r.ErrMsg, served)
	}
	if got := e.binding(sessionID(1)).Account; got != "acct-b" {
		t.Fatalf("bound to %s, want acct-b", got)
	}
}

func TestAfterARestartNoRequestDispatchesOnAReadingOlderThanItsFreshness(t *testing.T) {
	for _, tc := range []struct {
		name     string
		run      func(e *env, t0 time.Time)
		final    time.Duration
		wantSent bool
	}{
		{
			name: "no step, reading 10 min old",
			run: func(e *env, t0 time.Time) {
				e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
				e.clock.set(t0.Add(time.Minute))
				e.restart()
			},
			final:    10 * time.Minute,
			wantSent: true,
		},
		{
			name: "read in a hold, hold ends while running, then restart",
			run: func(e *env, t0 time.Time) {
				e.clock.set(t0.Add(-time.Hour))
				e.send(msg{Session: sessionID(1)})
				e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
				e.clock.set(t0.Add(time.Minute))
				e.send(msg{Session: sessionID(1)})
				e.restart()
			},
			final: 5 * time.Minute,
		},
		{
			name: "read in a hold, stopped in the hold, restart after it",
			run: func(e *env, t0 time.Time) {
				e.clock.set(t0.Add(-time.Hour))
				e.send(msg{Session: sessionID(1)})
				e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
				e.clock.set(t0.Add(time.Minute))
				e.restart()
			},
			final: 5 * time.Minute,
		},
		{
			name: "read before a step seen while running, then restart",
			run: func(e *env, t0 time.Time) {
				e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
				e.clock.set(t0.Add(20 * time.Minute))
				e.send(msg{Session: sessionID(1)})
				e.clock.set(t0.Add(10 * time.Minute))
				e.send(msg{Session: sessionID(1)})
				e.restart()
			},
			final: 25 * time.Minute,
		},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t, "acct-a")
			t0 := time.Now().Truncate(time.Second)
			e.clock.set(t0)
			e.start()
			if r := e.send(msg{Session: sessionID(1)}); r.Status != http.StatusOK {
				t.Fatalf("first request: got %d %q, want 200", r.Status, r.ErrMsg)
			}
			tc.run(e, t0)
			e.clock.set(t0.Add(tc.final))
			inference := len(e.upstream.inference())

			r := e.send(msg{Session: sessionID(1)})

			sent := len(e.upstream.inference()) - inference
			if tc.wantSent && (r.Status != http.StatusOK || sent != 1) {
				t.Fatalf("got %d %q with %d inference attempts, want 200 after one on the saved reading", r.Status, r.ErrMsg, sent)
			}
			if !tc.wantSent && (r.Status != http.StatusServiceUnavailable || !strings.Contains(r.ErrMsg, "fresh reading") || sent != 0) {
				t.Fatalf("got %d %q with %d inference attempts, want 503 naming the missing fresh reading and none sent", r.Status, r.ErrMsg, sent)
			}
		})
	}
}

func TestAfterTheWallClockStepsBackTheLocalWaitCountsToTheResetOnTheWallClock(t *testing.T) {
	e := newEnv(t, "acct-a")
	t0 := time.Now().Truncate(time.Second)
	e.clock.set(t0)
	e.start()
	e.send(msg{Session: sessionID(1)})
	stepped := t0.Add(-time.Hour)
	e.clock.set(stepped)
	reset := stepped.Add(30 * time.Minute)
	var exhausted []reply
	for range 10 {
		exhausted = append(exhausted, reply{Status: http.StatusTooManyRequests, Header: exhaustedHeaders(reset)})
	}
	e.upstream.script("acct-a", exhausted...)

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusTooManyRequests || resetHeader(r) != strconv.FormatInt(reset.Unix(), 10) || r.Header.Get("Retry-After") != "1800" {
		t.Fatalf("got %d reset %q retry-after %q %q, want the local 429 with reset %d and retry-after 1800", r.Status, resetHeader(r), r.Header.Get("Retry-After"), r.ErrMsg, reset.Unix())
	}
}

func TestConcurrentCallersOfAForwardWallClockNeverExpireChecks(t *testing.T) {
	base := time.Now().Round(0)
	firstEntered, secondDone := make(chan struct{}), make(chan struct{})
	var calls atomic.Int32
	var mu sync.Mutex
	var expired []time.Time
	c := &wallClock{
		read: func() time.Time {
			if calls.Add(1) == 1 {
				close(firstEntered)
				select {
				case <-secondDone:
				case <-time.After(100 * time.Millisecond):
				}
				return base
			}
			return base.Add(time.Millisecond)
		},
		markEvery: time.Minute,
		expire: func(m router.Instant) {
			mu.Lock()
			expired = append(expired, m.Stamp)
			mu.Unlock()
		},
	}

	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		c.now()
	}()
	<-firstEntered
	c.now()
	close(secondDone)
	wg.Wait()

	mu.Lock()
	defer mu.Unlock()
	if len(expired) != 0 {
		t.Fatalf("expired checks at %v, want none: the wall clock only moved forward", expired)
	}
}
