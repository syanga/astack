package claude

import (
	"bytes"
	"encoding/json"
	"net/http"
	"os"
	"reflect"
	"regexp"
	"sort"
	"testing"
)

// claudeCodeHeaders are the headers terminal Claude 2.1.285 sent in the
// PR1 client captures, minus credentials.
var claudeCodeHeaders = map[string]string{
	"Accept":                      "application/json",
	"Content-Type":                "application/json",
	"User-Agent":                  "claude-cli/2.1.285 (external, cli)",
	"X-Stainless-Arch":            "arm64",
	"X-Stainless-Lang":            "js",
	"X-Stainless-Os":              "MacOS",
	"X-Stainless-Package-Version": "0.127.0",
	"X-Stainless-Retry-Count":     "0",
	"X-Stainless-Runtime":         "node",
	"X-Stainless-Runtime-Version": "v26.3.0",
	"X-Stainless-Timeout":         "600",
	"Anthropic-Beta":              "claude-code-20250219,oauth-2025-04-20,interleaved-thinking-2025-05-14,context-management-2025-06-27,prompt-caching-scope-2026-01-05,extended-cache-ttl-2025-04-11",
	"Anthropic-Version":           "2023-06-01",
	"X-App":                       "cli",
}

const fixtureSession = "8de105ca-72b2-4016-8e13-6f9119960d27"

func (e *env) sendRaw(body []byte, header map[string]string) result {
	e.t.Helper()
	req, err := http.NewRequest(http.MethodPost, "http://"+e.svc.Addr()+"/v1/messages?beta=true", bytes.NewReader(body))
	if err != nil {
		e.t.Fatal(err)
	}
	for k, v := range header {
		req.Header.Set(k, v)
	}
	req.Header.Set("Authorization", "Bearer "+e.token)
	return readResult(e.t, req)
}

func rawFields(t *testing.T, body []byte) map[string]json.RawMessage {
	t.Helper()
	var m map[string]json.RawMessage
	if err := json.Unmarshal(body, &m); err != nil {
		t.Fatalf("body is not a JSON object: %v", err)
	}
	return m
}

// billingCCH is the billing attestation in the first system block. The SDK
// recomputes it over the body it sends, because it rewrites
// metadata.user_id for the credential.
var billingCCH = regexp.MustCompile(`cch=[0-9a-f]{5};`)

func metadataUserID(t *testing.T, raw json.RawMessage) map[string]string {
	t.Helper()
	var meta struct {
		UserID string `json:"user_id"`
	}
	var fields map[string]string
	if json.Unmarshal(raw, &meta) != nil || json.Unmarshal([]byte(meta.UserID), &fields) != nil {
		t.Fatalf("metadata.user_id is not a JSON object string: %s", raw)
	}
	return fields
}

// TestUpstreamReceivesTheClientsCacheablePrefix sends a Claude Code tool
// turn and compares, byte for byte, every top-level field the upstream
// receives with what the client sent. The documented credential
// transformations are the only differences allowed: metadata.user_id's
// device_id and account_uuid, and the billing attestation over the
// rewritten body.
func TestUpstreamReceivesTheClientsCacheablePrefix(t *testing.T) {
	body, err := os.ReadFile("../../testdata/requests/claude-code-tool-turn.json")
	if err != nil {
		t.Fatal(err)
	}
	e := newEnv(t, "acct-a")
	e.start()
	h := map[string]string{headerSessionID: fixtureSession}
	for k, v := range claudeCodeHeaders {
		h[k] = v
	}
	first := e.sendRaw(body, h)
	second := e.sendRaw(body, h)
	if first.Status != 200 || second.Status != 200 {
		t.Fatalf("statuses %d and %d, want 200", first.Status, second.Status)
	}

	seen := e.upstream.inference()
	in, out := rawFields(t, body), rawFields(t, seen[0].Body)
	keys := func(m map[string]json.RawMessage) []string {
		var ks []string
		for k := range m {
			ks = append(ks, k)
		}
		sort.Strings(ks)
		return ks
	}
	if !reflect.DeepEqual(keys(in), keys(out)) {
		t.Fatalf("upstream body fields %v, client sent %v", keys(out), keys(in))
	}
	for _, k := range keys(in) {
		switch k {
		case "metadata":
			client, upstream := metadataUserID(t, in[k]), metadataUserID(t, out[k])
			if upstream["session_id"] != client["session_id"] {
				t.Fatalf("upstream session_id %q, client sent %q", upstream["session_id"], client["session_id"])
			}
		case "system":
			if !bytes.Equal(billingCCH.ReplaceAll(in[k], []byte("cch=00000;")), billingCCH.ReplaceAll(out[k], []byte("cch=00000;"))) {
				t.Fatalf("system changed beyond the billing attestation:\nclient:   %s\nupstream: %s", in[k], out[k])
			}
		default:
			if !bytes.Equal(in[k], out[k]) {
				t.Fatalf("%s changed:\nclient:   %s\nupstream: %s", k, in[k], out[k])
			}
		}
	}
	if !bytes.Equal(seen[0].Body, seen[1].Body) {
		t.Fatal("two identical client requests reached the upstream with different bytes")
	}
	for _, s := range seen {
		if s.Token != e.tokens["acct-a"] {
			t.Fatal("upstream did not receive the pinned account's credential")
		}
		if s.Header.Get(headerSessionID) != fixtureSession {
			t.Fatalf("upstream session header %q, want the client's", s.Header.Get(headerSessionID))
		}
	}
}
