package claude

import (
	"net/http"
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

func TestTheServiceClockNeverStepsBack(t *testing.T) {
	base := time.Now()
	reads := []time.Time{base.Add(2 * time.Second), base, base.Add(3 * time.Second)}
	c := &wallClock{read: func() time.Time {
		t := reads[0]
		reads = reads[1:]
		return t
	}}

	got := []time.Time{c.now(), c.now(), c.now()}

	want := []time.Time{base.Add(2 * time.Second), base.Add(2 * time.Second), base.Add(3 * time.Second)}
	for i := range got {
		if !got[i].Equal(want[i]) || hasMonotonic(got[i]) {
			t.Fatalf("reading %d is %v, want %v without a monotonic reading", i, got[i], want[i])
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
