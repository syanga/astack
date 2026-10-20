package claude

import (
	"context"
	"encoding/json"
	"slices"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// resetsURL is the usage endpoint with the limit-reset grants that Claude
// Code reads for its reset offer. The grants block and its query are
// undocumented and may change.
const resetsURL = usageURL + "?cedar_ember=1&skip_spend=1"

const resetsEvery = 30 * time.Minute

// UsageWindow is one limit window as the usage endpoint reports it. Model
// is the endpoint's label for a model-scoped weekly window, such as "Fable",
// and is empty for a window that counts every model. Used is the fraction
// of the window used, or nil when the endpoint reported none.
type UsageWindow struct {
	Kind     router.WindowKind `json:"kind"`
	Model    string            `json:"model,omitempty"`
	Used     *float64          `json:"used,omitempty"`
	ResetsAt time.Time         `json:"resets_at,omitzero"`
}

// UsageReading holds an account's limit windows from its last usage
// response that decoded. Status shows them; routing uses response headers only.
type UsageReading struct {
	At      time.Time     `json:"at"`
	Windows []UsageWindow `json:"windows"`
}

// ResetKind names the windows a limit reset clears.
type ResetKind string

const (
	ResetSession ResetKind = "session"
	ResetWeekly  ResetKind = "weekly"
	// ResetFull clears the 5-hour and weekly windows.
	ResetFull ResetKind = "full"
)

var ResetKinds = []ResetKind{ResetSession, ResetWeekly, ResetFull}

// ResetGrant is one grant of limit resets the account can redeem until
// EndsAt, or with no end when EndsAt is zero.
type ResetGrant struct {
	Type   ResetKind `json:"type"`
	Left   int       `json:"left"`
	EndsAt time.Time `json:"ends_at,omitzero"`
}

func (g ResetGrant) OpenAt(now time.Time) bool {
	return g.EndsAt.IsZero() || g.EndsAt.After(now)
}

// ResetsReading holds an account's reset grants from its last successful
// resets read.
type ResetsReading struct {
	At     time.Time    `json:"at"`
	Grants []ResetGrant `json:"grants"`
}

type usageBody struct {
	ExtraUsage *struct {
		IsEnabled *bool `json:"is_enabled"`
	} `json:"extra_usage"`
	FiveHour    json.RawMessage `json:"five_hour"`
	SevenDay    json.RawMessage `json:"seven_day"`
	ModelScoped json.RawMessage `json:"model_scoped"`
	CedarEmber  json.RawMessage `json:"cedar_ember"`
}

func (b usageBody) overage() (router.Overage, error) {
	if b.ExtraUsage == nil || b.ExtraUsage.IsEnabled == nil {
		return "", errNoExtraUsage
	}
	if *b.ExtraUsage.IsEnabled {
		return router.OverageEnabled, nil
	}
	return router.OverageDisabled, nil
}

type usageWindowBody struct {
	Utilization *float64 `json:"utilization"`
	ResetsAt    *string  `json:"resets_at"`
}

func (w usageWindowBody) window(kind router.WindowKind, model string) UsageWindow {
	out := UsageWindow{Kind: kind, Model: model}
	if w.Utilization != nil {
		used := *w.Utilization / 100
		out.Used = &used
	}
	if w.ResetsAt != nil {
		if t, err := time.Parse(time.RFC3339Nano, *w.ResetsAt); err == nil {
			out.ResetsAt = t
		}
	}
	return out
}

func (b usageBody) windows() []UsageWindow {
	var out []UsageWindow
	for _, w := range []struct {
		raw  json.RawMessage
		kind router.WindowKind
	}{{b.FiveHour, router.FiveHour}, {b.SevenDay, router.Weekly}} {
		var body *usageWindowBody
		if json.Unmarshal(w.raw, &body) == nil && body != nil {
			out = append(out, body.window(w.kind, ""))
		}
	}
	var scoped []struct {
		DisplayName string `json:"display_name"`
		usageWindowBody
	}
	if json.Unmarshal(b.ModelScoped, &scoped) == nil {
		for _, s := range scoped {
			if s.DisplayName != "" {
				out = append(out, s.window(router.Weekly, s.DisplayName))
			}
		}
	}
	return out
}

func (b usageBody) grants(now time.Time) ([]ResetGrant, bool) {
	var block *struct {
		Grants []struct {
			ResetsLeft int       `json:"resets_left"`
			Type       ResetKind `json:"type"`
			Clears     []string  `json:"clears"`
			EndsAt     *string   `json:"ends_at"`
		} `json:"grants"`
	}
	if json.Unmarshal(b.CedarEmber, &block) != nil || block == nil {
		return nil, false
	}
	out := []ResetGrant{}
	for _, g := range block.Grants {
		if g.ResetsLeft <= 0 {
			continue
		}
		grant := ResetGrant{Type: g.Type, Left: g.ResetsLeft}
		if grant.Type == "" {
			grant.Type = clearedType(g.Clears)
		}
		if g.EndsAt != nil {
			if t, err := time.Parse(time.RFC3339Nano, *g.EndsAt); err == nil {
				grant.EndsAt = t
			}
		}
		if slices.Contains(ResetKinds, grant.Type) && grant.OpenAt(now) {
			out = append(out, grant)
		}
	}
	return out, true
}

func clearedType(clears []string) ResetKind {
	session, weekly := slices.Contains(clears, "five_hour"), slices.Contains(clears, "seven_day")
	switch {
	case session && weekly:
		return ResetFull
	case session:
		return ResetSession
	case weekly:
		return ResetWeekly
	}
	return ""
}

func (o *overageReader) noteUsage(account router.AccountID, windows []UsageWindow, at time.Time) {
	o.mu.Lock()
	defer o.mu.Unlock()
	if len(windows) == 0 {
		delete(o.usage, account)
		return
	}
	o.usage[account] = UsageReading{At: at, Windows: windows}
}

// readResets skips an access token the SDK is due to replace, because the
// SDK revokes the old token when it refreshes.
func (o *overageReader) readResets(ctx context.Context, account router.AccountID) {
	auth, ok := o.s.core.GetByID(o.s.authIDs[account])
	if !ok || auth == nil || refreshDue(auth, time.Now()) {
		return
	}
	body, err := o.get(ctx, account, auth, resetsURL)
	if err != nil {
		return
	}
	now := o.s.now()
	grants, ok := body.grants(now)
	if !ok {
		return
	}
	o.mu.Lock()
	defer o.mu.Unlock()
	o.resets[account] = ResetsReading{At: now, Grants: grants}
}

func (o *overageReader) usageSnapshot() (map[router.AccountID]UsageReading, map[router.AccountID]ResetsReading) {
	o.mu.Lock()
	defer o.mu.Unlock()
	usage := make(map[router.AccountID]UsageReading, len(o.usage))
	for k, v := range o.usage {
		usage[k] = v
	}
	resets := make(map[router.AccountID]ResetsReading, len(o.resets))
	for k, v := range o.resets {
		resets[k] = v
	}
	return usage, resets
}
