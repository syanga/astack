package claude

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"syscall"
	"testing"
	"time"
)

func TestAJournalFailureAnswers503NamingItsCauseInStatusAndOneEvent(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.send(msg{Session: sessionID(1)})
	_ = e.svc.store.Close()

	first := e.send(msg{Session: sessionID(2)})
	later := e.send(msg{Session: sessionID(3), NonStream: true})
	st := e.svc.Status()
	var logged []Event
	for _, ev := range e.events() {
		if strings.HasPrefix(ev.Kind, "journal_") {
			logged = append(logged, ev)
		}
	}

	cause := os.ErrClosed.Error()
	for name, r := range map[string]result{"first": first, "later": later} {
		if r.Status != http.StatusServiceUnavailable || !strings.Contains(r.ErrMsg, "assignment journal unavailable") || !strings.Contains(r.ErrMsg, cause) {
			t.Errorf("%s request after the journal failed answered %d %q, want a 503 naming %q", name, r.Status, r.ErrMsg, cause)
		}
	}
	if !strings.Contains(st.Journal, cause) || st.JournalFailure == nil || st.JournalFailure.Since.IsZero() {
		t.Fatalf("status journal %q, failure %+v; want the cause and when it failed", st.Journal, st.JournalFailure)
	}
	if len(logged) != 1 || logged[0].Kind != "journal_failed" || !strings.Contains(logged[0].Detail, cause) {
		t.Fatalf("journal events %+v, want one journal_failed naming %q", logged, cause)
	}
}

func failAccountStateWrite(e *env, session string) (string, result) {
	e.t.Helper()
	home := string(e.binding(session).Account)
	state := filepath.Join(e.dir, "assignments")
	if err := os.Chmod(state, 0o500); err != nil {
		e.t.Fatal(err)
	}
	e.t.Cleanup(func() { os.Chmod(state, 0o700) })
	e.upstream.script(home, reply{Status: http.StatusTooManyRequests, Header: exhaustedHeaders(time.Now().Add(2 * time.Hour))})
	failed := e.send(msg{Session: session})
	if err := os.Chmod(state, 0o700); err != nil {
		e.t.Fatal(err)
	}
	return home, failed
}

func TestALaterRequestRecoversTheJournalWithoutARestart(t *testing.T) {
	t.Parallel()
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})

	home, failed := failAccountStateWrite(e, sessionID(1))
	time.Sleep(5*time.Second + 200*time.Millisecond)
	moved := e.send(msg{Session: sessionID(1)})
	placed := e.send(msg{Session: sessionID(2)})
	st := e.svc.Status()
	var kinds []string
	var recovered Event
	for _, ev := range e.events() {
		if strings.HasPrefix(ev.Kind, "journal_") {
			kinds = append(kinds, ev.Kind)
		}
		if ev.Kind == "journal_recovered" {
			recovered = ev
		}
	}

	if failed.Status != http.StatusServiceUnavailable || !strings.Contains(failed.ErrMsg, "assignment journal unavailable") || !strings.Contains(failed.ErrMsg, "permission denied") {
		t.Fatalf("request whose account state write failed answered %d %q, want a 503 naming permission denied", failed.Status, failed.ErrMsg)
	}
	if retry, after := failed.Header.Get("X-Should-Retry"), failed.Header.Get("Retry-After"); retry != "true" || after != "5" {
		t.Fatalf("the 503 while recovery can succeed carried x-should-retry %q and retry-after %q, want true and 5", retry, after)
	}
	if moved.Status != http.StatusOK || moved.Text == served(home) || placed.Status != http.StatusOK {
		t.Fatalf("after the directory became writable the exhausted conversation got %d %q and a new one %d; want both served, the first off %s", moved.Status, moved.Text, placed.Status, home)
	}
	if st.Journal != "ok" || st.JournalFailure != nil || strings.Join(kinds, " ") != "journal_failed journal_recovered" {
		t.Fatalf("status journal %q, failure %+v, journal events %v; want ok after journal_failed then journal_recovered", st.Journal, st.JournalFailure, kinds)
	}
	if !strings.HasPrefix(recovered.Detail, "after 0 failed attempts since ") {
		t.Fatalf("journal_recovered detail %q, want the count of failed attempts and the time of the failure", recovered.Detail)
	}
}

func TestAReplacedLockFileAnswers503AskingForARestart(t *testing.T) {
	t.Parallel()
	e := newEnv(t, "acct-a", "acct-b")
	e.start()
	e.send(msg{Session: sessionID(1)})
	failAccountStateWrite(e, sessionID(1))
	if err := os.Remove(filepath.Join(e.dir, "assignments", "lock")); err != nil {
		t.Fatal(err)
	}

	time.Sleep(5*time.Second + 200*time.Millisecond)
	r := e.send(msg{Session: sessionID(2)})

	if r.Status != http.StatusServiceUnavailable || !strings.HasSuffix(r.ErrMsg, "lock file was replaced; restart the router") {
		t.Fatalf("request after the lock file was removed answered %d %q, want a 503 that ends asking for a restart", r.Status, r.ErrMsg)
	}
	if retry, after := r.Header.Get("X-Should-Retry"), r.Header.Get("Retry-After"); retry != "false" || after != "" {
		t.Fatalf("the 503 that asks for a restart carried x-should-retry %q and retry-after %q, want false and none", retry, after)
	}
}

type failOnceWriter struct {
	bytes.Buffer
	failed bool
}

func (w *failOnceWriter) Write(p []byte) (int, error) {
	if !w.failed {
		w.failed = true
		return 0, syscall.EFBIG
	}
	return w.Buffer.Write(p)
}

func (w *failOnceWriter) Close() error { return nil }

func TestAnEventAfterAFailedWriteIsStillLogged(t *testing.T) {
	w := &failOnceWriter{}
	l := &eventLog{out: w}

	l.emit(Event{Kind: "journal_failed", Detail: "lost"})
	l.emit(Event{Kind: "journal_recovered", Detail: "after 1 failed attempts"})

	var got []Event
	for _, line := range bytes.Split(bytes.TrimSpace(w.Bytes()), []byte("\n")) {
		var e Event
		if err := json.Unmarshal(line, &e); err != nil {
			t.Fatalf("event line %q: %v", line, err)
		}
		got = append(got, e)
	}
	if len(got) != 1 || got[0].Kind != "journal_recovered" {
		t.Fatalf("events after a failed write %+v, want the later journal_recovered event", got)
	}
}
