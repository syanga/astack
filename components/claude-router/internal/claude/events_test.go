package claude

import (
	"bytes"
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"syscall"
	"testing"
)

type failOnceWriter struct {
	bytes.Buffer
	keep   int
	failed bool
}

func (w *failOnceWriter) Write(p []byte) (int, error) {
	if !w.failed {
		w.failed = true
		n, _ := w.Buffer.Write(p[:w.keep])
		return n, syscall.EFBIG
	}
	return w.Buffer.Write(p)
}

func (w *failOnceWriter) Close() error { return nil }

func parsedKinds(log []byte) []string {
	var kinds []string
	for _, line := range bytes.Split(log, []byte("\n")) {
		var e Event
		if json.Unmarshal(line, &e) == nil {
			kinds = append(kinds, e.Kind)
		}
	}
	return kinds
}

func TestAnEventAfterAFailedWriteIsStillLogged(t *testing.T) {
	for _, keep := range []int{0, 10} {
		t.Run(fmt.Sprintf("%d bytes written before the failure", keep), func(t *testing.T) {
			w := &failOnceWriter{keep: keep}
			l := &eventLog{out: w}

			l.emit(Event{Kind: "journal_failed"})
			l.emit(Event{Kind: "journal_recovered"})

			if got := parsedKinds(w.Bytes()); len(got) != 1 || got[0] != "journal_recovered" {
				t.Fatalf("events a reader parses after a failed write %v, want the later journal_recovered event", got)
			}
		})
	}
	t.Run("a log file that ends mid-line when the service starts", func(t *testing.T) {
		path := filepath.Join(t.TempDir(), "events.jsonl")
		if err := os.WriteFile(path, []byte(`{"at":"2026-11-18T00:00:00Z","kind":"journal_fa`), 0o600); err != nil {
			t.Fatal(err)
		}
		l, err := openEventLog(path)
		if err != nil {
			t.Fatal(err)
		}

		l.emit(Event{Kind: "journal_recovered"})
		if err := l.close(); err != nil {
			t.Fatal(err)
		}

		data, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		if got := parsedKinds(data); len(got) != 1 || got[0] != "journal_recovered" {
			t.Fatalf("events a reader parses after a restart over a partial line %v, want the new journal_recovered event", got)
		}
	})
}
