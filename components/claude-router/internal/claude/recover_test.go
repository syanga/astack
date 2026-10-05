package claude

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"reflect"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func nestedHeaders(fiveHour, weekly time.Time) map[string]string {
	h := exhaustedHeaders(fiveHour)
	h["anthropic-ratelimit-unified-7d-status"] = "rejected"
	h["anthropic-ratelimit-unified-7d-reset"] = strconv.FormatInt(weekly.Unix(), 10)
	return h
}

func (e *env) migrations() []Event {
	var out []Event
	for _, ev := range e.events() {
		if ev.Kind == "migrated" {
			out = append(out, ev)
		}
	}
	return out
}

func (e *env) journalOps(op string) int {
	e.t.Helper()
	data, err := os.ReadFile(filepath.Join(e.dir, "assignments", "assignments.jsonl"))
	if err != nil {
		e.t.Fatal(err)
	}
	return bytes.Count(data, []byte(`"op":"`+op+`"`))
}

func resetHeader(r result) string { return r.Header.Get("Anthropic-Ratelimit-Unified-Reset") }

func TestExhaustionBeforeOutputMovesTheConversationWithinTheRequest(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(2 * time.Hour).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})
	opus := "claude-opus-4-1-20250805"

	moved := e.send(msg{Session: sessionID(1), Model: opus})
	subagent := e.send(msg{Session: sessionID(1), Agent: "agent-3", Model: opus})

	if moved.Status != 200 || moved.Text != served("acct-b") {
		t.Fatalf("exhausting request got %d %q, want 200 from acct-b", moved.Status, moved.Text)
	}
	if subagent.Text != served("acct-b") {
		t.Fatalf("subagent request after the migration served %q, want acct-b", subagent.Text)
	}
	if want := []string{"acct-a", "acct-a", "acct-b", "acct-b"}; !reflect.DeepEqual(e.upstream.accountsServed(), want) {
		t.Fatalf("upstream attempts %v, want %v", e.upstream.accountsServed(), want)
	}
	if b := e.binding(sessionID(1)); b.Account != "acct-b" || b.Reason != router.ReasonExhausted {
		t.Fatalf("binding %+v, want acct-b for exhausted", b)
	}
	seen := e.upstream.inference()
	if got := rawString(rawFields(t, seen[2].Body)["model"]); got != opus {
		t.Fatalf("the destination received model %q, want the requested %s", got, opus)
	}
	if src, dst := seen[1].Header.Get(headerSessionID), seen[2].Header.Get(headerSessionID); dst == "" || dst != src {
		t.Fatalf("the destination received session header %q, want the %q the source received", dst, src)
	}
	if src, dst := upstreamSession(t, seen[1].Body), upstreamSession(t, seen[2].Body); dst == "" || dst != src {
		t.Fatalf("the destination received metadata session %q, want the %q the source received", dst, src)
	}
	if m := e.migrations(); len(m) != 1 || m[0].From != "acct-a" || m[0].Account != "acct-b" || m[0].Reason != string(router.ReasonExhausted) {
		t.Fatalf("migration events %+v, want one exhausted move from acct-a to acct-b", m)
	}
}

func TestMigratedConversationStaysAfterTheSourceRecovers(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(10 * time.Minute).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})
	e.send(msg{Session: sessionID(1)})

	e.clock.set(reset.Add(time.Minute))
	after := e.send(msg{Session: sessionID(1)})
	fresh := e.send(msg{Session: sessionID(2)})

	if after.Text != served("acct-b") {
		t.Fatalf("after acct-a's reset the migrated conversation served %q, want acct-b", after.Text)
	}
	if fresh.Text != served("acct-a") {
		t.Fatalf("a new conversation after the reset served %q, want the recovered acct-a", fresh.Text)
	}
	if n := len(e.migrations()); n != 1 {
		t.Fatalf("%d migrations, want 1: the conversation does not return on its own", n)
	}
}

func TestEveryAccountExhaustedWaitsForTheEarliestUsableReset(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	now := time.Now().Truncate(time.Second)
	aFiveHour, aWeekly, bFiveHour := now.Add(time.Hour), now.Add(72*time.Hour), now.Add(2*time.Hour)
	e.upstream.script("acct-a", reply{Status: 429, Header: nestedHeaders(aFiveHour, aWeekly)})
	e.upstream.script("acct-b", reply{Status: 429, Header: exhaustedHeaders(bFiveHour)})

	first := e.send(msg{Session: sessionID(1)})
	again := e.send(msg{Session: sessionID(1)})
	other := e.send(msg{Session: sessionID(2)})
	attemptsWhileWaiting := len(e.upstream.inference())

	for name, r := range map[string]result{"exhausting request": first, "request during the wait": again, "new conversation": other} {
		if r.Status != http.StatusTooManyRequests || r.ErrType != "rate_limit_error" || r.Header.Get("Anthropic-Ratelimit-Unified-Status") != "rejected" {
			t.Fatalf("%s: %d %q, want the local 429 wait", name, r.Status, r.ErrType)
		}
		if resetHeader(r) != strconv.FormatInt(bFiveHour.Unix(), 10) {
			t.Fatalf("%s: reset %s, want acct-b's %d: acct-a's five-hour reset at %d is blocked by its weekly window", name, resetHeader(r), bFiveHour.Unix(), aFiveHour.Unix())
		}
		if ra, _ := strconv.Atoi(r.Header.Get("Retry-After")); ra < 7000 || ra > 7200 {
			t.Fatalf("%s: retry-after %q, want about 7200", name, r.Header.Get("Retry-After"))
		}
		if r.Header.Get("X-Should-Retry") == "false" {
			t.Fatalf("%s: the wait says x-should-retry false; clients must send again after the reset", name)
		}
	}
	if attemptsWhileWaiting != 3 {
		t.Fatalf("%d upstream attempts, want 3: one placement and one on each account before they were known exhausted", attemptsWhileWaiting)
	}

	e.clock.set(aFiveHour.Add(time.Minute))
	early := e.send(msg{Session: sessionID(1)})
	e.clock.set(bFiveHour)
	resumed := e.send(msg{Session: sessionID(1)})

	if early.Status != http.StatusTooManyRequests || len(e.upstream.inference()) != 4 {
		t.Fatalf("after acct-a's five-hour reset: %d with %d upstream attempts, want a 429 and no attempt on acct-a", early.Status, len(e.upstream.inference())-3)
	}
	if resumed.Status != 200 || resumed.Text != served("acct-b") {
		t.Fatalf("at acct-b's reset: %d %q, want 200 from acct-b", resumed.Status, resumed.Text)
	}
}

func TestUnknownResetWaitsWithoutPromisingATime(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	h := exhaustedHeaders(time.Now())
	delete(h, "anthropic-ratelimit-unified-5h-reset")
	delete(h, "anthropic-ratelimit-unified-reset")
	e.upstream.script("acct-a", reply{Status: 429, Header: h})

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != http.StatusTooManyRequests || resetHeader(r) != "" || r.Header.Get("Retry-After") != "300" {
		t.Fatalf("got %d reset %q retry-after %q, want 429 with no reset and retry-after 300", r.Status, resetHeader(r), r.Header.Get("Retry-After"))
	}
	if !strings.Contains(r.ErrMsg, "unknown") {
		t.Fatalf("message %q does not say the reset time is unknown", r.ErrMsg)
	}
	e.clock.set(time.Now().Add(6 * time.Minute))
	if again := e.send(msg{Session: sessionID(1)}); again.Status != 200 {
		t.Fatalf("after the recheck time: %d, want the account tried again and served", again.Status)
	}
}

func TestMigrationWithTheAttemptCapSpentAsksTheClientToSendAgain(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.cfg.MaxUpstreamAttempts = 1
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(time.Now().Add(time.Hour))})

	moved := e.send(msg{Session: sessionID(1)})
	resent := e.send(msg{Session: sessionID(1)})

	if moved.Status != http.StatusTooManyRequests || moved.Header.Get("Retry-After") != "1" || !strings.Contains(moved.ErrMsg, "acct-b") {
		t.Fatalf("got %d retry-after %q %q, want a 429 asking to send again in 1 s and naming acct-b", moved.Status, moved.Header.Get("Retry-After"), moved.ErrMsg)
	}
	if resent.Status != 200 || resent.Text != served("acct-b") {
		t.Fatalf("the resend got %d %q, want 200 from acct-b", resent.Status, resent.Text)
	}
	if want := []string{"acct-a", "acct-a", "acct-b"}; !reflect.DeepEqual(e.upstream.accountsServed(), want) {
		t.Fatalf("upstream attempts %v, want %v: one per client request", e.upstream.accountsServed(), want)
	}
}

func TestConcurrentExhaustedRequestsCommitOneMigration(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b", "acct-c")
	e.start()
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(time.Hour)
	for range 6 {
		e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})
	}

	results := make([]result, 6)
	var wg sync.WaitGroup
	for i := range results {
		wg.Add(1)
		go func() {
			defer wg.Done()
			results[i] = e.send(msg{Session: sessionID(1), Agent: "agent-" + strconv.Itoa(i)})
		}()
	}
	wg.Wait()

	dest := e.binding(sessionID(1)).Account
	for i, r := range results {
		if r.Status != 200 || r.Text != served(string(dest)) {
			t.Fatalf("request %d got %d %q, want 200 from the one destination %s", i, r.Status, r.Text, dest)
		}
	}
	if n := e.journalOps("migrate"); n != 1 {
		t.Fatalf("journal holds %d migrations, want 1", n)
	}
	if n := len(e.migrations()); n != 1 {
		t.Fatalf("%d migration events, want 1", n)
	}
}

func TestADispatchedRequestKeepsItsAccountWhileTheConversationMigrates(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	release := make(chan struct{})
	e.upstream.script("acct-a", reply{Before: func() { <-release }}, reply{Status: 429, Header: exhaustedHeaders(time.Now().Add(time.Hour))})

	slow := make(chan result, 1)
	go func() { slow <- e.send(msg{Session: sessionID(1), Agent: "agent-1"}) }()
	for len(e.upstream.inference()) < 2 {
		time.Sleep(5 * time.Millisecond)
	}
	moved := e.send(msg{Session: sessionID(1)})
	close(release)
	child := <-slow
	next := e.send(msg{Session: sessionID(1), Agent: "agent-1"})

	if moved.Text != served("acct-b") {
		t.Fatalf("the exhausting request served %q, want acct-b", moved.Text)
	}
	if child.Status != 200 || child.Text != served("acct-a") {
		t.Fatalf("the subagent request dispatched before the migration got %d %q, want 200 from acct-a", child.Status, child.Text)
	}
	if next.Text != served("acct-b") {
		t.Fatalf("the next subagent request served %q, want the destination acct-b", next.Text)
	}
}

func TestClientCancellationDuringTheMovedResponseKeepsTheMigration(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(time.Now().Add(time.Hour))})
	e.upstream.script("acct-b", reply{Events: 3, Hold: true})
	ctx, cancel := context.WithTimeout(context.Background(), 500*time.Millisecond)
	defer cancel()

	e.sendCtx(ctx, msg{Session: sessionID(1)})

	select {
	case <-e.upstream.ended:
	case <-time.After(5 * time.Second):
		t.Fatal("the destination's stream stayed open after the client went away")
	}
	if b := e.binding(sessionID(1)); b.Account != "acct-b" {
		t.Fatalf("after the cancellation the conversation is on %s, want the committed acct-b", b.Account)
	}
	if after := e.send(msg{Session: sessionID(1)}); after.Text != served("acct-b") {
		t.Fatalf("the explicit retry served %q, want acct-b", after.Text)
	}
}

func TestInterruptionAfterOutputIsReportedAndWaitsForAnExplicitRetry(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.upstream.script("acct-a", reply{Events: 4})

	cut := e.send(msg{Session: sessionID(1)})
	attempts := len(e.upstream.inference())
	retry := e.send(msg{Session: sessionID(1)})

	if want := []string{"message_start", "content_block_start", "content_block_delta", "content_block_stop", "error"}; !reflect.DeepEqual(cut.Events, want) {
		t.Fatalf("client saw %v, want the completed block and then an error event", cut.Events)
	}
	if attempts != 2 {
		t.Fatalf("%d upstream attempts after the cut, want 2: the router does not resend after output", attempts)
	}
	if retry.Status != 200 || retry.Text != served("acct-a") {
		t.Fatalf("explicit retry got %d %q, want 200 from the unchanged acct-a", retry.Status, retry.Text)
	}
	var outcomes []string
	for _, ev := range e.events() {
		if ev.Kind == "request" {
			outcomes = append(outcomes, ev.Outcome)
		}
	}
	if want := []string{"completed", "error_after_output", "completed"}; !reflect.DeepEqual(outcomes, want) {
		t.Fatalf("request outcomes %v, want %v", outcomes, want)
	}
}

func TestRevokedLoginKeepsAssignmentsAcrossRestartUntilMovedOrConfirmed(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.send(msg{Session: sessionID(2)})
	e.send(msg{Session: sessionID(3)})
	e.upstream.script("acct-a", reply{Status: 401}, reply{Status: 401})
	e.send(msg{Session: sessionID(1)})
	e.upstream.setUsage("acct-a", usageReply{Status: http.StatusUnauthorized})

	e.restart()
	stuck := e.send(msg{Session: sessionID(1)})
	fresh := e.send(msg{Session: sessionID(4)})
	st := e.svc.Status()

	if stuck.Status != http.StatusServiceUnavailable || !strings.Contains(stuck.ErrMsg, "claude-router login") {
		t.Fatalf("served conversation on the revoked account after restart: %d %q, want 503 naming claude-router login", stuck.Status, stuck.ErrMsg)
	}
	if fresh.Text != served("acct-b") {
		t.Fatalf("new conversation served %q, want acct-b", fresh.Text)
	}
	if !st.Accounts[0].NeedsLogin || st.Accounts[1].NeedsLogin {
		t.Fatalf("status %+v, want acct-a and only acct-a needing login", st.Accounts)
	}

	moved := e.move(sessionID(1), "acct-b")
	afterMove := e.send(msg{Session: sessionID(1)})
	if moved.Status != 200 || afterMove.Text != served("acct-b") {
		t.Fatalf("manual move answered %d, then the conversation served %q, want 200 and acct-b", moved.Status, afterMove.Text)
	}
	if m := e.migrations(); len(m) != 1 || m[0].Reason != string(router.ReasonManual) {
		t.Fatalf("migration events %+v, want one manual move", m)
	}

	e.upstream.mu.Lock()
	delete(e.upstream.replies, "acct-a")
	e.upstream.mu.Unlock()
	disabled := false
	e.upstream.setUsage("acct-a", usageReply{Enabled: &disabled})
	e.restart()
	if r := e.send(msg{Session: sessionID(3)}); r.Text != served("acct-a") {
		t.Fatalf("after a settings read with acct-a's credential succeeded, its conversation served %q, want acct-a", r.Text)
	}
}

func (e *env) move(session, to string) result {
	e.t.Helper()
	body, _ := json.Marshal(map[string]string{"conversation": session, "to": to})
	req, _ := http.NewRequest(http.MethodPost, "http://"+e.svc.Addr()+"/claude-router/move", bytes.NewReader(body))
	req.Header.Set("X-Api-Key", e.token)
	return readResult(e.t, req)
}

func TestMoveRequiresTheClientTokenAndAnAssignedConversation(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})

	body, _ := json.Marshal(map[string]string{"conversation": sessionID(1), "to": "acct-b"})
	req, _ := http.NewRequest(http.MethodPost, "http://"+e.svc.Addr()+"/claude-router/move", bytes.NewReader(body))
	noToken := readResult(t, req)
	unknown := e.move(sessionID(9), "acct-b")
	notEnrolled := e.move(sessionID(1), "acct-z")

	if noToken.Status != http.StatusUnauthorized || unknown.Status != http.StatusNotFound || notEnrolled.Status != http.StatusBadRequest {
		t.Fatalf("got %d, %d, %d; want 401 without the token, 404 for an unassigned conversation, 400 for an account not enrolled", noToken.Status, unknown.Status, notEnrolled.Status)
	}
	if b := e.binding(sessionID(1)); b.Account != "acct-a" {
		t.Fatalf("refused moves changed the binding to %s", b.Account)
	}
}

func TestRestartDuringAWaitKeepsTheResetAndResumesAfterIt(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(time.Hour).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})
	e.send(msg{Session: sessionID(1)})

	e.restart()
	waiting := e.send(msg{Session: sessionID(1)})
	attempts := len(e.upstream.inference())
	e.clock.set(reset)
	resumed := e.send(msg{Session: sessionID(1)})

	if waiting.Status != http.StatusTooManyRequests || resetHeader(waiting) != strconv.FormatInt(reset.Unix(), 10) {
		t.Fatalf("after the restart: %d reset %q, want the same 429 wait until %d", waiting.Status, resetHeader(waiting), reset.Unix())
	}
	if attempts != 2 {
		t.Fatalf("%d upstream attempts, want 2: none on the exhausted account after the restart", attempts)
	}
	if resumed.Status != 200 || resumed.Text != served("acct-a") {
		t.Fatalf("at the reset: %d %q, want 200 from acct-a", resumed.Status, resumed.Text)
	}
}

func upstreamSession(t *testing.T, body []byte) string {
	t.Helper()
	var b struct {
		Metadata struct {
			UserID string `json:"user_id"`
		} `json:"metadata"`
	}
	if json.Unmarshal(body, &b) != nil {
		t.Fatal("upstream body is not JSON")
	}
	s, _ := metadataSession(b.Metadata.UserID)
	return s
}

func TestAWaitRereadsAStaleAccountThatCouldServeBeforeTheReset(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	later := time.Now().Add(31 * time.Minute)
	e.clock.set(later)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(later.Add(3 * time.Hour))})
	readsBefore := len(e.upstream.usageReads())

	r := e.send(msg{Session: sessionID(1)})

	if r.Status != 200 || r.Text != served("acct-b") {
		t.Fatalf("got %d %q, want 200 from acct-b after rereading its stale setting", r.Status, r.Text)
	}
	if n := len(e.upstream.usageReads()) - readsBefore; n != 2 {
		t.Fatalf("%d settings reads, want 2: acct-a before dispatch and acct-b before the move", n)
	}
}

func TestAWaitThatOutlivesTheCheckReadsItAgainAtTheResetBeforeDispatch(t *testing.T) {
	for _, tc := range []struct {
		name   string
		usage  usageReply
		status int
	}{
		{"enabled", usageReply{Enabled: boolPtr(true)}, http.StatusServiceUnavailable},
		{"read fails", usageReply{Status: http.StatusInternalServerError}, http.StatusServiceUnavailable},
		{"disabled", usageReply{Enabled: boolPtr(false)}, http.StatusOK},
	} {
		t.Run(tc.name, func(t *testing.T) {
			e := newEnv(t, "acct-a")
			t0 := time.Now()
			e.clock.set(t0)
			e.start()
			e.send(msg{Session: sessionID(1)})
			reset := t0.Add(time.Hour).Truncate(time.Second)
			e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})
			e.send(msg{Session: sessionID(1)})
			e.upstream.setUsage("acct-a", usageReply{Status: http.StatusInternalServerError})
			e.clock.set(t0.Add(45 * time.Minute))
			during := e.send(msg{Session: sessionID(1)})
			e.upstream.setUsage("acct-a", tc.usage)
			reads, inference := len(e.upstream.usageReads()), len(e.upstream.inference())

			e.clock.set(reset.Add(time.Second))
			r := e.send(msg{Session: sessionID(1)})

			if during.Status != http.StatusTooManyRequests || during.Header.Get("X-Should-Retry") == "false" || resetHeader(during) != strconv.FormatInt(reset.Unix(), 10) {
				t.Fatalf("during the wait with a stale check: %d should-retry %q reset %q, want the wait until the reset", during.Status, during.Header.Get("X-Should-Retry"), resetHeader(during))
			}
			if r.Status != tc.status {
				t.Fatalf("the resend at the reset got %d %q, want %d", r.Status, r.ErrMsg, tc.status)
			}
			if n := len(e.upstream.usageReads()) - reads; n < 1 {
				t.Fatalf("%d settings reads at the resend, want the stale check read again", n)
			}
			want := 0
			if tc.status == http.StatusOK {
				want = 1
			}
			if n := len(e.upstream.inference()) - inference; n != want {
				t.Fatalf("%d inference attempts at the resend, want %d", n, want)
			}
			if want == 1 && e.upstream.inference()[inference].At.Before(e.upstream.usageReads()[reads].At) {
				t.Fatal("the dispatch did not follow the read")
			}
		})
	}
}
