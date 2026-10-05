package claude

import (
	"net/http"
	"reflect"
	"strings"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func boolPtr(b bool) *bool { return &b }

func TestUnknownOverageStateRefusesDispatch(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusNotFound})
	e.upstream.setUsage("acct-b", usageReply{})
	e.start()

	placed := e.send(msg{Session: sessionID(1)})
	refused := e.send(msg{Session: sessionID(2)})

	if placed.Status != http.StatusServiceUnavailable || refused.Status != http.StatusServiceUnavailable {
		t.Fatalf("got %d and %d, want 503 while no account has a known overage state", placed.Status, refused.Status)
	}
	if !strings.Contains(placed.ErrMsg, "paid overflow") || placed.Header.Get("X-Should-Retry") != "false" {
		t.Fatalf("refusal %q (x-should-retry %q) does not name the included-only state", placed.ErrMsg, placed.Header.Get("X-Should-Retry"))
	}
	if n := len(e.upstream.inference()); n != 0 {
		t.Fatalf("router made %d inference attempts with unknown overage state", n)
	}
	if n := len(e.upstream.usageReads()); n > 4 {
		t.Fatalf("router read the setting %d times, want at most one start read and one recheck per account", n)
	}
}

func TestAnAccountWithUnknownStateIsSkippedForPlacement(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusNotFound})
	e.start()

	r1 := e.send(msg{Session: sessionID(1)})
	r2 := e.send(msg{Session: sessionID(2)})

	if r1.Text != served("acct-b") || r2.Text != served("acct-b") {
		t.Fatalf("served %q and %q, want both on acct-b, the only account with a reading", r1.Text, r2.Text)
	}
}

func TestEnabledOverflowRefusesDispatch(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(true)})
	e.start()

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusServiceUnavailable || len(e.upstream.inference()) != 0 {
		t.Fatalf("got %d with %d inference attempts, want 503 and none", r.Status, len(e.upstream.inference()))
	}
}

func TestStaleReadingIsRecheckedBeforeDispatch(t *testing.T) {
	e := newEnv(t, "acct-a")
	t0 := time.Now()
	e.clock.set(t0)
	e.start()
	e.send(msg{Session: sessionID(1)})

	e.clock.set(t0.Add(31 * time.Minute))
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})
	refused := e.send(msg{Session: sessionID(1)})
	e.clock.set(t0.Add(32 * time.Minute))
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(false)})
	rechecked := e.send(msg{Session: sessionID(1)})

	if refused.Status != http.StatusServiceUnavailable {
		t.Fatalf("stale reading with a failed recheck got %d, want 503", refused.Status)
	}
	if rechecked.Status != 200 || rechecked.Text != served("acct-a") {
		t.Fatalf("after a successful recheck got %d %q, want 200 from acct-a", rechecked.Status, rechecked.Text)
	}
	if n := len(e.upstream.inference()); n != 2 {
		t.Fatalf("inference attempts %d, want 2: none while the reading was stale", n)
	}
	if n := len(e.upstream.usageReads()); n != 3 {
		t.Fatalf("settings reads %d, want 3: start, failed recheck, successful recheck", n)
	}
}

func TestResponseHeadersRefreshTheReading(t *testing.T) {
	e := newEnv(t, "acct-a")
	t0 := time.Now()
	e.clock.set(t0)
	e.start()
	disabled := map[string]string{"anthropic-ratelimit-unified-overage-status": "rejected", "anthropic-ratelimit-unified-overage-disabled-reason": "org_level_disabled"}
	e.upstream.script("acct-a", reply{Header: disabled})
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusServiceUnavailable})

	e.clock.set(t0.Add(25 * time.Minute))
	e.send(msg{Session: sessionID(1)})
	e.clock.set(t0.Add(50 * time.Minute))
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 {
		t.Fatalf("got %d, want 200 on a reading refreshed by the previous response", r.Status)
	}
	if n := len(e.upstream.usageReads()); n != 1 {
		t.Fatalf("settings reads %d, want only the start read", n)
	}
}

func TestPaidUseStopsRoutingToTheAccount(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	paid := map[string]string{
		"anthropic-ratelimit-unified-representative-claim": "overage",
		"anthropic-ratelimit-unified-overage-status":       "allowed",
		"anthropic-ratelimit-unified-overage-in-use":       "true",
	}
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Header: paid})

	shown := e.send(msg{Session: sessionID(1)})
	moved := e.send(msg{Session: sessionID(1)})
	fresh := e.send(msg{Session: sessionID(2)})

	if shown.Status != 200 || shown.Text != served("acct-a") {
		t.Fatalf("response that showed paid use got %d %q, want it delivered from acct-a", shown.Status, shown.Text)
	}
	if moved.Text != served("acct-b") || fresh.Text != served("acct-b") {
		t.Fatalf("after paid use served %q and %q, want acct-b for both", moved.Text, fresh.Text)
	}
	b := e.binding(sessionID(1))
	if b.Account != "acct-b" || b.Reason != router.ReasonOverageObserved {
		t.Fatalf("binding %+v, want acct-b for overage_observed", b)
	}
	want := []string{"acct-a", "acct-a", "acct-b", "acct-b"}
	if got := e.upstream.accountsServed(); !reflect.DeepEqual(got, want) {
		t.Fatalf("upstream attempts %v, want %v", got, want)
	}
	var kinds []string
	for _, ev := range e.events() {
		if ev.Kind == "paid_use_observed" || ev.Kind == "migrated" {
			kinds = append(kinds, ev.Kind)
		}
	}
	if !reflect.DeepEqual(kinds, []string{"paid_use_observed", "migrated"}) {
		t.Fatalf("events %v, want paid_use_observed then migrated", kinds)
	}
}

func TestPaidUseWithoutAnotherAccountRefuses(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Header: map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true"}})

	e.send(msg{Session: sessionID(1)})
	r := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusServiceUnavailable || !strings.Contains(r.ErrMsg, "paid use") {
		t.Fatalf("got %d %q, want 503 naming paid use", r.Status, r.ErrMsg)
	}
	if n := len(e.upstream.inference()); n != 1 {
		t.Fatalf("inference attempts %d, want 1: none after paid use", n)
	}
}

func TestDisabledResponseFromAnAttemptStartedBeforePaidUseDoesNotClearIt(t *testing.T) {
	e := newEnv(t, "acct-a")
	t0 := time.Now()
	e.clock.set(t0)
	e.start()
	e.send(msg{Session: sessionID(1)})
	entered, release := make(chan struct{}), make(chan struct{})
	e.upstream.script("acct-a",
		reply{Header: map[string]string{"anthropic-ratelimit-unified-overage-status": "rejected"}, Before: func() {
			close(entered)
			<-release
			e.clock.set(t0.Add(3 * time.Minute))
		}},
		reply{Header: map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true"}})

	e.clock.set(t0.Add(time.Minute))
	slow := make(chan result, 1)
	go func() { slow <- e.send(msg{Session: sessionID(1)}) }()
	<-entered
	e.clock.set(t0.Add(2 * time.Minute))
	paid := e.send(msg{Session: sessionID(1)})
	close(release)
	disabled := <-slow
	e.clock.set(t0.Add(4 * time.Minute))
	next := e.send(msg{Session: sessionID(1)})

	if paid.Status != 200 || disabled.Status != 200 {
		t.Fatalf("got %d and %d, want both responses delivered", paid.Status, disabled.Status)
	}
	if next.Status != http.StatusServiceUnavailable || !strings.Contains(next.ErrMsg, "paid use") {
		t.Fatalf("next request got %d %q, want 503 naming paid use: the disabled response may describe the account before the paid use", next.Status, next.ErrMsg)
	}
}

func TestEnabledSettingsReadInFlightOutranksAnEarlierDisabledResponse(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.OverageCheckEvery = Duration(50 * time.Millisecond)
	t0 := time.Now()
	e.clock.set(t0)
	e.start()
	e.send(msg{Session: sessionID(1)})
	entered, release := make(chan struct{}), make(chan struct{})
	e.clock.set(t0.Add(time.Minute))
	e.upstream.setUsage("acct-a", usageReply{Enabled: boolPtr(true), Before: func() {
		close(entered)
		<-release
		e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
		e.clock.set(t0.Add(3 * time.Minute))
	}})

	select {
	case <-entered:
	case <-time.After(5 * time.Second):
		t.Fatal("no settings read started")
	}
	e.clock.set(t0.Add(2 * time.Minute))
	e.upstream.script("acct-a", reply{Header: map[string]string{"anthropic-ratelimit-unified-overage-status": "rejected"}})
	during := e.send(msg{Session: sessionID(1)})
	close(release)
	deadline := time.Now().Add(5 * time.Second)
	for !hasEnabledRead(e) {
		if time.Now().After(deadline) {
			t.Fatal("the settings read did not finish")
		}
		time.Sleep(10 * time.Millisecond)
	}
	e.clock.set(t0.Add(4 * time.Minute))
	next := e.send(msg{Session: sessionID(1)})

	if during.Status != 200 {
		t.Fatalf("request during the read got %d, want 200", during.Status)
	}
	if next.Status != http.StatusServiceUnavailable || !strings.Contains(next.ErrMsg, "paid overflow enabled") {
		t.Fatalf("next request got %d %q, want 503 naming enabled paid overflow: the read may have seen the setting after the response did", next.Status, next.ErrMsg)
	}
}

func hasEnabledRead(e *env) bool {
	for _, ev := range e.events() {
		if ev.Kind == "overage_read" && ev.Overage == router.OverageEnabled {
			return true
		}
	}
	return false
}

func TestObservationsReachTheRouterStampedNoLaterThanNowAndNeverZero(t *testing.T) {
	header := func(m map[string]string) http.Header {
		h := http.Header{}
		for k, v := range m {
			h.Set(k, v)
		}
		return h
	}
	t0 := time.Now().Truncate(time.Second)
	cases := map[string]struct {
		observe    func(e *env)
		wantStatus int
		wantMsg    string
	}{
		"zero-stamped paid use": {
			observe: func(e *env) {
				e.svc.observeHeaders("acct-a", header(map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true"}), time.Time{}, time.Time{})
			},
			wantStatus: http.StatusServiceUnavailable, wantMsg: "paid use",
		},
		"future-stamped report after a live rejection": {
			observe: func(e *env) {
				e.svc.observeHeaders("acct-a", header(exhaustedHeaders(t0.Add(2*time.Hour))), t0, t0)
				later := t0.Add(8 * 24 * time.Hour)
				e.svc.observeHeaders("acct-a", header(map[string]string{
					"anthropic-ratelimit-unified-5h-status":      "allowed",
					"anthropic-ratelimit-unified-5h-utilization": "0.1",
				}), later, later)
			},
			wantStatus: http.StatusTooManyRequests, wantMsg: "usable again at",
		},
		"future-stamped disabled response after paid use": {
			observe: func(e *env) {
				e.svc.observeHeaders("acct-a", header(map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true"}), t0, t0)
				e.clock.set(t0.Add(time.Minute))
				later := t0.Add(time.Hour)
				e.svc.observeHeaders("acct-a", header(map[string]string{"anthropic-ratelimit-unified-overage-status": "rejected"}), later, later)
			},
			wantStatus: http.StatusServiceUnavailable, wantMsg: "paid use",
		},
		"rejection with a zero attempt start": {
			observe: func(e *env) {
				e.clock.set(t0.Add(time.Minute))
				e.svc.observeHeaders("acct-a", header(exhaustedHeaders(t0.Add(2*time.Hour))), time.Time{}, t0)
			},
			wantStatus: http.StatusTooManyRequests, wantMsg: "usable again at",
		},
	}
	for name, c := range cases {
		t.Run(name, func(t *testing.T) {
			e := newEnv(t, "acct-a")
			e.clock.set(t0)
			e.start()
			e.send(msg{Session: sessionID(1)})

			c.observe(e)
			e.clock.set(t0.Add(2 * time.Minute))
			r := e.send(msg{Session: sessionID(1), NonStream: true})

			if r.Status != c.wantStatus || !strings.Contains(r.ErrMsg, c.wantMsg) {
				t.Fatalf("got %d %q, want %d naming %q", r.Status, r.ErrMsg, c.wantStatus, c.wantMsg)
			}
		})
	}
}
