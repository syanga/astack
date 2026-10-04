package claude

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

type fakeUpstream struct {
	mu       sync.Mutex
	accounts map[string]string
	replies  map[string][]reply
	usage    map[string]usageReply
	seen     []seenRequest
	ended    chan time.Time
}

type reply struct {
	Status      int
	Header      map[string]string
	Events      int
	StreamError string
	DataOnly    bool
	NoUsage     bool
	Hold        bool
	CleanCut    bool
	BodyCut     bool
	Drop        bool
	Before      func()
	Body        string
}

type usageReply struct {
	Status  int
	Enabled *bool
	Before  func()
}

type seenRequest struct {
	Account string
	Path    string
	Token   string
	Header  http.Header
	Body    []byte
	At      time.Time
}

func newFakeUpstream() *fakeUpstream {
	return &fakeUpstream{accounts: map[string]string{}, replies: map[string][]reply{}, usage: map[string]usageReply{}, ended: make(chan time.Time, 16)}
}

func hashToken(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])[:12]
}

func (u *fakeUpstream) bind(token, account string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.accounts[hashToken(token)] = account
}

func (u *fakeUpstream) script(account string, rs ...reply) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.replies[account] = append(u.replies[account], rs...)
}

func (u *fakeUpstream) setUsage(account string, r usageReply) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.usage[account] = r
}

func (u *fakeUpstream) inference() []seenRequest {
	u.mu.Lock()
	defer u.mu.Unlock()
	var out []seenRequest
	for _, r := range u.seen {
		if r.Path == "/v1/messages" {
			out = append(out, r)
		}
	}
	return out
}

func (u *fakeUpstream) usageReads() []seenRequest {
	u.mu.Lock()
	defer u.mu.Unlock()
	var out []seenRequest
	for _, r := range u.seen {
		if r.Path == "/api/oauth/usage" {
			out = append(out, r)
		}
	}
	return out
}

func (u *fakeUpstream) accountsServed() []string {
	var out []string
	for _, r := range u.inference() {
		out = append(out, r.Account)
	}
	return out
}

func (u *fakeUpstream) RoundTrip(req *http.Request) (*http.Response, error) {
	var body []byte
	if req.Body != nil {
		body, _ = io.ReadAll(req.Body)
		_ = req.Body.Close()
	}
	token := strings.TrimSpace(strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer "))
	if token == "" {
		token = req.Header.Get("X-Api-Key")
	}
	u.mu.Lock()
	account, known := u.accounts[hashToken(token)]
	if !known {
		account = "unknown"
	}
	u.seen = append(u.seen, seenRequest{Account: account, Path: req.URL.Path, Token: token, Header: req.Header.Clone(), Body: body, At: time.Now()})
	var r reply
	if req.URL.Path == "/v1/messages" {
		if q := u.replies[account]; len(q) > 0 {
			r, u.replies[account] = q[0], q[1:]
		}
	}
	usage, usageSet := u.usage[account]
	u.mu.Unlock()
	if r.Before != nil {
		r.Before()
	}
	if r.Drop {
		return nil, io.ErrUnexpectedEOF
	}

	if !known {
		return respond(req, http.StatusUnauthorized, nil, errorBody("authentication_error", "unknown token")), nil
	}
	switch req.URL.Path {
	case "/api/oauth/usage":
		if usage.Before != nil {
			usage.Before()
		}
		if !usageSet {
			f := false
			usage = usageReply{Enabled: &f}
		}
		if usage.Status != 0 && usage.Status != http.StatusOK {
			return respond(req, usage.Status, nil, errorBody("not_found_error", "no usage")), nil
		}
		doc := map[string]any{"five_hour": map[string]any{"utilization": 10.0, "resets_at": "2026-10-04T05:00:00Z"}}
		if usage.Enabled != nil {
			doc["extra_usage"] = map[string]any{"is_enabled": *usage.Enabled, "monthly_limit": nil, "used_credits": nil, "utilization": nil}
		}
		b, _ := json.Marshal(doc)
		return respond(req, http.StatusOK, map[string]string{"Content-Type": "application/json"}, string(b)), nil
	case "/v1/messages":
	default:
		return respond(req, http.StatusNotFound, nil, errorBody("not_found_error", "no such path")), nil
	}
	if r.Status != 0 && r.Status != http.StatusOK {
		b := r.Body
		if b == "" {
			b = errorBody(errorType(r.Status), http.StatusText(r.Status))
		}
		return respond(req, r.Status, r.Header, b), nil
	}
	var shape struct {
		Stream bool `json:"stream"`
	}
	_ = json.Unmarshal(body, &shape)
	if !shape.Stream {
		h := map[string]string{"Content-Type": "application/json"}
		for k, v := range r.Header {
			h[k] = v
		}
		resp := respond(req, http.StatusOK, h, messageBody(account, r.NoUsage))
		if r.BodyCut {
			whole := messageBody(account, r.NoUsage)
			resp.Body = io.NopCloser(io.MultiReader(strings.NewReader(whole[:len(whole)/2]), errReader{io.ErrUnexpectedEOF}))
			resp.ContentLength = -1
		}
		return resp, nil
	}
	return stream(req, account, r, u.ended), nil
}

type errReader struct{ err error }

func (r errReader) Read([]byte) (int, error) { return 0, r.err }

func errorBody(kind, msg string) string {
	return fmt.Sprintf(`{"type":"error","error":{"type":%q,"message":%q}}`, kind, msg)
}

func respond(req *http.Request, status int, header map[string]string, body string) *http.Response {
	h := http.Header{}
	for k, v := range header {
		h.Set(k, v)
	}
	if h.Get("Content-Type") == "" {
		h.Set("Content-Type", "application/json")
	}
	return &http.Response{
		StatusCode: status, Status: fmt.Sprintf("%d %s", status, http.StatusText(status)),
		Header: h, Body: io.NopCloser(strings.NewReader(body)), ContentLength: int64(len(body)),
		Request: req, ProtoMajor: 1, ProtoMinor: 1,
	}
}

const usageJSONFull = `"usage":{"input_tokens":12,"output_tokens":3,"cache_creation_input_tokens":100,"cache_read_input_tokens":2000}`

func messageBody(account string, noUsage bool) string {
	usage := "," + usageJSONFull
	if noUsage {
		usage = ""
	}
	return `{"id":"msg_fake","type":"message","role":"assistant","model":"claude-sonnet-4-5-20250929","content":[{"type":"text","text":"served-by:` + account + `"}],"stop_reason":"end_turn","stop_sequence":null` + usage + `}`
}

func streamEvents(account string, noUsage bool) []string {
	startUsage, deltaUsage := ","+usageJSONFull, `,"usage":{"output_tokens":3}`
	if noUsage {
		startUsage, deltaUsage = "", ""
	}
	return []string{
		"event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_fake\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-sonnet-4-5-20250929\",\"content\":[],\"stop_reason\":null,\"stop_sequence\":null" + startUsage + "}}\n\n",
		"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n",
		"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"served-by:" + account + "\"}}\n\n",
		"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n",
		"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\",\"stop_sequence\":null}" + deltaUsage + "}\n\n",
		"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
	}
}

func stream(req *http.Request, account string, r reply, ended chan<- time.Time) *http.Response {
	pr, pw := io.Pipe()
	h := http.Header{"Content-Type": {"text/event-stream"}}
	for k, v := range r.Header {
		h.Set(k, v)
	}
	events := streamEvents(account, r.NoUsage)
	go func() {
		if r.StreamError != "" {
			prefix := "event: error\n"
			if r.DataOnly {
				prefix = ""
			}
			_, _ = io.WriteString(pw, prefix+"data: "+errorBody(r.StreamError, "scripted")+"\n\n")
			_ = pw.Close()
			return
		}
		limit := len(events)
		if r.Events > 0 {
			limit = r.Events
		}
		for i := 0; i < limit; i++ {
			select {
			case <-req.Context().Done():
				_ = pw.CloseWithError(req.Context().Err())
				return
			default:
			}
			if _, err := io.WriteString(pw, events[i]); err != nil {
				return
			}
		}
		if r.Hold {
			<-req.Context().Done()
			ended <- time.Now()
			_ = pw.CloseWithError(req.Context().Err())
			return
		}
		if limit < len(events) && !r.CleanCut {
			_ = pw.CloseWithError(io.ErrUnexpectedEOF)
			return
		}
		_ = pw.Close()
	}()
	return &http.Response{
		StatusCode: http.StatusOK, Status: "200 OK", Header: h, Body: pr, ContentLength: -1,
		Request: req, ProtoMajor: 1, ProtoMinor: 1,
	}
}
