package claude

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"io"
	"os"
	"sync"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// Event is one sanitized service event. It carries account labels, a hash
// of the conversation key, statuses, classes, decisions, counters, and
// timing. It never carries a token, a prompt, a header value, or a body.
type Event struct {
	At           time.Time        `json:"at"`
	Kind         string           `json:"kind"`
	Conversation string           `json:"conversation,omitempty"`
	Account      router.AccountID `json:"account,omitempty"`
	From         router.AccountID `json:"from,omitempty"`
	Decision     router.Kind      `json:"decision,omitempty"`
	Reason       string           `json:"reason,omitempty"`
	Class        router.Class     `json:"class,omitempty"`
	Status       int              `json:"status,omitempty"`
	Attempt      int              `json:"attempt,omitempty"`
	Attempts     int              `json:"upstream_attempts,omitempty"`
	Stream       *bool            `json:"stream,omitempty"`
	Outcome      string           `json:"outcome,omitempty"`
	Overage      router.Overage   `json:"overage,omitempty"`
	Usage        *Usage           `json:"usage,omitempty"`
	DurationMS   *int64           `json:"duration_ms,omitempty"`
	Detail       string           `json:"detail,omitempty"`
}

// Usage holds the token counters a response reported. A nil field is a
// counter the upstream did not supply, which is unknown rather than zero.
type Usage struct {
	InputTokens              *int64 `json:"input_tokens"`
	OutputTokens             *int64 `json:"output_tokens"`
	CacheCreationInputTokens *int64 `json:"cache_creation_input_tokens"`
	CacheReadInputTokens     *int64 `json:"cache_read_input_tokens"`
}

func (u *Usage) merge(other Usage) {
	for _, p := range []struct{ dst **int64; src *int64 }{
		{&u.InputTokens, other.InputTokens},
		{&u.OutputTokens, other.OutputTokens},
		{&u.CacheCreationInputTokens, other.CacheCreationInputTokens},
		{&u.CacheReadInputTokens, other.CacheReadInputTokens},
	} {
		if p.src != nil {
			v := *p.src
			*p.dst = &v
		}
	}
}

func (u Usage) known() bool {
	return u.InputTokens != nil || u.OutputTokens != nil || u.CacheCreationInputTokens != nil || u.CacheReadInputTokens != nil
}

// conversationHash is the sanitized form of a conversation key in events.
func conversationHash(c router.ConversationID) string {
	if c == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(c))
	return hex.EncodeToString(sum[:])[:12]
}

type eventLog struct {
	mu  sync.Mutex
	out io.WriteCloser
	enc *json.Encoder
}

func openEventLog(path string) (*eventLog, error) {
	f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_APPEND, 0o600)
	if err != nil {
		return nil, err
	}
	return &eventLog{out: f, enc: json.NewEncoder(f)}, nil
}

func (l *eventLog) emit(e Event) {
	if e.At.IsZero() {
		e.At = time.Now()
	}
	l.mu.Lock()
	defer l.mu.Unlock()
	_ = l.enc.Encode(e)
}

func (l *eventLog) close() error {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.out.Close()
}
