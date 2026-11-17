package claude

import (
	"bytes"
	"encoding/json"
	"fmt"
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

func TestAnEventAfterAFailedWriteIsStillLogged(t *testing.T) {
	for _, keep := range []int{0, 10} {
		t.Run(fmt.Sprintf("%d bytes written before the failure", keep), func(t *testing.T) {
			w := &failOnceWriter{keep: keep}
			l := &eventLog{out: w}

			l.emit(Event{Kind: "journal_failed"})
			l.emit(Event{Kind: "journal_recovered"})

			var got []string
			for _, line := range bytes.Split(w.Bytes(), []byte("\n")) {
				var e Event
				if json.Unmarshal(line, &e) == nil {
					got = append(got, e.Kind)
				}
			}
			if len(got) != 1 || got[0] != "journal_recovered" {
				t.Fatalf("events a reader parses after a failed write %v, want the later journal_recovered event", got)
			}
		})
	}
}
