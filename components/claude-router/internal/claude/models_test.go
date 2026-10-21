package claude

import (
	"context"
	"encoding/json"
	"net/http"
	"reflect"
	"testing"
	"time"
)

const fiveFiveThinking = `"thinking": {"zero_allowed": true, "dynamic_allowed": true, "levels": ["low", "medium", "high", "xhigh", "max"]}`

const testCatalog = `{"claude": [
	{"id": "claude-opus-5-5", "object": "model", "owned_by": "anthropic", "type": "claude", "context_length": 1000000, "max_completion_tokens": 128000, ` + fiveFiveThinking + `},
	{"id": "claude-haiku-5-5", "object": "model", "owned_by": "anthropic", "type": "claude", "context_length": 1000000, "max_completion_tokens": 128000, ` + fiveFiveThinking + `},
	{"id": "gpt-not-claude", "object": "model", "type": "claude"}
]}`

func catalog(body string) func(context.Context) ([]byte, error) {
	return func(context.Context) ([]byte, error) { return []byte(body), nil }
}

func thinkingBody(e *env, session, model string) []byte {
	var m map[string]any
	_ = json.Unmarshal(e.body(msg{Session: session, Model: model}), &m)
	m["thinking"] = map[string]any{"type": "adaptive"}
	m["output_config"] = map[string]any{"effort": "high"}
	b, _ := json.Marshal(m)
	return b
}

func upstreamFields(t *testing.T, e *env, keys ...string) map[string]any {
	t.Helper()
	sent := e.upstream.inference()
	var body map[string]any
	if err := json.Unmarshal(sent[len(sent)-1].Body, &body); err != nil {
		t.Fatal(err)
	}
	out := map[string]any{}
	for _, k := range keys {
		out[k] = body[k]
	}
	return out
}

func TestAModelOnlyTheCatalogListsIsServedWithItsThinkingSettings(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.startWith(Options{ModelCatalog: catalog(testCatalog)})

	r := e.send(msg{Session: sessionID(1), Body: thinkingBody(e, sessionID(1), "claude-haiku-5-5")})
	sent := upstreamFields(t, e, "model", "thinking", "output_config")

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("claude-haiku-5-5 got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
	want := map[string]any{"model": "claude-haiku-5-5", "thinking": map[string]any{"type": "adaptive"}, "output_config": map[string]any{"effort": "high"}}
	if !reflect.DeepEqual(sent, want) {
		t.Fatalf("upstream got %v, want the client's model and thinking settings %v", sent, want)
	}
	if got := e.eventDetails("model_catalog", "read"); !reflect.DeepEqual(got, []string{"claude-haiku-5-5"}) {
		t.Fatalf("model_catalog events %q, want one read that added claude-haiku-5-5 only", got)
	}
}

func TestWithoutACatalogTheRouterServesTheBuiltInModels(t *testing.T) {
	for name, read := range map[string]func(context.Context) ([]byte, error){
		"no catalog":         noCatalog,
		"not the SDK format": catalog(`{"claude": "many"}`),
		"no claude models":   catalog(`{"claude": [{"id": "gpt-5", "type": "claude"}]}`),
	} {
		t.Run(name, func(t *testing.T) {
			e := newEnv(t, "acct-a")
			e.startWith(Options{ModelCatalog: read})

			known := e.send(msg{Session: sessionID(1), Model: "claude-opus-5-5"})
			unknown := e.send(msg{Session: sessionID(2), Model: "claude-haiku-5-5"})

			if known.Status != 200 {
				t.Fatalf("claude-opus-5-5 got %d, want 200 from the built-in list", known.Status)
			}
			if unknown.Status != http.StatusServiceUnavailable {
				t.Fatalf("claude-haiku-5-5 got %d, want 503: the built-in list lacks it", unknown.Status)
			}
			if n := len(e.eventDetails("model_catalog", "failed")); n != 1 {
				t.Fatalf("%d failed model_catalog events, want 1", n)
			}
		})
	}
}

func TestACatalogModelIsServedAfterTheSDKRenewsTheAccountsCredential(t *testing.T) {
	e := newEnv(t, "acct-a")
	e.writeCredentialDueIn("acct-a", 3*time.Second)
	e.clock.set(time.Now())
	e.startWith(Options{ModelCatalog: catalog(testCatalog)})
	x := &tokenExchange{up: e.upstream, account: "acct-a", accept: true}
	e.replaceTokenExchange(x)
	e.waitFor("the SDK renewed the credential", 15*time.Second, func() bool {
		return x.exchanges(t) == 1 && !refreshDue(e.sdkAuth("acct-a"), time.Now())
	})

	r := e.send(msg{Session: sessionID(1), Model: "claude-haiku-5-5"})

	if r.Status != 200 || r.Text != served("acct-a") {
		t.Fatalf("claude-haiku-5-5 after the SDK's renewal got %d %q, want 200 from acct-a", r.Status, r.Text)
	}
}

func (e *env) eventDetails(kind, outcome string) []string {
	var out []string
	for _, ev := range e.events() {
		if ev.Kind == kind && ev.Outcome == outcome {
			out = append(out, ev.Detail)
		}
	}
	return out
}
