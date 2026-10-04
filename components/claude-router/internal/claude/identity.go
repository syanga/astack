package claude

import (
	"encoding/json"
	"errors"
	"net/http"
	"strings"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

const (
	headerSessionID   = "X-Claude-Code-Session-Id"
	headerAgentID     = "X-Claude-Code-Agent-Id"
	headerParentAgent = "X-Claude-Code-Parent-Agent-Id"
)

// Identity is what the router binds a request on, read from the raw client
// request before the SDK rewrites any credential-specific field.
type Identity struct {
	Conversation router.ConversationID
	Agent        string
	ParentAgent  string
	Model        string
	Stream       bool
}

// errIdentity is a request the router refuses to place: it answers 400
// invalid_request_error with x-should-retry: false.
type errIdentity struct{ msg string }

func (e errIdentity) Error() string { return e.msg }

// ResolveIdentity reads the conversation key from x-claude-code-session-id
// and cross-checks it against metadata.user_id.session_id in the body. A
// fork arrives with a new session ID and is a new conversation. The key
// never includes metadata.user_id.device_id or account_uuid, which the SDK
// rewrites per credential.
func ResolveIdentity(h http.Header, body []byte) (Identity, error) {
	var shape struct {
		Model    string `json:"model"`
		Stream   bool   `json:"stream"`
		Metadata struct {
			UserID string `json:"user_id"`
		} `json:"metadata"`
	}
	if err := json.Unmarshal(body, &shape); err != nil {
		return Identity{}, errIdentity{"claude-router: request body is not a JSON object"}
	}
	session := strings.TrimSpace(h.Get(headerSessionID))
	if session == "" {
		return Identity{}, errIdentity{"claude-router: missing x-claude-code-session-id header; the router binds conversations on it and does not place requests without it"}
	}
	meta, err := metadataSession(shape.Metadata.UserID)
	if err != nil {
		return Identity{}, errIdentity{"claude-router: metadata.user_id carries no session_id to cross-check x-claude-code-session-id"}
	}
	if meta != session {
		return Identity{}, errIdentity{"claude-router: x-claude-code-session-id disagrees with metadata.user_id.session_id"}
	}
	if strings.TrimSpace(shape.Model) == "" {
		return Identity{}, errIdentity{"claude-router: request names no model"}
	}
	return Identity{
		Conversation: router.ConversationID(session),
		Agent:        strings.TrimSpace(h.Get(headerAgentID)),
		ParentAgent:  strings.TrimSpace(h.Get(headerParentAgent)),
		Model:        shape.Model,
		Stream:       shape.Stream,
	}, nil
}

// metadataSession extracts session_id from metadata.user_id. Current clients
// send a JSON object encoded as a string; older clients send
// user_<hash>_account_<uuid>_session_<uuid>.
func metadataSession(userID string) (string, error) {
	userID = strings.TrimSpace(userID)
	if strings.HasPrefix(userID, "{") {
		var fields struct {
			SessionID string `json:"session_id"`
		}
		if err := json.Unmarshal([]byte(userID), &fields); err != nil || fields.SessionID == "" {
			return "", errors.New("no session_id")
		}
		return fields.SessionID, nil
	}
	if i := strings.LastIndex(userID, "_session_"); i >= 0 && i+len("_session_") < len(userID) {
		return userID[i+len("_session_"):], nil
	}
	return "", errors.New("no session_id")
}
