package router

import (
	"time"
)

type accountView struct {
	Account
	needsLogin  bool
	observation Observation
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
	idx, enrolled := v.index[current]
	if !enrolled {
		return Decision{Kind: Reauth, Account: current, Reason: ReasonNotEnrolled}
	}
	acct := v.accounts[idx]
	if acct.needsLogin {
		return Decision{Kind: Reauth, Account: current, Reason: ReasonNeedsLogin}
	}
	if _, _, blocked := usableReset(cfg, now, acct.observation, req.Model); blocked {
		d := place(cfg, now, v, req.Model)
		if d.Kind == Place {
			d.Kind, d.From, d.Reason = Migrate, current, ReasonExhausted
		}
		return d
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

// place chooses an account for new work. It minimizes
// (load+1)/(capacity*boost) over logged-in accounts that no known rejection
// blocks for the model. boost is 1 under CapacityOnly and 1+ResetBias*slack
// under ResetAware. Ties go to the earlier enrolled account.
func place(cfg Config, now time.Time, v view, model string) Decision {
	best, bestCapacity := -1, -1
	var bestScore, bestCapacityScore float64
	loggedIn := false
	var earliest time.Time
	earliestKnown := false
	loads := v.loads()
	for i, a := range v.accounts {
		if a.needsLogin {
			continue
		}
		loggedIn = true
		if until, known, blocked := usableReset(cfg, now, a.observation, model); blocked {
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
		if !loggedIn {
			return Decision{Kind: Unavailable, Reason: ReasonNoLogin}
		}
		return Decision{Kind: Wait, Reason: ReasonAllBlocked, Until: earliest, ResetKnown: earliestKnown}
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

// usableReset returns the time after which no known rejection blocks the
// model: the latest end across all blocking windows, so an earlier five-hour
// reset never hides a weekly rejection. A rejection without a reported reset
// ends at its recheck time and makes the result inexact.
func usableReset(cfg Config, now time.Time, obs Observation, model string) (until time.Time, known bool, blocked bool) {
	known = true
	for _, w := range obs.Windows {
		if !w.Rejected || !w.appliesTo(model) {
			continue
		}
		end, exact := w.ResetsAt, true
		if end.IsZero() {
			end, exact = obs.At.Add(cfg.UnknownResetRecheck), false
		}
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

// slack is the largest share of a fresh window's allowance that is behind an
// even pace: elapsed fraction of the window minus utilization. A window that
// has already reset, or lacks a reset or utilization, contributes nothing.
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
