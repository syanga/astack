package claude

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"os"
	"strconv"
	"testing"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

func TestStatusReportsCapacityQuotaAndEachBinding(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.cfg.Accounts[0].Capacity = 5
	before := time.Now()
	e.start()
	fiveHour := time.Now().Add(3 * time.Hour).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Header: map[string]string{
		"anthropic-ratelimit-unified-5h-status":      "allowed",
		"anthropic-ratelimit-unified-5h-utilization": "0.38",
		"anthropic-ratelimit-unified-5h-reset":       strconv.FormatInt(fiveHour.Unix(), 10),
	}})
	e.send(msg{Session: sessionID(1)})
	e.send(msg{Session: sessionID(2)})

	sent := time.Now()
	st := e.svc.Status()

	a, b := st.Accounts[0], st.Accounts[1]
	if a.ID != "acct-a" || a.Capacity != 5 || b.Capacity != 1 {
		t.Fatalf("accounts %s cap %v, %s cap %v; want acct-a cap 5 and acct-b cap 1", a.ID, a.Capacity, b.ID, b.Capacity)
	}
	if len(a.Quota.Windows) != 1 || a.Quota.Windows[0].Kind != router.FiveHour ||
		a.Quota.Windows[0].Utilization == nil || *a.Quota.Windows[0].Utilization != 0.38 ||
		!a.Quota.Windows[0].ResetsAt.Equal(fiveHour) || a.Quota.At.IsZero() {
		t.Fatalf("acct-a quota %+v, want the 5-hour window at 0.38 resetting at %v, with its time", a.Quota, fiveHour)
	}
	if len(b.Quota.Windows) != 0 {
		t.Fatalf("acct-b quota %+v, want none: no response from acct-b carried quota headers", b.Quota)
	}
	if a.Bound+b.Bound != 2 || a.Active+b.Active != 2 || st.Conversations != 2 {
		t.Fatalf("bound %d+%d, active %d+%d, conversations %d; want 2 bound and 2 active", a.Bound, b.Bound, a.Active, b.Active, st.Conversations)
	}
	want := map[string]bool{conversationHash(router.ConversationID(sessionID(1))): true, conversationHash(router.ConversationID(sessionID(2))): true}
	for _, bs := range st.Bindings {
		if !want[bs.Conversation] || !bs.Served || bs.LastRequest.Before(before) || bs.LastRequest.After(sent) ||
			!bs.Active || bs.Account != a.ID && bs.Account != b.ID || bs.Reason != router.ReasonCapacity {
			t.Fatalf("binding %+v, want one of the two sent conversations, served, active, assigned for capacity, last request between %v and %v", bs, before, sent)
		}
		delete(want, bs.Conversation)
	}
	if len(want) != 0 {
		t.Fatalf("bindings %+v miss %v", st.Bindings, want)
	}
	if st.Journal != "ok" || st.PID != os.Getpid() || st.Started.Before(before) || st.Started.After(sent) || time.Duration(st.QuotaFreshFor) != 15*time.Minute {
		t.Fatalf("journal %q, pid %d, started %v, quota_fresh_for %v; want ok, pid %d, started between %v and %v, 15m", st.Journal, st.PID, st.Started, time.Duration(st.QuotaFreshFor), os.Getpid(), before, sent)
	}
}

func TestStatusCountsAnIdleConversationAsBoundButNotActive(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	e.clock.set(time.Now().Add(2 * time.Hour))
	e.send(msg{Session: sessionID(2)})

	st := e.svc.Status()

	if a := st.Accounts[0]; a.Bound != 2 || a.Active != 1 {
		t.Fatalf("bound %d, active %d; want 2 bound, 1 active: the first conversation's last request is 2 h old", a.Bound, a.Active)
	}
}

func TestAWaitEventCarriesTheResetItWaitsFor(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	reset := time.Now().Add(time.Hour).Truncate(time.Second)
	e.upstream.script("acct-a", reply{Status: 429, Header: exhaustedHeaders(reset)})

	e.send(msg{Session: sessionID(1)})
	e.send(msg{Session: sessionID(1)})

	var waits []Event
	for _, ev := range e.events() {
		if ev.Kind == "request" && ev.Decision == router.Wait {
			waits = append(waits, ev)
		}
	}
	if len(waits) != 2 || !waits[1].Until.Equal(reset) {
		t.Fatalf("wait events %+v, want 2, the last with until %v", waits, reset)
	}
}

func TestMoveTakesTheHashThatStatusShows(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	hash := conversationHash(router.ConversationID(sessionID(1)))

	moved := e.move(hash, "acct-b")
	after := e.send(msg{Session: sessionID(1)})

	if moved.Status != http.StatusOK || after.Text != served("acct-b") {
		t.Fatalf("move by hash got %d %q, next request served %q; want 200 and acct-b", moved.Status, moved.ErrMsg, after.Text)
	}
	if m := e.migrations(); len(m) != 1 || m[0].Conversation != hash || m[0].Reason != string(router.ReasonManual) {
		t.Fatalf("migration events %+v, want one manual move of %s", m, hash)
	}
}

func TestMoveRefusesAHashPrefixThatNamesTwoConversations(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	a, b := collidingSessions(4)
	e.send(msg{Session: a})
	e.send(msg{Session: b})
	prefix := conversationHash(router.ConversationID(a))[:4]
	before := []router.AccountID{e.binding(a).Account, e.binding(b).Account}

	ambiguous := e.move(prefix, "acct-b")
	unknown := e.move("0123456789ab", "acct-b")

	if ambiguous.Status != http.StatusConflict || ambiguous.ErrMsg != "claude-router: "+prefix+" matches 2 conversations; give more of the hash" {
		t.Fatalf("ambiguous prefix got %d %q, want 409 naming 2 matches", ambiguous.Status, ambiguous.ErrMsg)
	}
	if unknown.Status != http.StatusNotFound {
		t.Fatalf("a hash with no match got %d, want 404", unknown.Status)
	}
	if e.binding(a).Account != before[0] || e.binding(b).Account != before[1] {
		t.Fatal("a refused move changed a binding")
	}
}

func collidingSessions(n int) (string, string) {
	seen := map[string]string{}
	for i := 0; ; i++ {
		id := sessionID(1000 + i)
		sum := sha256.Sum256([]byte(id))
		p := hex.EncodeToString(sum[:])[:n]
		if other, ok := seen[p]; ok {
			return other, id
		}
		seen[p] = id
	}
}
