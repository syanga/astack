package main

import (
	"fmt"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// Script is a fixture of routing steps with literal expected outcomes. The
// expectations were derived by hand from the placement rule and are not
// computed by the policy.
type Script struct {
	Name        string           `json:"name"`
	Description string           `json:"description"`
	Start       time.Time        `json:"start"`
	Accounts    []router.Account `json:"accounts"`
	Steps       []Step           `json:"steps"`
}

// Step is one action at AtMinutes after Start. Exactly one action is set.
type Step struct {
	AtMinutes float64            `json:"at_minutes"`
	Route     *router.Request    `json:"route,omitempty"`
	Commit    *ScriptCommit      `json:"commit,omitempty"`
	Observe   *ScriptObservation `json:"observe,omitempty"`
	Report    *ScriptReport      `json:"report,omitempty"`
	Move      *ScriptMove        `json:"move,omitempty"`
	Relogin   router.AccountID   `json:"relogin,omitempty"`
	Served    *ScriptCommit      `json:"served,omitempty"`
	Overage   *ScriptOverage     `json:"overage,omitempty"`
	Restart   bool               `json:"restart,omitempty"`
	// Expect maps a rule name, or "any", to the literal outcome of Route.
	Expect map[string]Expected `json:"expect,omitempty"`
}

// ScriptCommit records an assignment directly, to set up workload.
type ScriptCommit struct {
	Conversation router.ConversationID `json:"conversation"`
	Account      router.AccountID      `json:"account"`
}

// ScriptWindow is a quota window with its reset relative to the step.
type ScriptWindow struct {
	Kind           router.WindowKind `json:"kind"`
	Models         []string          `json:"models,omitempty"`
	Utilization    *float64          `json:"utilization,omitempty"`
	Rejected       bool              `json:"rejected,omitempty"`
	ResetInMinutes *float64          `json:"reset_in_minutes,omitempty"`
}

// ScriptObservation is an observation taken AgeMinutes before the step.
type ScriptObservation struct {
	Account    router.AccountID `json:"account"`
	AgeMinutes float64          `json:"age_minutes,omitempty"`
	Windows    []ScriptWindow   `json:"windows"`
}

// ScriptReport is a classified failure before output on an attempt that
// started at the step. EvidenceAgeMinutes dates its quota evidence before
// the attempt, as a stale SDK snapshot would be.
type ScriptReport struct {
	Account            router.AccountID `json:"account"`
	Model              string           `json:"model"`
	Class              router.Class     `json:"class"`
	Windows            []ScriptWindow   `json:"windows,omitempty"`
	EvidenceAgeMinutes float64          `json:"evidence_age_minutes,omitempty"`
}

// ScriptOverage records a paid-overflow check. An empty Account applies it
// to every account.
type ScriptOverage struct {
	Account router.AccountID `json:"account,omitempty"`
	State   router.Overage   `json:"state"`
}

// ScriptMove is a manual override.
type ScriptMove struct {
	Conversation router.ConversationID `json:"conversation"`
	To           router.AccountID      `json:"to"`
}

// Expected lists the decision fields a step checks. Empty fields are not
// checked.
type Expected struct {
	Kind         router.Kind      `json:"kind,omitempty"`
	Account      router.AccountID `json:"account,omitempty"`
	From         router.AccountID `json:"from,omitempty"`
	Reason       router.Reason    `json:"reason,omitempty"`
	UntilMinutes *float64         `json:"until_minutes,omitempty"`
	ResetKnown   *bool            `json:"reset_known,omitempty"`
	Recheck      *bool            `json:"recheck_overage,omitempty"`
	BoundTo      router.AccountID `json:"bound_to,omitempty"`
}

// StepResult is one checked step.
type StepResult struct {
	Step     int              `json:"step"`
	At       time.Time        `json:"at"`
	Request  *router.Request  `json:"request"`
	Expected Expected         `json:"expected"`
	Got      router.Decision  `json:"got"`
	BoundTo  router.AccountID `json:"bound_to,omitempty"`
	Pass     bool             `json:"pass"`
}

// ScriptResult is a script run under one rule.
type ScriptResult struct {
	Script string        `json:"script"`
	Rule   router.Rule   `json:"rule"`
	Config router.Config `json:"config"`
	Checks []StepResult  `json:"checks"`
	Pass   bool          `json:"pass"`
}

func windows(at time.Time, ws []ScriptWindow) []router.Window {
	var out []router.Window
	for _, w := range ws {
		rw := router.Window{Kind: w.Kind, Models: w.Models, Utilization: w.Utilization, Rejected: w.Rejected}
		if w.ResetInMinutes != nil {
			rw.ResetsAt = at.Add(minutes(*w.ResetInMinutes))
		}
		out = append(out, rw)
	}
	return out
}

// RunScript runs a script through the production router under cfg with
// durable state in dir.
func RunScript(sc Script, cfg router.Config, dir string) (ScriptResult, error) {
	res := ScriptResult{Script: sc.Name, Rule: cfg.Rule, Config: cfg, Pass: true}
	open := func() (*router.Store, *router.Router, error) {
		st, err := router.OpenStore(dir)
		if err != nil {
			return nil, nil, err
		}
		r, err := router.New(cfg, sc.Accounts, st)
		if err != nil {
			st.Close()
			return nil, nil, err
		}
		return st, r, nil
	}
	st, r, err := open()
	if err != nil {
		return res, err
	}
	defer func() { st.Close() }()
	for i, step := range sc.Steps {
		at := sc.Start.Add(minutes(step.AtMinutes))
		switch {
		case step.Route != nil:
			d, err := r.Route(at, *step.Route)
			if err != nil {
				return res, fmt.Errorf("step %d: %w", i, err)
			}
			want, ok := step.Expect[string(cfg.Rule)]
			if !ok {
				want, ok = step.Expect["any"]
			}
			if !ok {
				continue
			}
			bnd, _, err := r.Lookup(step.Route.Conversation)
			if err != nil {
				return res, fmt.Errorf("step %d: %w", i, err)
			}
			check := StepResult{Step: i, At: at, Request: step.Route, Expected: want, Got: d, BoundTo: bnd.Account}
			check.Pass = matches(want, d, bnd.Account, sc.Start)
			res.Pass = res.Pass && check.Pass
			res.Checks = append(res.Checks, check)
		case step.Commit != nil:
			if _, err := r.CommitAssignment(at, step.Commit.Conversation, step.Commit.Account, router.ReasonManual); err != nil {
				return res, fmt.Errorf("step %d: %w", i, err)
			}
		case step.Observe != nil:
			obsAt := at.Add(-minutes(step.Observe.AgeMinutes))
			r.Observe(step.Observe.Account, obsAt, router.Observation{
				At:      at.Add(-minutes(step.Observe.AgeMinutes)),
				Windows: windows(at, step.Observe.Windows),
			})
		case step.Report != nil:
			r.Report(router.Failure{
				Account: step.Report.Account, Model: step.Report.Model, Class: step.Report.Class, AttemptStart: at,
				Observation: router.Observation{At: at.Add(-minutes(step.Report.EvidenceAgeMinutes)), Windows: windows(at, step.Report.Windows)},
			})
		case step.Move != nil:
			if err := r.Move(at, step.Move.Conversation, step.Move.To); err != nil {
				return res, fmt.Errorf("step %d: %w", i, err)
			}
		case step.Overage != nil:
			for _, a := range sc.Accounts {
				if step.Overage.Account == "" || step.Overage.Account == a.ID {
					r.ObserveOverage(a.ID, step.Overage.State, at)
				}
			}
		case step.Served != nil:
			if err := r.Served(at, step.Served.Conversation, step.Served.Account); err != nil {
				return res, fmt.Errorf("step %d: %w", i, err)
			}
		case step.Relogin != "":
			if err := r.Relogin(step.Relogin); err != nil {
				return res, fmt.Errorf("step %d: %w", i, err)
			}
		case step.Restart:
			if err := st.Close(); err != nil {
				return res, err
			}
			if st, r, err = open(); err != nil {
				return res, fmt.Errorf("step %d: reopen: %w", i, err)
			}
		default:
			return res, fmt.Errorf("step %d has no action", i)
		}
	}
	return res, nil
}

func matches(want Expected, got router.Decision, bound router.AccountID, start time.Time) bool {
	switch {
	case want.Kind != "" && want.Kind != got.Kind,
		want.Account != "" && want.Account != got.Account,
		want.From != "" && want.From != got.From,
		want.Reason != "" && want.Reason != got.Reason,
		want.ResetKnown != nil && *want.ResetKnown != got.ResetKnown,
		want.Recheck != nil && *want.Recheck != got.RecheckOverage,
		want.BoundTo != "" && want.BoundTo != bound:
		return false
	case want.UntilMinutes != nil:
		return got.Until.Equal(start.Add(minutes(*want.UntilMinutes)))
	}
	return true
}
