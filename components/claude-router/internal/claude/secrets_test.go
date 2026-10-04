package claude

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestNoSecretInStatusEventsOrLogs(t *testing.T) {
	e := newEnv(t, "acct-a", "acct-b", "acct-c")
	e.upstream.setUsage("acct-c", usageReply{Status: http.StatusNotFound})
	e.start()
	e.upstream.script("acct-a", reply{Header: map[string]string{"anthropic-ratelimit-unified-overage-in-use": "true"}})
	e.upstream.script("acct-b", reply{Status: 503}, reply{Status: 401})
	e.send(msg{Session: sessionID(1), Token: "-"})
	e.send(msg{Session: sessionID(1), Token: "client-" + randomHex(24)})
	e.send(msg{Session: sessionID(1), NoSession: true})
	e.send(msg{Session: sessionID(1)})
	e.send(msg{Session: sessionID(2)})
	e.send(msg{Session: sessionID(3), NonStream: true})
	e.send(msg{Session: sessionID(3)})

	req, _ := http.NewRequest(http.MethodGet, "http://"+e.svc.Addr()+"/claude-router/status", nil)
	req.Header.Set("X-Api-Key", e.token)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	var status Status
	if err := json.NewDecoder(resp.Body).Decode(&status); err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	statusJSON, _ := json.Marshal(status)
	e.svc.Close()
	events, err := os.ReadFile(filepath.Join(e.dir, "events.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	sdkConfig, err := os.ReadFile(filepath.Join(e.dir, "sdk-config.yaml"))
	if err != nil {
		t.Fatal(err)
	}

	secrets := map[string]string{"client token": e.token}
	for account, token := range e.tokens {
		secrets[account+" access token"] = token
		secrets[account+" token suffix"] = token[len(token)-16:]
	}
	outputs := map[string]string{
		"status":     string(statusJSON),
		"events":     string(events),
		"SDK logs":   sdkLogs.String(),
		"SDK config": string(sdkConfig),
	}
	if !strings.Contains(outputs["SDK logs"], watcherStartedMessage) {
		t.Fatal("the test captured no SDK log output, so the log check would be vacuous")
	}
	for out, text := range outputs {
		for name, secret := range secrets {
			if strings.Contains(text, secret) {
				t.Fatalf("%s contains the %s", out, name)
			}
		}
	}
	if len(status.Accounts) != 3 || !status.Accounts[0].Registered {
		t.Fatalf("status %s does not report the enrolled accounts", statusJSON)
	}
	if !strings.Contains(string(events), `"kind":"paid_use_observed"`) || !strings.Contains(string(events), `"kind":"client_auth_rejected"`) {
		t.Fatal("events do not record the paid-use and client-auth outcomes the test drove")
	}
}
