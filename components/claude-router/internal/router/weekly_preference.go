package router

import (
	"math"
	"time"
)

// WeeklyUsage is an account-wide weekly utilization reading, stamped on the
// service clock, with a scheduled reset on the wall clock.
type WeeklyUsage struct {
	At, ResetsAt time.Time
	Used         float64
}

// WeeklyCredit adds Left weekly allowances until EndsAt on the wall clock.
// A zero EndsAt means the grant has no expiry.
type WeeklyCredit struct {
	Left   int
	EndsAt time.Time
}

// WeeklyCredits is a successful reset reading stamped on the service clock.
// An empty Grants slice clears prior credit.
type WeeklyCredits struct {
	At     time.Time
	Grants []WeeklyCredit
}

type weeklyPreference struct {
	usage   WeeklyUsage
	credits WeeklyCredits
}

func (p weeklyPreference) slack(cfg Config, now Instant) float64 {
	u := p.usage
	if now.Held() || u.At.IsZero() || now.Stamp.Before(u.At) || now.Stamp.Sub(u.At) > cfg.FreshFor || !u.ResetsAt.After(now.Wall) {
		return 0
	}
	credits := 0.0
	if !p.credits.At.IsZero() && !now.Stamp.Before(p.credits.At) && now.Stamp.Sub(p.credits.At) <= time.Hour {
		for _, g := range p.credits.Grants {
			if g.EndsAt.IsZero() || g.EndsAt.After(now.Wall) {
				credits += float64(g.Left)
			}
		}
	}
	elapsed := 1 - float64(u.ResetsAt.Sub(now.Wall))/float64(Weekly.Period())
	return max(0, elapsed-u.Used+credits)
}

// ObserveWeeklyUsage keeps the newest valid account-wide weekly reading in
// memory without changing admission or rejection evidence.
func (r *Router) ObserveWeeklyUsage(account AccountID, reading WeeklyUsage) {
	r.mu.Lock()
	defer r.mu.Unlock()
	if idx, ok := r.index[account]; ok {
		r.observeWeeklyUsage(&r.accounts[idx], reading)
	}
}

func (r *Router) observeWeeklyUsage(a *accountView, reading WeeklyUsage) {
	if r.expiry.Held() || reading.At.IsZero() || reading.At.Before(r.expiry.Stamp) || reading.At.Before(a.weekly.usage.At) ||
		!reading.ResetsAt.After(reading.At) || math.IsNaN(reading.Used) || math.IsInf(reading.Used, 0) || reading.Used < 0 || reading.Used > 1 {
		return
	}
	a.weekly.usage = reading
}

// ObserveWeeklyCredits keeps a successful reset reading in memory. It clones
// positive, unexpired grants and ignores readings older than the last one.
func (r *Router) ObserveWeeklyCredits(account AccountID, reading WeeklyCredits) {
	r.mu.Lock()
	defer r.mu.Unlock()
	idx, ok := r.index[account]
	if !ok || r.expiry.Held() || reading.At.IsZero() || reading.At.Before(r.expiry.Stamp) || reading.At.Before(r.accounts[idx].weekly.credits.At) {
		return
	}
	grants := make([]WeeklyCredit, 0, len(reading.Grants))
	for _, g := range reading.Grants {
		if g.Left > 0 && (g.EndsAt.IsZero() || g.EndsAt.After(reading.At)) {
			grants = append(grants, g)
		}
	}
	r.accounts[idx].weekly.credits = WeeklyCredits{At: reading.At, Grants: grants}
}
