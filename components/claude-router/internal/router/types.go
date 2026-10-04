package router

import (
	"strings"
	"time"
)

// AccountID names one enrolled subscription account.
type AccountID string

// ConversationID is the client's x-claude-code-session-id. Subagents send
// their parent's value, so they share the parent's assignment.
type ConversationID string

// Account is one enrolled subscription account. Capacity is its relative
// allowance, such as 1 for Pro and 5 for Max 5x.
type Account struct {
	ID       AccountID `json:"id"`
	Capacity float64   `json:"capacity"`
}

// WindowKind names a quota window reported by the provider.
type WindowKind string

const (
	FiveHour WindowKind = "five_hour"
	Weekly   WindowKind = "weekly"
	// Unspecified marks a rejection whose window the provider did not name,
	// such as a credential-scoped 429 without a rejected window header.
	Unspecified WindowKind = "unspecified"
)

// Period returns the window's length, or 0 when it is not known.
func (k WindowKind) Period() time.Duration {
	switch k {
	case FiveHour:
		return 5 * time.Hour
	case Weekly:
		return 7 * 24 * time.Hour
	}
	return 0
}

// Window is one quota window in an observation. Models lists model-name
// prefixes the window constrains; an empty list constrains every model.
// A zero ResetsAt means the reset is unknown.
type Window struct {
	Kind        WindowKind `json:"kind"`
	Models      []string   `json:"models,omitempty"`
	Utilization *float64   `json:"utilization,omitempty"`
	Rejected    bool       `json:"rejected,omitempty"`
	ResetsAt    time.Time  `json:"resets_at,omitzero"`
}

func (w Window) appliesTo(model string) bool {
	if len(w.Models) == 0 {
		return true
	}
	for _, prefix := range w.Models {
		if strings.HasPrefix(model, prefix) {
			return true
		}
	}
	return false
}

// Observation is an account's quota state as last reported, and when.
type Observation struct {
	At      time.Time `json:"at"`
	Windows []Window  `json:"windows"`
}

// Class is the recovery category of a failure before output, as classified
// from the SDK's error and quota snapshot.
type Class string

const (
	ClassNone       Class = ""
	ClassTransient  Class = "transient"
	ClassThrottle   Class = "throttle"
	ClassExhausted  Class = "exhausted"
	ClassModelLimit Class = "model_limit"
	ClassAuth       Class = "auth"
)

// Request is one inference attempt. Attempt counts from 1. LastFailure is the
// class of the previous attempt of the same request, or ClassNone.
type Request struct {
	Conversation ConversationID `json:"conversation"`
	Agent        string         `json:"agent,omitempty"`
	ParentAgent  string         `json:"parent_agent,omitempty"`
	Model        string         `json:"model"`
	Attempt      int            `json:"attempt,omitempty"`
	LastFailure  Class          `json:"last_failure,omitempty"`
}

// Kind is the routing outcome for one request.
type Kind string

const (
	// Dispatch sends the request on the conversation's existing account.
	Dispatch Kind = "dispatch"
	// Place assigns a new conversation. Commit it before dispatch.
	Place Kind = "place"
	// Migrate moves the conversation after confirmed exhaustion. Commit it
	// before dispatch.
	Migrate Kind = "migrate"
	// Retry resends after a transient failure on the same account.
	Retry Kind = "retry"
	// Wait answers locally: no eligible account can serve the model until Until.
	Wait Kind = "wait"
	// Reauth reports that the assigned account needs a browser login. The
	// assignment stays until relogin or an explicit move.
	Reauth Kind = "reauth"
	// Fail reports a failure to the client after the retry budget is spent.
	Fail Kind = "fail"
	// Reject refuses a request without conversation identity.
	Reject Kind = "reject"
	// Unavailable reports that no enrolled account is logged in.
	Unavailable Kind = "unavailable"
)

// Reason explains a Decision.
type Reason string

const (
	ReasonAssigned        Reason = "assigned"
	ReasonCapacity        Reason = "capacity"
	ReasonResetPreference Reason = "reset_preference"
	ReasonConcurrentFirst Reason = "concurrent_first_request"
	ReasonExhausted       Reason = "exhausted"
	ReasonManual          Reason = "manual"
	ReasonTransient       Reason = "transient_retry"
	ReasonRetryBudget     Reason = "retry_budget_spent"
	ReasonNeedsLogin      Reason = "needs_login"
	ReasonNotEnrolled     Reason = "not_enrolled"
	ReasonMissingIdentity Reason = "missing_identity"
	ReasonAllBlocked      Reason = "all_blocked"
	ReasonNoLogin         Reason = "no_logged_in_account"
)

// Freshness describes the observation behind a placement.
type Freshness string

const (
	Fresh  Freshness = "fresh"
	Stale  Freshness = "stale"
	Absent Freshness = "absent"
)

// Decision is the policy outcome for one request. For Wait, Until is the
// earliest usable reset and ResetKnown is false when Until is only a recheck
// time for a rejection without a reported reset.
type Decision struct {
	Kind        Kind      `json:"kind"`
	Account     AccountID `json:"account,omitempty"`
	From        AccountID `json:"from,omitempty"`
	Reason      Reason    `json:"reason"`
	Observation Freshness `json:"observation,omitempty"`
	Until       time.Time `json:"until,omitzero"`
	ResetKnown  bool      `json:"reset_known,omitempty"`
}

// Rule selects the placement rule.
type Rule string

const (
	// CapacityOnly places by capacity and assigned workload alone.
	CapacityOnly Rule = "capacity_only"
	// ResetAware also prefers fresh unused allowance approaching reset.
	ResetAware Rule = "reset_aware"
)

// Config holds the policy parameters. DefaultConfig gives the values chosen
// from the simulator comparison recorded in CONTRACT.md.
type Config struct {
	Rule Rule `json:"rule"`
	// ResetBias scales an account's capacity by 1 + ResetBias*slack, where
	// slack is the largest fraction of a fresh window's allowance that is
	// behind an even pace.
	ResetBias float64 `json:"reset_bias"`
	// FreshFor is the age after which an observation no longer informs reset
	// preference. Known rejections apply at any age.
	FreshFor time.Duration `json:"fresh_for"`
	// ActiveFor is how long after its last request a conversation counts
	// toward its account's workload.
	ActiveFor time.Duration `json:"active_for"`
	// UnknownResetRecheck is how long a rejection without a reported reset
	// blocks its account before a request may probe it again.
	UnknownResetRecheck time.Duration `json:"unknown_reset_recheck"`
	// MaxAttempts bounds attempts of one request on its account after
	// transient failures and generic throttling.
	MaxAttempts int `json:"max_attempts"`
}

// DefaultConfig returns the production policy defaults.
func DefaultConfig() Config {
	return Config{
		Rule:                ResetAware,
		ResetBias:           2,
		FreshFor:            15 * time.Minute,
		ActiveFor:           time.Hour,
		UnknownResetRecheck: 5 * time.Minute,
		MaxAttempts:         3,
	}
}
