package probes

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"
	"sync"
	"time"
)

const anthropicHost = "api.anthropic.com"

// Reply scripts one upstream response. The zero value streams a complete
// successful message.
type Reply struct {
	Status int
	Header map[string]string
	Body   string
	// PartialEvents is the number of SSE events written before the stream
	// fails. Zero with Fail set fails before any output.
	PartialEvents int
	// Fail ends a 200 stream early: "drop" breaks the connection and
	// "overloaded" writes an in-stream overloaded_error event.
	Fail string
	// Hold blocks after PartialEvents until the request context ends.
	Hold bool
}

// Attempt is one sanitized upstream inference attempt.
type Attempt struct {
	Seq int `json:"seq"`
	// Call is the router call ID read from the request context, which is how
	// a router-owned transport ties each upstream attempt to its request.
	Call       string    `json:"call"`
	Account    string    `json:"account"`
	TokenHash  string    `json:"token_sha256_prefix"`
	Status     int       `json:"status"`
	Outcome    string    `json:"outcome"`
	Events     int       `json:"events_sent"`
	StartedAt  time.Time `json:"started_at"`
	EndedAt    time.Time `json:"ended_at"`
	CanceledBy string    `json:"canceled_by,omitempty"`
	// RateLimitHeaders are the rate-limit headers of this attempt's response.
	RateLimitHeaders map[string]string `json:"rate_limit_headers,omitempty"`
}

type callKey struct{}

// WithCall tags a context with a router call ID. Every upstream attempt made
// under that context records the ID.
func WithCall(ctx context.Context, call string) context.Context {
	return context.WithValue(ctx, callKey{}, call)
}

// AttemptsFor returns the attempts made under one router call.
func (u *Upstream) AttemptsFor(call string) []Attempt {
	var out []Attempt
	for _, a := range u.Attempts() {
		if a.Call == call {
			out = append(out, a)
		}
	}
	return out
}

// Upstream is an in-process fake of the Anthropic Messages API. It serves
// every request the SDK sends through the manager's round tripper and refuses
// every other host, so no request leaves the process.
type Upstream struct {
	mu       sync.Mutex
	accounts map[string]string
	scripts  map[string][]Reply
	attempts []*Attempt
	refused  []string
}

func NewUpstream() *Upstream {
	return &Upstream{
		accounts: map[string]string{},
		scripts:  map[string][]Reply{},
	}
}

func tokenHash(token string) string {
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:])[:12]
}

// Bind maps a fake access token to an account name.
func (u *Upstream) Bind(token, account string) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.accounts[tokenHash(token)] = account
}

// Script queues replies for an account. Requests beyond the queue succeed.
func (u *Upstream) Script(account string, replies ...Reply) {
	u.mu.Lock()
	defer u.mu.Unlock()
	u.scripts[account] = append(u.scripts[account], replies...)
}

// Attempts returns a copy of every attempt recorded so far.
func (u *Upstream) Attempts() []Attempt {
	u.mu.Lock()
	defer u.mu.Unlock()
	out := make([]Attempt, len(u.attempts))
	for i, a := range u.attempts {
		out[i] = *a
	}
	return out
}

// Refused lists hosts the SDK tried to reach other than the fake API.
func (u *Upstream) Refused() []string {
	u.mu.Lock()
	defer u.mu.Unlock()
	return append([]string(nil), u.refused...)
}

// AccountsAttempted lists the account of every attempt in order.
func (u *Upstream) AccountsAttempted() []string {
	attempts := u.Attempts()
	out := make([]string, len(attempts))
	for i, a := range attempts {
		out[i] = a.Account
	}
	return out
}

// WaitEnded blocks until n attempts have finished or the timeout passes.
func (u *Upstream) WaitEnded(n int, timeout time.Duration) bool {
	deadline := time.Now().Add(timeout)
	for {
		u.mu.Lock()
		ended := 0
		for _, a := range u.attempts {
			if !a.EndedAt.IsZero() {
				ended++
			}
		}
		u.mu.Unlock()
		if ended >= n {
			return true
		}
		if time.Now().After(deadline) {
			return false
		}
		time.Sleep(10 * time.Millisecond)
	}
}

func (u *Upstream) RoundTrip(req *http.Request) (*http.Response, error) {
	if req.Body != nil {
		_, _ = io.Copy(io.Discard, req.Body)
		_ = req.Body.Close()
	}
	if !strings.EqualFold(req.URL.Hostname(), anthropicHost) || !strings.HasPrefix(req.URL.Path, "/v1/messages") {
		u.mu.Lock()
		u.refused = append(u.refused, req.URL.Hostname()+req.URL.Path)
		u.mu.Unlock()
		return nil, fmt.Errorf("probe upstream refuses %s", req.URL.Hostname())
	}
	token := strings.TrimSpace(strings.TrimPrefix(req.Header.Get("Authorization"), "Bearer "))
	if token == "" {
		token = req.Header.Get("x-api-key")
	}
	hash := tokenHash(token)

	u.mu.Lock()
	account, known := u.accounts[hash]
	if !known {
		account = "unknown"
	}
	reply := Reply{}
	if queue := u.scripts[account]; len(queue) > 0 {
		reply = queue[0]
		u.scripts[account] = queue[1:]
	}
	call, _ := req.Context().Value(callKey{}).(string)
	attempt := &Attempt{Seq: len(u.attempts) + 1, Call: call, Account: account, TokenHash: hash, StartedAt: time.Now(), RateLimitHeaders: rateLimitHeaders(reply.Header)}
	u.attempts = append(u.attempts, attempt)
	u.mu.Unlock()

	if !known {
		reply = Reply{Status: http.StatusUnauthorized, Body: anthropicError("authentication_error", "unknown token")}
	}
	if reply.Status != 0 && reply.Status != http.StatusOK {
		u.finish(attempt, reply.Status, "status", 0, "")
		return errorResponse(req, reply), nil
	}
	return u.stream(req, attempt, reply), nil
}

func rateLimitHeaders(h map[string]string) map[string]string {
	out := map[string]string{}
	for name, value := range h {
		canonical := http.CanonicalHeaderKey(name)
		if strings.HasPrefix(canonical, "Anthropic-Ratelimit-") || canonical == "Retry-After" {
			out[canonical] = value
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

func (u *Upstream) finish(a *Attempt, status int, outcome string, events int, canceledBy string) {
	u.mu.Lock()
	a.Status = status
	a.Outcome = outcome
	a.Events = events
	a.CanceledBy = canceledBy
	a.EndedAt = time.Now()
	u.mu.Unlock()
}

func errorResponse(req *http.Request, reply Reply) *http.Response {
	header := http.Header{"Content-Type": {"application/json"}}
	for k, v := range reply.Header {
		header.Set(k, v)
	}
	body := reply.Body
	if body == "" {
		body = anthropicError("api_error", http.StatusText(reply.Status))
	}
	return &http.Response{
		StatusCode:    reply.Status,
		Status:        fmt.Sprintf("%d %s", reply.Status, http.StatusText(reply.Status)),
		Header:        header,
		Body:          io.NopCloser(strings.NewReader(body)),
		ContentLength: int64(len(body)),
		Request:       req,
		ProtoMajor:    1,
		ProtoMinor:    1,
	}
}

func anthropicError(kind, message string) string {
	return fmt.Sprintf(`{"type":"error","error":{"type":%q,"message":%q}}`, kind, message)
}

func (u *Upstream) stream(req *http.Request, attempt *Attempt, reply Reply) *http.Response {
	pr, pw := io.Pipe()
	header := http.Header{"Content-Type": {"text/event-stream"}}
	for k, v := range reply.Header {
		header.Set(k, v)
	}
	events := successEvents(attempt.Account)
	go func() {
		ctx := req.Context()
		sent := 0
		write := func(ev string) bool {
			select {
			case <-ctx.Done():
				return false
			default:
			}
			if _, err := io.WriteString(pw, ev); err != nil {
				return false
			}
			sent++
			return true
		}
		limit := len(events)
		if reply.Fail != "" || reply.Hold {
			limit = reply.PartialEvents
		}
		for i := 0; i < limit && i < len(events); i++ {
			if !write(events[i]) {
				u.finish(attempt, http.StatusOK, "canceled", sent, "request context")
				_ = pw.CloseWithError(errRequestEnded)
				return
			}
		}
		if reply.Hold {
			<-ctx.Done()
			u.finish(attempt, http.StatusOK, "canceled", sent, "request context")
			_ = pw.CloseWithError(errRequestEnded)
			return
		}
		switch reply.Fail {
		case "drop":
			u.finish(attempt, http.StatusOK, "dropped", sent, "")
			_ = pw.CloseWithError(io.ErrUnexpectedEOF)
		case "overloaded":
			write("event: error\ndata: " + anthropicError("overloaded_error", "Overloaded") + "\n\n")
			u.finish(attempt, http.StatusOK, "in_stream_error", sent, "")
			_ = pw.Close()
		default:
			u.finish(attempt, http.StatusOK, "completed", sent, "")
			_ = pw.Close()
		}
	}()
	return &http.Response{
		StatusCode:    http.StatusOK,
		Status:        "200 OK",
		Header:        header,
		Body:          pr,
		ContentLength: -1,
		Request:       req,
		ProtoMajor:    1,
		ProtoMinor:    1,
	}
}

var errRequestEnded = errors.New("probe upstream: request context ended")

func successEvents(account string) []string {
	return []string{
		"event: message_start\ndata: {\"type\":\"message_start\",\"message\":{\"id\":\"msg_probe\",\"type\":\"message\",\"role\":\"assistant\",\"model\":\"claude-sonnet-4-5-20250929\",\"content\":[],\"stop_reason\":null,\"stop_sequence\":null,\"usage\":{\"input_tokens\":10,\"output_tokens\":1,\"cache_creation_input_tokens\":0,\"cache_read_input_tokens\":0}}}\n\n",
		"event: content_block_start\ndata: {\"type\":\"content_block_start\",\"index\":0,\"content_block\":{\"type\":\"text\",\"text\":\"\"}}\n\n",
		"event: content_block_delta\ndata: {\"type\":\"content_block_delta\",\"index\":0,\"delta\":{\"type\":\"text_delta\",\"text\":\"served-by:" + account + "\"}}\n\n",
		"event: content_block_stop\ndata: {\"type\":\"content_block_stop\",\"index\":0}\n\n",
		"event: message_delta\ndata: {\"type\":\"message_delta\",\"delta\":{\"stop_reason\":\"end_turn\",\"stop_sequence\":null},\"usage\":{\"output_tokens\":2}}\n\n",
		"event: message_stop\ndata: {\"type\":\"message_stop\"}\n\n",
	}
}
