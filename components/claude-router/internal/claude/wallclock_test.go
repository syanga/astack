package claude

import (
	"net/http"
	"slices"
	"strings"
	"testing"
	"time"
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

func TestTheServiceClockNeverStepsBackAndMarksWhileItHolds(t *testing.T) {
	base := time.Now().Round(0)
	reads := []time.Duration{2 * time.Minute, 0, 30 * time.Second, 10 * time.Second, 70 * time.Second, 3 * time.Minute}
	var marks []time.Time
	c := &wallClock{
		read: func() time.Time {
			d := reads[0]
			reads = reads[1:]
			return base.Add(d)
		},
		markEvery: time.Minute,
		expire:    func(m time.Time) { marks = append(marks, m) },
	}

	var got []time.Time
	for range 6 {
		got = append(got, c.now())
	}

	m1 := base.Add(2*time.Minute + time.Nanosecond)
	m2, m3 := m1.Add(time.Nanosecond), m1.Add(2*time.Nanosecond)
	want := []time.Time{base.Add(2 * time.Minute), m1, m1, m2, m3, base.Add(3 * time.Minute)}
	for i := range got {
		if !got[i].Equal(want[i]) || hasMonotonic(got[i]) {
			t.Fatalf("reading %d is %v, want %v without a monotonic reading", i, got[i], want[i])
		}
	}
	wantMarks := []time.Time{m1, m2, m3, base.Add(3 * time.Minute)}
	if !slices.EqualFunc(marks, wantMarks, time.Time.Equal) {
		t.Fatalf("marks %v, want %v: at each step, a minute of wall time after the last mark, and at the end of the hold", marks, wantMarks)
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
