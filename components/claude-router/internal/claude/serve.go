package claude

import (
	"context"
	"crypto/subtle"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/router-for-me/CLIProxyAPI/v8/sdk/api/handlers"
	claudehandlers "github.com/router-for-me/CLIProxyAPI/v8/sdk/api/handlers/claude"
	cliproxyexecutor "github.com/router-for-me/CLIProxyAPI/v8/sdk/cliproxy/executor"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

const (
	maxRequestBody         = 64 << 20
	maxRoutingSteps        = 8
	unknownResetRetryAfter = 5 * time.Minute
)

func (s *Service) handler() http.Handler {
	e := gin.New()
	e.Use(gin.CustomRecoveryWithWriter(io.Discard, func(c *gin.Context, _ any) {
		writeError(c, http.StatusInternalServerError, "api_error", "claude-router: internal error")
	}))
	e.Use(s.authenticate)
	e.POST("/v1/messages", s.messages)
	e.GET("/claude-router/status", s.status)
	e.NoRoute(func(c *gin.Context) {
		writeError(c, http.StatusNotFound, "not_found_error", "claude-router serves POST /v1/messages only")
	})
	return e
}

// authenticate accepts the private client token as x-api-key or as a bearer
// token, then removes both headers so the SDK never sees the client token.
func (s *Service) authenticate(c *gin.Context) {
	got := c.GetHeader("X-Api-Key")
	if got == "" {
		if v, ok := strings.CutPrefix(c.GetHeader("Authorization"), "Bearer "); ok {
			got = strings.TrimSpace(v)
		}
	}
	if subtle.ConstantTimeCompare([]byte(got), []byte(s.token)) != 1 {
		s.events.emit(Event{Kind: "client_auth_rejected", Status: http.StatusUnauthorized})
		writeError(c, http.StatusUnauthorized, "authentication_error", "claude-router: invalid client token")
		c.Abort()
		return
	}
	c.Request.Header.Del("X-Api-Key")
	c.Request.Header.Del("Authorization")
	c.Next()
}

func writeError(c *gin.Context, status int, kind, message string) {
	c.Header("X-Should-Retry", "false")
	c.Header("Content-Type", "application/json")
	body, _ := json.Marshal(map[string]any{"type": "error", "error": map[string]string{"type": kind, "message": message}})
	c.Data(status, "application/json", body)
}

func errorType(status int) string {
	switch {
	case status == http.StatusTooManyRequests:
		return "rate_limit_error"
	case status == 529:
		return "overloaded_error"
	case status >= 500:
		return "api_error"
	case status == http.StatusNotFound:
		return "not_found_error"
	case status == http.StatusRequestEntityTooLarge:
		return "request_too_large"
	}
	return "invalid_request_error"
}

type outcome struct {
	Delivered  bool
	ClientGone bool
	Status     int
	Err        error
	Class      router.Class
	Message    string
	Header     http.Header
	AttemptAt  time.Time
	Attempts   int
	Result     string
	Usage      Usage
}

func (s *Service) messages(c *gin.Context) {
	start := s.now()
	body, err := io.ReadAll(io.LimitReader(c.Request.Body, maxRequestBody+1))
	if err != nil {
		writeError(c, http.StatusBadRequest, "invalid_request_error", "claude-router: could not read the request body")
		return
	}
	if len(body) > maxRequestBody {
		writeError(c, http.StatusRequestEntityTooLarge, "request_too_large", "claude-router: request body too large")
		return
	}
	id, err := ResolveIdentity(c.Request.Header, body)
	if err != nil {
		s.events.emit(Event{Kind: "request_rejected", Decision: router.Reject, Reason: string(router.ReasonMissingIdentity), Status: http.StatusBadRequest})
		writeError(c, http.StatusBadRequest, "invalid_request_error", err.Error())
		return
	}
	conv := conversationHash(id.Conversation)
	stream := id.Stream
	req := router.Request{Conversation: id.Conversation, Agent: id.Agent, ParentAgent: id.ParentAgent, Model: id.Model, Attempt: 1}
	call := fmt.Sprintf("r%d", s.seq.Add(1))
	upstream := 0
	rechecked := false
	finish := func(kind string, account router.AccountID, d router.Decision, status int, out *outcome) {
		dur := s.now().Sub(start).Milliseconds()
		e := Event{Kind: kind, Conversation: conv, Account: account, Decision: d.Kind, Reason: string(d.Reason), Status: status, Attempt: req.Attempt, Attempts: upstream, Stream: &stream, DurationMS: &dur}
		if out != nil {
			e.Outcome, e.Class = out.Result, out.Class
			if out.Usage.known() {
				u := out.Usage
				e.Usage = &u
			}
		}
		s.events.emit(e)
	}

	for step := 0; step < maxRoutingSteps; step++ {
		now := s.now()
		d := s.router.Decide(now, req)
		var account router.AccountID
		switch d.Kind {
		case router.Place, router.Dispatch, router.Retry:
			got, err := s.router.CommitAssignment(now, id.Conversation, d.Account, d.Reason)
			if err != nil {
				finish("request", d.Account, d, http.StatusServiceUnavailable, nil)
				writeError(c, http.StatusServiceUnavailable, "api_error", "claude-router: assignment journal unavailable; restart the router")
				return
			}
			account = got
		case router.Migrate:
			if d.Reason == router.ReasonExhausted {
				s.answerExhausted(c, now, d.From, id.Model)
				finish("request", d.From, d, http.StatusTooManyRequests, nil)
				return
			}
			got, err := s.router.CommitMigration(now, id.Conversation, d.From, d.Account, d.Reason)
			if err != nil {
				finish("request", d.From, d, http.StatusServiceUnavailable, nil)
				writeError(c, http.StatusServiceUnavailable, "api_error", "claude-router: assignment journal unavailable; restart the router")
				return
			}
			s.events.emit(Event{Kind: "migrated", Conversation: conv, Account: got, From: d.From, Reason: string(d.Reason)})
			account = got
		case router.Refuse:
			if d.RecheckOverage && !rechecked {
				rechecked = true
				s.recheckOverage(c.Request.Context(), d.Account)
				continue
			}
			finish("request", d.Account, d, http.StatusServiceUnavailable, nil)
			writeError(c, http.StatusServiceUnavailable, "api_error", refuseMessage(d))
			return
		case router.Wait:
			s.answerWait(c, now, d.Until, d.ResetKnown, "claude-router: no account can serve this model now")
			finish("request", "", d, http.StatusTooManyRequests, nil)
			return
		case router.Reauth, router.Unavailable:
			finish("request", d.Account, d, http.StatusServiceUnavailable, nil)
			writeError(c, http.StatusServiceUnavailable, "api_error", loginMessage(d))
			return
		case router.Reject:
			finish("request", "", d, http.StatusBadRequest, nil)
			writeError(c, http.StatusBadRequest, "invalid_request_error", "claude-router: request has no conversation identity")
			return
		case router.Fail:
			finish("request", d.Account, d, http.StatusServiceUnavailable, nil)
			writeError(c, http.StatusServiceUnavailable, "api_error", "claude-router: request failed after the router's retry budget")
			return
		}

		authID := s.authIDs[account]
		out := s.dispatch(c, fmt.Sprintf("%s-%d", call, step), authID, account, id, body)
		upstream += out.Attempts
		switch {
		case out.ClientGone:
			finish("request", account, d, 499, &out)
			return
		case out.Delivered:
			finish("request", account, d, http.StatusOK, &out)
			return
		}
		if out.Attempts == 0 {
			finish("request", account, d, http.StatusServiceUnavailable, &out)
			writeError(c, http.StatusServiceUnavailable, "api_error", "claude-router: the SDK refused the pinned account without an upstream attempt")
			return
		}
		class := s.router.Report(router.Failure{
			Account:      account,
			Model:        id.Model,
			Class:        out.Class,
			AttemptStart: out.AttemptAt,
			Observation:  exhaustionEvidence(out.Header, out.AttemptAt, out.Class),
		})
		s.events.emit(Event{Kind: "attempt_failed", Conversation: conv, Account: account, Class: class, Status: out.Status, Attempt: req.Attempt})
		if class == router.ClassRequestScoped {
			status := out.Status
			if status < 400 || status >= 500 {
				status = http.StatusBadGateway
			}
			finish("request", account, router.Decision{Kind: router.Fail, Reason: router.ReasonRequestScoped}, status, &out)
			writeError(c, status, errorType(status), upstreamMessage(out.Message))
			return
		}
		req.LastFailure = class
		req.Attempt++
		if class == router.ClassTransient || class == router.ClassThrottle {
			if !sleepCtx(c.Request.Context(), retryBackoff(req.Attempt)) {
				return
			}
		}
	}
	finish("request", "", router.Decision{Kind: router.Fail, Reason: router.ReasonRetryBudget}, http.StatusServiceUnavailable, nil)
	writeError(c, http.StatusServiceUnavailable, "api_error", "claude-router: request could not be routed")
}

func upstreamMessage(text string) string {
	var e struct {
		Error struct {
			Message string `json:"message"`
		} `json:"error"`
	}
	if json.Unmarshal([]byte(text), &e) == nil && e.Error.Message != "" {
		return e.Error.Message
	}
	return text
}

func retryBackoff(attempt int) time.Duration {
	d := 250 * time.Millisecond << max(0, attempt-2)
	return min(d, 2*time.Second)
}

func sleepCtx(ctx context.Context, d time.Duration) bool {
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func (s *Service) recheckOverage(ctx context.Context, account router.AccountID) {
	if account != "" {
		s.overage.recheck(ctx, account)
		return
	}
	for _, a := range s.cfg.Accounts {
		s.overage.recheck(ctx, a.ID)
	}
}

func refuseMessage(d router.Decision) string {
	switch d.Reason {
	case router.ReasonPaidUse:
		return fmt.Sprintf("claude-router: account %s reported paid use, and no other account can take this conversation; the router does not send it more requests", d.Account)
	case router.ReasonOverageEnabled:
		return fmt.Sprintf("claude-router: account %s has paid overflow enabled; the router serves included allowance only", d.Account)
	case router.ReasonNoVerified:
		return "claude-router: no account has a fresh reading that paid overflow is disabled"
	}
	return fmt.Sprintf("claude-router: account %s has no fresh reading that paid overflow is disabled (%s)", d.Account, d.Reason)
}

func loginMessage(d router.Decision) string {
	if d.Kind == router.Unavailable {
		return "claude-router: no enrolled account is logged in; run claude-router login"
	}
	return fmt.Sprintf("claude-router: account %s needs a new proxy login (claude-router login -account %s); the conversation stays on it", d.Account, d.Account)
}

// answerWait is the local 429 clients wait through: unified status rejected,
// the reset, and retry-after for the same delay. An unknown reset omits the
// reset header and sends a bounded retry-after. It never uses spend-cap
// error shapes.
func (s *Service) answerWait(c *gin.Context, now, until time.Time, known bool, prefix string) {
	c.Header("Anthropic-Ratelimit-Unified-Status", "rejected")
	delay := unknownResetRetryAfter
	msg := prefix + "; the reset time is unknown"
	if known && until.After(now) {
		delay = until.Sub(now)
		c.Header("Anthropic-Ratelimit-Unified-Reset", strconv.FormatInt(until.Unix(), 10))
		msg = fmt.Sprintf("%s; usable again at %s", prefix, until.UTC().Format(time.RFC3339))
	}
	secs := int64((delay + time.Second - 1) / time.Second)
	c.Header("Retry-After", strconv.FormatInt(secs, 10))
	body, _ := json.Marshal(map[string]any{"type": "error", "error": map[string]string{"type": "rate_limit_error", "message": msg}})
	c.Data(http.StatusTooManyRequests, "application/json", body)
}

func (s *Service) answerExhausted(c *gin.Context, now time.Time, account router.AccountID, model string) {
	until, known, _ := s.router.BlockedUntil(now, account, model)
	s.answerWait(c, now, until, known, fmt.Sprintf("claude-router: account %s is exhausted and this build does not move conversations", account))
}

func (s *Service) dispatch(c *gin.Context, call, authID string, account router.AccountID, id Identity, body []byte) outcome {
	api := claudehandlers.NewClaudeCodeAPIHandler(s.base)
	ctx, cancel := s.base.GetContextWithCancel(api, c, context.Background())
	ctx = handlers.WithPinnedAuthID(ctx, authID)
	ctx = withCall(ctx, call)
	defer s.transport.forget(call)
	var out outcome
	if id.Stream {
		out = s.relayStream(c, ctx, call, account, id, body)
	} else {
		out = s.relayMessage(c, ctx, call, account, id, body)
	}
	cancel(out.Err)
	attempts := s.transport.attempts(call)
	out.Attempts = len(attempts)
	if n := len(attempts); n > 0 && !out.Delivered && !out.ClientGone {
		last := attempts[n-1]
		out.AttemptAt = last.Start
		if out.Header == nil {
			out.Header = last.Header
		}
		if out.Class == "" {
			out.Class = classify(out.Status, out.Err, last.Header)
		}
	}
	return out
}

func failureOf(status int, err error) outcome {
	if status == 0 {
		status = http.StatusBadGateway
	}
	msg := http.StatusText(status)
	if err != nil {
		msg = err.Error()
	}
	var terminated *cliproxyexecutor.RequestTerminatedError
	if errors.As(err, &terminated) && len(terminated.Body) > 0 {
		msg = string(terminated.Body)
	}
	return outcome{Status: status, Err: err, Message: msg, Result: "failed_before_output"}
}

func (s *Service) relayMessage(c *gin.Context, ctx context.Context, _ string, account router.AccountID, id Identity, body []byte) outcome {
	resp, _, errMsg := s.base.ExecuteWithAuthManager(ctx, "claude", id.Model, body, "")
	if c.Request.Context().Err() != nil {
		return outcome{ClientGone: true, Result: "client_canceled"}
	}
	if errMsg != nil {
		return failureOf(errMsg.StatusCode, errMsg.Error)
	}
	out := outcome{Delivered: true, Result: "completed"}
	if u, ok := usageFrom("message", resp); ok {
		out.Usage = u
	}
	var shape struct {
		Content []json.RawMessage `json:"content"`
	}
	if json.Unmarshal(resp, &shape) == nil && len(shape.Content) > 0 {
		s.markServed(id.Conversation, account)
	}
	c.Data(http.StatusOK, "application/json", resp)
	return out
}

// relayStream holds the response until the first complete SSE event. An
// error event before any output is a failure the router may retry; any
// other first event commits the response. After that, the router relays
// every chunk, marks the assignment served at the first completed content
// block, and ends the stream gracefully whatever happens upstream.
func (s *Service) relayStream(c *gin.Context, ctx context.Context, _ string, account router.AccountID, id Identity, body []byte) outcome {
	data, _, errs := s.base.ExecuteStreamWithAuthManager(ctx, "claude", id.Model, body, "")
	var scan sseScanner
	var pending []byte
	committed, served, stopped := false, false, false
	out := outcome{}
	handle := func(events []sseEvent) {
		for _, ev := range events {
			switch ev.Name {
			case "message_start", "message_delta":
				if u, ok := usageFrom(ev.Name, ev.Data); ok {
					out.Usage.merge(u)
				}
			case "content_block_stop":
				if !served {
					served = true
					s.markServed(id.Conversation, account)
				}
			case "message_stop":
				stopped = true
			case "error":
				out.Result = "error_after_output"
			}
		}
	}
	commit := func() {
		committed = true
		out.Delivered = true
		c.Header("Content-Type", "text/event-stream")
		c.Header("Cache-Control", "no-cache")
		c.Status(http.StatusOK)
		_, _ = c.Writer.Write(pending)
		c.Writer.Flush()
		pending = nil
	}
	for data != nil || errs != nil {
		select {
		case <-c.Request.Context().Done():
			out.ClientGone, out.Delivered, out.Result = true, false, "client_canceled"
			return out
		case msg, ok := <-errs:
			if !ok {
				errs = nil
				continue
			}
			if !committed {
				f := failureOf(msg.StatusCode, msg.Error)
				return f
			}
			payload, _ := json.Marshal(map[string]any{"type": "error", "error": map[string]string{"type": errorType(msg.StatusCode), "message": "upstream stream failed"}})
			_, _ = fmt.Fprintf(c.Writer, "event: error\ndata: %s\n\n", payload)
			c.Writer.Flush()
			out.Result = "error_after_output"
			return out
		case chunk, ok := <-data:
			if !ok {
				data = nil
				continue
			}
			events := scan.feed(chunk)
			if !committed {
				pending = append(pending, chunk...)
				if len(events) == 0 {
					continue
				}
				if events[0].Name == "error" {
					kind := streamErrorType(events[0].Data)
					f := failureOf(529, errors.New("in-stream "+kind+" before output"))
					f.Class = classifyStreamError(kind)
					return f
				}
				commit()
			} else {
				_, _ = c.Writer.Write(chunk)
				c.Writer.Flush()
			}
			handle(events)
		}
	}
	if !committed {
		f := failureOf(http.StatusBadGateway, errors.New("upstream stream ended before any event"))
		f.Class = router.ClassTransient
		return f
	}
	if out.Result == "" {
		out.Result = "completed"
		if !stopped {
			out.Result = "incomplete"
		}
	}
	return out
}

func (s *Service) markServed(conv router.ConversationID, account router.AccountID) {
	if err := s.router.Served(s.now(), conv, account); err != nil {
		s.events.emit(Event{Kind: "served_mark_failed", Conversation: conversationHash(conv), Account: account})
		return
	}
	s.events.emit(Event{Kind: "served", Conversation: conversationHash(conv), Account: account})
}

// AccountStatus is one enrolled account in the status output.
type AccountStatus struct {
	ID         router.AccountID `json:"id"`
	Registered bool             `json:"registered"`
	Overage    overageState     `json:"overage"`
}

// Status is the status output. It holds no token, prompt, or header value.
type Status struct {
	Listen        string          `json:"listen"`
	Accounts      []AccountStatus `json:"accounts"`
	Conversations int             `json:"conversations"`
}

// Status returns the service status.
func (s *Service) Status() Status {
	ov := s.overage.snapshot()
	st := Status{Listen: s.addr, Conversations: len(s.store.Bindings())}
	for _, a := range s.cfg.Accounts {
		st.Accounts = append(st.Accounts, AccountStatus{ID: a.ID, Registered: s.authIDs[a.ID] != "", Overage: ov[a.ID]})
	}
	return st
}

func (s *Service) status(c *gin.Context) {
	c.JSON(http.StatusOK, s.Status())
}
