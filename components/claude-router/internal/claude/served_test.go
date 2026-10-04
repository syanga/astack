package claude

import (
	"bufio"
	"bytes"
	"context"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestServedMarkIsOnDiskBeforeTheClientSeesTheCompletedBlock(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.start()
	e.upstream.script("acct-a", reply{Events: 4, Hold: true})
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	req, _ := http.NewRequestWithContext(ctx, http.MethodPost, "http://"+e.svc.Addr()+"/v1/messages", bytes.NewReader(e.body(msg{Session: sessionID(1)})))
	req.Header.Set("X-Api-Key", e.token)
	req.Header.Set(headerSessionID, sessionID(1))
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()

	sawBlockStop := false
	sc := bufio.NewScanner(resp.Body)
	for sc.Scan() {
		if sc.Text() == "event: content_block_stop" {
			sawBlockStop = true
			break
		}
	}
	journal, err := os.ReadFile(filepath.Join(e.dir, "assignments", "assignments.jsonl"))
	if err != nil {
		t.Fatal(err)
	}

	if !sawBlockStop {
		t.Fatal("client never saw content_block_stop")
	}
	if !strings.Contains(string(journal), `"op":"served"`) {
		t.Fatalf("journal holds no served record when the client sees the completed block:\n%s", journal)
	}
}
