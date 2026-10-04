package router

import (
	"time"
)

type accountView struct {
	Account
	needsLogin  bool
	observation Observation
	rejections  []rejection
	allowedAt   map[string]time.Time
	overage     Overage
	overageAt   time.Time
}

type rejection struct {
	Window
	at time.Time
}

func (rj rejection) end(cfg Config) time.Time {
	if rj.ResetsAt.IsZero() {
		return rj.at.Add(cfg.UnknownResetRecheck)
	}
	return rj.ResetsAt
}

func overageBar(cfg Config, now time.Time, a accountView) Reason {
	switch {
	case a.overage == OveragePaidUse:
		return ReasonPaidUse
	case a.overage == OverageEnabled:
		return ReasonOverageEnabled
	case a.overage != OverageDisabled:
		return ReasonOverageUnknown
	case now.Sub(a.overageAt) > cfg.OverageFreshFor:
		return ReasonOverageStale
	}
	return ""
}

func recheck(bar Reason) bool {
	return bar == ReasonOverageUnknown || bar == ReasonOverageStale
}

type view struct {
	accounts []accountView
	index    map[AccountID]int
	loads    func() []int
	binding  Binding
	bound    bool
}

func decide(cfg Config, now time.Time, v view, req Request) Decision {
	if req.Conversation == "" {
		return Decision{Kind: Reject, Reason: ReasonMissingIdentity}
	}
	if !v.bound {
		return place(cfg, now, v, req.Model)
	}
	current := v.binding.Account
	if req.LastFailure == ClassRequestScoped {
		return Decision{Kind: Fail, Account: current, Reason: ReasonRequestScoped}
	}
	idx, enrolled := v.index[current]
	if !enrolled {
		return Decision{Kind: Reauth, Account: current, Reason: ReasonNotEnrolled}
	}
	acct := v.accounts[idx]
	if acct.needsLogin {
		if !v.binding.Served {
			if d := place(cfg, now, v, req.Model); d.Kind == Place {
				d.Kind, d.From, d.Reason = Migrate, current, ReasonNeverServed
				return d
			}
		}
		return Decision{Kind: Reauth, Account: current, Reason: ReasonNeedsLogin}
	}
	if acct.overage == OveragePaidUse {
		d := place(cfg, now, v, req.Model)
		switch d.Kind {
		case Place:
			d.Kind, d.From, d.Reason = Migrate, current, ReasonOverageObserved
			return d
		case Wait:
			return d
		}
		return Decision{Kind: Refuse, Account: current, Reason: ReasonPaidUse, RecheckOverage: d.RecheckOverage}
	}
	if _, _, blocked := usableReset(cfg, now, acct.rejections, req.Model); blocked {
		d := place(cfg, now, v, req.Model)
		if d.Kind == Place {
			d.Kind, d.From, d.Reason = Migrate, current, ReasonExhausted
		}
		return d
	}
	if bar := overageBar(cfg, now, acct); bar != "" {
		return Decision{Kind: Refuse, Account: current, Reason: bar, RecheckOverage: recheck(bar)}
	}
	switch req.LastFailure {
	case ClassTransient, ClassThrottle:
		if req.Attempt > cfg.MaxAttempts {
			return Decision{Kind: Fail, Account: current, Reason: ReasonRetryBudget}
		}
		return Decision{Kind: Retry, Account: current, Reason: ReasonTransient}
	}
	return Decision{Kind: Dispatch, Account: current, Reason: ReasonAssigned}
}

func place(cfg Config, now time.Time, v view, model string) Decision {
	best, bestCapacity := -1, -1
	var bestScore, bestCapacityScore float64
	loggedIn, barred, stale := false, false, false
	var earliest time.Time
	earliestKnown := false
	loads := v.loads()
	for i, a := range v.accounts {
		if a.needsLogin {
			continue
		}
		loggedIn = true
		until, known, blocked := usableReset(cfg, now, a.rejections, model)
		if bar := overageBar(cfg, now, a); bar != "" {
			barred = true
			stale = stale || recheck(bar) && !blocked
			continue
		}
		if blocked {
			earliest, earliestKnown = earlier(earliest, earliestKnown, until, known)
			continue
		}
		base := float64(loads[i]+1) / a.Capacity
		if bestCapacity < 0 || base < bestCapacityScore {
			bestCapacity, bestCapacityScore = i, base
		}
		score := base
		if cfg.Rule == ResetAware {
			score = base / (1 + cfg.ResetBias*slack(cfg, now, a.observation, model))
		}
		if best < 0 || score < bestScore {
			best, bestScore = i, score
		}
	}
	if best < 0 {
		switch {
		case !loggedIn:
			return Decision{Kind: Unavailable, Reason: ReasonNoLogin}
		case earliest.IsZero() && barred:
			return Decision{Kind: Refuse, Reason: ReasonNoVerified, RecheckOverage: stale}
		}
		return Decision{Kind: Wait, Reason: ReasonAllBlocked, Until: earliest, ResetKnown: earliestKnown, RecheckOverage: stale}
	}
	reason := ReasonCapacity
	if best != bestCapacity {
		reason = ReasonResetPreference
	}
	return Decision{
		Kind:        Place,
		Account:     v.accounts[best].ID,
		Reason:      reason,
		Observation: freshness(cfg, now, v.accounts[best].observation),
	}
}

func earlier(cur time.Time, curKnown bool, t time.Time, known bool) (time.Time, bool) {
	if cur.IsZero() || t.Before(cur) {
		return t, known
	}
	if t.Equal(cur) {
		return cur, curKnown || known
	}
	return cur, curKnown
}

func usableReset(cfg Config, now time.Time, rejections []rejection, model string) (until time.Time, known bool, blocked bool) {
	known = true
	for _, rj := range rejections {
		if !rj.appliesTo(model) {
			continue
		}
		end, exact := rj.end(cfg), !rj.ResetsAt.IsZero()
		if !end.After(now) {
			continue
		}
		blocked = true
		if end.After(until) {
			until = end
		}
		known = known && exact
	}
	if !blocked {
		return time.Time{}, false, false
	}
	return until, known, true
}

func freshness(cfg Config, now time.Time, obs Observation) Freshness {
	switch {
	case obs.At.IsZero():
		return Absent
	case now.Sub(obs.At) > cfg.FreshFor:
		return Stale
	}
	return Fresh
}

func slack(cfg Config, now time.Time, obs Observation, model string) float64 {
	if freshness(cfg, now, obs) != Fresh {
		return 0
	}
	best := 0.0
	for _, w := range obs.Windows {
		period := w.Kind.Period()
		if period == 0 || w.Utilization == nil || w.ResetsAt.IsZero() || !w.ResetsAt.After(now) || !w.appliesTo(model) {
			continue
		}
		elapsed := 1 - float64(w.ResetsAt.Sub(now))/float64(period)
		best = max(best, min(1, elapsed-*w.Utilization))
	}
	return best
}
