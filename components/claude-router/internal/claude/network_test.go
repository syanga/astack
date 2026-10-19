package claude

import (
	"bufio"
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

type netAction int

const (
	pass netAction = iota
	closeBeforeBody
	closeAfterBody
	killTogether
	stallBody
	abortThenIgnore
)

type arrival struct {
	Conn int
	Read bool
}

type netUpstream struct {
	t      *testing.T
	fake   *fakeUpstream
	url    string
	ln     net.Listener
	script func(n int) netAction
	group  int

	mu       sync.Mutex
	cond     *sync.Cond
	conns    []net.Conn
	ignored  map[int]bool
	arrivals []arrival
}

type connKey struct{}

func newNetUpstream(t *testing.T, fake *fakeUpstream, script func(n int) netAction) *netUpstream {
	t.Helper()
	ln, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	u := &netUpstream{t: t, fake: fake, url: "http://" + ln.Addr().String(), ln: ln, script: script, ignored: map[int]bool{}}
	u.cond = sync.NewCond(&u.mu)
	var p http.Protocols
	p.SetUnencryptedHTTP2(true)
	srv := &http.Server{
		Handler:   u,
		Protocols: &p,
		HTTP2:     &http.HTTP2Config{MaxReceiveBufferPerConnection: 64 << 10, MaxReceiveBufferPerStream: 64 << 10},
		ConnContext: func(ctx context.Context, c net.Conn) context.Context {
			u.mu.Lock()
			defer u.mu.Unlock()
			u.conns = append(u.conns, c)
			return context.WithValue(ctx, connKey{}, len(u.conns)-1)
		},
	}
	go func() { _ = srv.Serve(ln) }()
	t.Cleanup(func() { _ = srv.Close() })
	return u
}

func (u *netUpstream) open() http.RoundTripper {
	var p http.Protocols
	p.SetUnencryptedHTTP2(true)
	return &http.Transport{Protocols: &p}
}

func (u *netUpstream) ServeHTTP(w http.ResponseWriter, r *http.Request) {
	conn := r.Context().Value(connKey{}).(int)
	if r.URL.Path != "/v1/messages" {
		body, _ := io.ReadAll(r.Body)
		u.forward(w, r, body)
		return
	}
	u.mu.Lock()
	u.arrivals = append(u.arrivals, arrival{Conn: conn})
	n := len(u.arrivals)
	ignored := u.ignored[conn]
	u.mu.Unlock()
	if ignored {
		<-r.Context().Done()
		return
	}
	act := u.script(n)
	switch act {
	case closeBeforeBody:
		u.reset(conn)
		return
	case stallBody:
		<-r.Context().Done()
		return
	}
	body, err := io.ReadAll(r.Body)
	if err != nil {
		return
	}
	u.mu.Lock()
	u.arrivals[n-1].Read = true
	u.cond.Broadcast()
	if act == killTogether {
		for u.read() < u.group {
			u.cond.Wait()
		}
	}
	if act == abortThenIgnore {
		u.ignored[conn] = true
	}
	u.mu.Unlock()
	switch act {
	case pass:
		u.forward(w, r, body)
	case abortThenIgnore:
		panic(http.ErrAbortHandler)
	default:
		u.reset(conn)
	}
}

func (u *netUpstream) read() int {
	n := 0
	for _, a := range u.arrivals {
		if a.Read {
			n++
		}
	}
	return n
}

func (u *netUpstream) reset(conn int) {
	u.mu.Lock()
	c := u.conns[conn]
	u.mu.Unlock()
	if tc, ok := c.(*net.TCPConn); ok {
		_ = tc.SetLinger(0)
	}
	_ = c.Close()
}

func (u *netUpstream) stop() {
	_ = u.ln.Close()
	u.mu.Lock()
	n := len(u.conns)
	u.mu.Unlock()
	for i := 0; i < n; i++ {
		u.reset(i)
	}
}

func (u *netUpstream) forward(w http.ResponseWriter, r *http.Request, body []byte) {
	req := r.Clone(r.Context())
	req.Body = io.NopCloser(bytes.NewReader(body))
	resp, err := u.fake.RoundTrip(req)
	if err != nil {
		u.reset(r.Context().Value(connKey{}).(int))
		return
	}
	defer resp.Body.Close()
	for k, v := range resp.Header {
		w.Header()[k] = v
	}
	w.WriteHeader(resp.StatusCode)
	buf := make([]byte, 4096)
	for {
		n, err := resp.Body.Read(buf)
		if n > 0 {
			_, _ = w.Write(buf[:n])
			w.(http.Flusher).Flush()
		}
		if err != nil {
			return
		}
	}
}

func (u *netUpstream) seen() []arrival {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]arrival(nil), u.arrivals...)
}

func (e *env) startNet(u *netUpstream, opts Options) *Service {
	e.t.Helper()
	e.cfg.TestUpstream = u.url
	opts.Upstream = u.open
	return e.startWith(opts)
}

// An HTTP/2 client sends no more body than the server's flow-control window
// until the server reads, so a reset before the read interrupts the write.
func largeBody(e *env, session string) []byte {
	var m map[string]any
	_ = json.Unmarshal(e.body(msg{Session: session}), &m)
	m["messages"] = []map[string]any{{"role": "user", "content": strings.Repeat("a", 1<<20)}}
	b, _ := json.Marshal(m)
	return b
}

type rawEvent map[string]any

func (e *env) rawEvents(kind string) []rawEvent {
	e.t.Helper()
	data, err := os.ReadFile(filepath.Join(e.dir, "events.jsonl"))
	if err != nil {
		e.t.Fatal(err)
	}
	var out []rawEvent
	sc := bufio.NewScanner(bytes.NewReader(data))
	for sc.Scan() {
		var ev rawEvent
		if json.Unmarshal(sc.Bytes(), &ev) == nil && ev["kind"] == kind {
			out = append(out, ev)
		}
	}
	return out
}

func (e *env) failures() []string {
	var out []string
	for _, ev := range e.rawEvents("attempt_failed") {
		s, _ := ev["class"].(string)
		if p, ok := ev["phase"].(string); ok {
			s += "/" + p
		}
		out = append(out, s)
	}
	return out
}

func (e *env) requestAttempts() []int {
	var out []int
	for _, ev := range e.rawEvents("request") {
		n, _ := ev["upstream_attempts"].(float64)
		out = append(out, int(n))
	}
	return out
}

func TestAConnectionResetBeforeTheBodyIsReadIsRetriedWithoutCharge(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.cfg.MaxUpstreamAttempts = 1
	u := newNetUpstream(t, e.upstream, func(n int) netAction {
		if n == 1 {
			return closeBeforeBody
		}
		return pass
	})
	e.startNet(u, Options{})

	r := e.send(msg{Session: sessionID(1), Body: largeBody(e, sessionID(1))})

	if r.Status != http.StatusOK || r.Text != served("acct-a") {
		t.Fatalf("got %d %q (%s), want 200 from acct-a: an attempt the provider never received does not use the one-attempt cap", r.Status, r.Text, r.ErrMsg)
	}
	seen := u.seen()
	if len(seen) != 2 || seen[0].Read || !seen[1].Read || seen[1].Conn == seen[0].Conn {
		t.Fatalf("upstream saw %+v, want an unread request, then a read one on a new connection", seen)
	}
	if got := e.failures(); !reflect.DeepEqual(got, []string{"network/before_write"}) {
		t.Fatalf("attempt_failed events %v, want [network/before_write]", got)
	}
	if got := e.requestAttempts(); !reflect.DeepEqual(got, []int{1}) {
		t.Fatalf("request upstream_attempts %v, want [1]", got)
	}
}

func TestAConnectionResetAfterTheBodyIsReadIsRetriedAndCharged(t *testing.T) {
	t.Run("retried on a new connection on the same account", func(t *testing.T) {
		e := newEnv(t, "acct-a", "acct-b")
		u := newNetUpstream(t, e.upstream, func(n int) netAction {
			if n == 1 {
				return closeAfterBody
			}
			return pass
		})
		e.startNet(u, Options{})

		r := e.send(msg{Session: sessionID(1)})

		if r.Status != http.StatusOK {
			t.Fatalf("got %d (%s), want 200 after a retry", r.Status, r.ErrMsg)
		}
		if b := e.binding(sessionID(1)); b.Account != "acct-a" || r.Text != served("acct-a") {
			t.Fatalf("served by %q, bound to %s; want both on acct-a: a network failure does not move the conversation", r.Text, b.Account)
		}
		seen := u.seen()
		if len(seen) != 2 || !seen[0].Read || !seen[1].Read || seen[1].Conn == seen[0].Conn {
			t.Fatalf("upstream saw %+v, want two read requests on different connections", seen)
		}
		if got := e.failures(); !reflect.DeepEqual(got, []string{"network/after_write"}) {
			t.Fatalf("attempt_failed events %v, want [network/after_write]", got)
		}
		if got := e.requestAttempts(); !reflect.DeepEqual(got, []int{2}) {
			t.Fatalf("request upstream_attempts %v, want [2]", got)
		}
	})
	t.Run("charged against a one-attempt cap", func(t *testing.T) {
		e := newEnv(t, "acct-a")
		e.cfg.MaxUpstreamAttempts = 1
		u := newNetUpstream(t, e.upstream, func(int) netAction { return closeAfterBody })
		e.startNet(u, Options{})

		r := e.send(msg{Session: sessionID(1)})

		if r.Status != http.StatusServiceUnavailable || r.Header.Get("X-Should-Retry") != "false" {
			t.Fatalf("got %d with x-should-retry %q, want the attempt-cap 503 the client does not retry", r.Status, r.Header.Get("X-Should-Retry"))
		}
		if seen := u.seen(); len(seen) != 1 {
			t.Fatalf("upstream saw %d requests, want 1: the cap counts an attempt the provider may have run", len(seen))
		}
		if got := e.requestAttempts(); !reflect.DeepEqual(got, []int{1}) {
			t.Fatalf("request upstream_attempts %v, want [1]", got)
		}
	})
}

func TestAKilledHTTP2ConnectionRetriesEveryStreamOnANewConnection(t *testing.T) {
	const streams = 3
	e := newEnv(t, "acct-a")
	u := newNetUpstream(t, e.upstream, func(n int) netAction {
		if n <= streams {
			return killTogether
		}
		return pass
	})
	u.group = streams
	e.startNet(u, Options{})

	results := make([]result, streams)
	var wg sync.WaitGroup
	for i := range streams {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = e.send(msg{Session: sessionID(i + 1)})
		}()
	}
	wg.Wait()

	for i, r := range results {
		if r.Status != http.StatusOK || r.Text != served("acct-a") {
			t.Fatalf("request %d got %d %q (%s), want 200", i+1, r.Status, r.Text, r.ErrMsg)
		}
	}
	seen := u.seen()
	if len(seen) != 2*streams {
		t.Fatalf("upstream saw %d requests, want %d: one failed and one retried attempt per stream", len(seen), 2*streams)
	}
	killed := seen[0].Conn
	for i, a := range seen {
		if i < streams && a.Conn != killed {
			t.Fatalf("precondition: the first %d requests did not share one connection: %+v", streams, seen)
		}
		if i >= streams && a.Conn == killed {
			t.Fatalf("retry %d reused the killed connection: %+v", i+1-streams, seen)
		}
	}
	want := []string{"network/after_write", "network/after_write", "network/after_write"}
	if got := e.failures(); !reflect.DeepEqual(got, want) {
		t.Fatalf("attempt_failed events %v, want %v", got, want)
	}
	if got := e.requestAttempts(); !reflect.DeepEqual(got, []int{2, 2, 2}) {
		t.Fatalf("request upstream_attempts %v, want [2 2 2]", got)
	}
}

func (e *env) requestReasons() []string {
	var out []string
	for _, ev := range e.rawEvents("request") {
		s, _ := ev["reason"].(string)
		out = append(out, s)
	}
	return out
}

func TestFailuresBeforeWriteStopAfterTheNetworkRetryTime(t *testing.T) {
	e := newEnv(t, "acct-a")
	u := newNetUpstream(t, e.upstream, func(int) netAction { return pass })
	e.startNet(u, Options{NetRetryFor: time.Second})
	u.stop()

	start := time.Now()
	r := e.send(msg{Session: sessionID(1)})
	took := time.Since(start)

	if r.Status != http.StatusServiceUnavailable || r.Header.Get("X-Should-Retry") != "true" {
		t.Fatalf("got %d with x-should-retry %q, want 503 the client may retry", r.Status, r.Header.Get("X-Should-Retry"))
	}
	if took > 5*time.Second {
		t.Fatalf("answered after %s, want about the 1 s network retry time", took)
	}
	got := e.failures()
	if len(got) < 2 {
		t.Fatalf("attempt_failed events %v, want at least two refused connections", got)
	}
	for _, f := range got {
		if f != "network/before_write" {
			t.Fatalf("attempt_failed events %v, want only network/before_write", got)
		}
	}
	if attempts := e.requestAttempts(); !reflect.DeepEqual(attempts, []int{0}) {
		t.Fatalf("request upstream_attempts %v, want [0]: no request reached the provider", attempts)
	}
	if reasons := e.requestReasons(); !reflect.DeepEqual(reasons, []string{"provider_unreachable"}) {
		t.Fatalf("request reasons %v, want [provider_unreachable]", reasons)
	}
}

func TestAfterAChargedAttemptTheNetworkRetryAnswerStopsClientResends(t *testing.T) {
	e := newEnv(t, "acct-a")
	u := newNetUpstream(t, e.upstream, func(n int) netAction {
		if n == 1 {
			return closeAfterBody
		}
		return closeBeforeBody
	})
	e.startNet(u, Options{NetRetryFor: time.Second})

	r := e.send(msg{Session: sessionID(1), Body: largeBody(e, sessionID(1))})

	if r.Status != http.StatusServiceUnavailable || r.Header.Get("X-Should-Retry") != "false" {
		t.Fatalf("got %d with x-should-retry %q, want 503 the client does not resend: the provider may have run the first attempt", r.Status, r.Header.Get("X-Should-Retry"))
	}
	got := e.failures()
	if len(got) < 2 || got[0] != "network/after_write" || got[1] != "network/before_write" {
		t.Fatalf("attempt_failed events %v, want network/after_write, then network/before_write until the retry time ends", got)
	}
	if attempts := e.requestAttempts(); !reflect.DeepEqual(attempts, []int{1}) {
		t.Fatalf("request upstream_attempts %v, want [1]", attempts)
	}
	if reasons := e.requestReasons(); !reflect.DeepEqual(reasons, []string{"provider_unreachable"}) {
		t.Fatalf("request reasons %v, want [provider_unreachable]", reasons)
	}
}

func TestARetryWhoseBodyWriteStallsEndsAtTheNetworkRetryTime(t *testing.T) {
	e := newEnv(t, "acct-a")
	u := newNetUpstream(t, e.upstream, func(n int) netAction {
		if n == 1 {
			return closeBeforeBody
		}
		return stallBody
	})
	e.startNet(u, Options{NetRetryFor: time.Second})

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	start := time.Now()
	r := e.sendCtx(ctx, msg{Session: sessionID(1), Body: largeBody(e, sessionID(1))})
	took := time.Since(start)

	if r.Status != http.StatusServiceUnavailable || r.Header.Get("X-Should-Retry") != "true" {
		t.Fatalf("got %d with x-should-retry %q (%s), want 503 the client may retry", r.Status, r.Header.Get("X-Should-Retry"), r.ErrMsg)
	}
	if took > 5*time.Second {
		t.Fatalf("answered after %s, want about the 1 s network retry time", took)
	}
	seen := u.seen()
	if len(seen) != 2 || seen[0].Read || seen[1].Read {
		t.Fatalf("upstream saw %+v, want two requests it never read: the reset one and the stalled retry", seen)
	}
	if got := e.failures(); !reflect.DeepEqual(got, []string{"network/before_write", "network/before_write"}) {
		t.Fatalf("attempt_failed events %v, want [network/before_write network/before_write]", got)
	}
	if attempts := e.requestAttempts(); !reflect.DeepEqual(attempts, []int{0}) {
		t.Fatalf("request upstream_attempts %v, want [0]", attempts)
	}
}

func TestARetryAfterAFailedStreamUsesANewConnection(t *testing.T) {
	e := newEnv(t, "acct-a")
	u := newNetUpstream(t, e.upstream, func(n int) netAction {
		if n == 1 {
			return abortThenIgnore
		}
		return pass
	})
	e.startNet(u, Options{})

	ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
	defer cancel()
	r := e.sendCtx(ctx, msg{Session: sessionID(1)})

	if r.Status != http.StatusOK || r.Text != served("acct-a") {
		t.Fatalf("got %d %q (%s), want 200 from acct-a: the retry must not go to the connection that stopped answering", r.Status, r.Text, r.ErrMsg)
	}
	seen := u.seen()
	if len(seen) != 2 || seen[1].Conn == seen[0].Conn {
		t.Fatalf("upstream saw %+v, want the retry on a new connection", seen)
	}
	if got := e.failures(); !reflect.DeepEqual(got, []string{"network/after_write"}) {
		t.Fatalf("attempt_failed events %v, want [network/after_write]", got)
	}
}

type refusingUpstream struct {
	others  http.RoundTripper
	calls   atomic.Int32
	started chan int32
	holdAt  int32
}

func (u *refusingUpstream) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.URL.Path != "/v1/messages" {
		return u.others.RoundTrip(req)
	}
	closeBody(req)
	n := u.calls.Add(1)
	u.started <- n
	if n == u.holdAt {
		<-req.Context().Done()
		return nil, req.Context().Err()
	}
	return nil, errors.New("dial tcp: connect: connection refused")
}

func TestAClientThatLeavesStopsTheNetworkRetries(t *testing.T) {
	e := newEnv(t, "acct-a")
	up := &refusingUpstream{others: e.upstream, started: make(chan int32, 16), holdAt: 3}
	e.startWith(Options{Upstream: fixed(up)})

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	done := make(chan result, 1)
	go func() { done <- e.sendCtx(ctx, msg{Session: sessionID(1)}) }()
	for want := int32(1); want <= up.holdAt; want++ {
		select {
		case n := <-up.started:
			if n != want {
				t.Fatalf("upstream call %d, want %d", n, want)
			}
		case <-time.After(10 * time.Second):
			t.Fatalf("upstream saw %d calls, want %d: the transport retries refused connections", up.calls.Load(), up.holdAt)
		}
	}
	cancel()
	if r := <-done; r.Status != -1 {
		t.Fatalf("got %d, want the client to give up first", r.Status)
	}
	e.waitFor("the request event", 10*time.Second, func() bool { return len(e.rawEvents("request")) > 0 })

	if n := up.calls.Load(); n != up.holdAt {
		t.Fatalf("upstream saw %d calls, want %d: none after the client left", n, up.holdAt)
	}
	if got := e.failures(); !reflect.DeepEqual(got, []string{"network/before_write", "network/before_write"}) {
		t.Fatalf("attempt_failed events %v, want the two refused connections before the client left", got)
	}
	if reqs := e.rawEvents("request"); len(reqs) != 1 || reqs[0]["status"] != float64(499) {
		t.Fatalf("request events %v, want one with status 499", reqs)
	}
}

type offsetClock struct{ offset atomic.Int64 }

func (c *offsetClock) now() time.Time { return time.Now().Add(time.Duration(c.offset.Load())) }

func TestANetworkRetryThatWakesAfterTheRetryTimeIsNotSent(t *testing.T) {
	for _, tc := range []struct {
		name  string
		pause time.Duration
		want  int32
	}{
		{"without a pause, tries at 0, 250 ms, and 750 ms", 0, 3},
		{"after a pause during the first wait, no retry", time.Hour, 1},
	} {
		t.Run(tc.name, func(t *testing.T) {
			clk := &offsetClock{}
			up := &refusingUpstream{started: make(chan int32, 16)}
			tr := newTransport(newUpstream(fixed(up)), nil, 4, time.Second, []router.Account{{ID: "acct-a"}}, clk.now, nil, nil,
				func(callInfo, attempt) { clk.offset.Add(int64(tc.pause)) })
			ctx := withCall(context.Background(), callInfo{id: "r1-0", account: "acct-a", budget: &budget{}})
			req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "https://api.anthropic.com/v1/messages", strings.NewReader("{}"))

			_, err := accountTransport{t: tr, account: "acct-a"}.RoundTrip(req)

			if err == nil {
				t.Fatal("round trip succeeded, want the refused connection's error")
			}
			if n := up.calls.Load(); n != tc.want {
				t.Fatalf("upstream saw %d calls, want %d", n, tc.want)
			}
		})
	}
}
