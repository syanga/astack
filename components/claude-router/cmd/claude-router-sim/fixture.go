package main

import (
	"encoding/json"
	"fmt"
	"os"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

// Fixture describes a synthetic workload. Every quota and cache quantity is
// an assumption, labeled in Assumptions and copied into each result.
type Fixture struct {
	Name        string            `json:"name"`
	Assumptions map[string]string `json:"assumptions"`
	Start       time.Time         `json:"start"`
	Hours       float64           `json:"hours"`
	// FiveHourUnits and WeeklyUnits are the allowance of a capacity-1
	// account, in thousands of cost-weighted tokens.
	FiveHourUnits float64      `json:"five_hour_units"`
	WeeklyUnits   float64      `json:"weekly_units"`
	Accounts      []SimAccount `json:"accounts"`
	Workload      Workload     `json:"workload"`
	Cache         CacheModel   `json:"cache"`
	CrashHours    []float64    `json:"crash_hours,omitempty"`
	AuthFaults    []AuthFault  `json:"auth_faults,omitempty"`
	PaidUse       []PaidUse    `json:"paid_use,omitempty"`
	// OverageCheckMinutes is the interval of the assumed settings read that
	// verifies each account's paid overflow is disabled.
	OverageCheckMinutes float64 `json:"overage_check_minutes"`
}

// PaidUse makes a response show paid use on an account at AtHours. The
// account's overflow is disabled again at ClearedHours, 0 meaning never.
type PaidUse struct {
	Account      router.AccountID `json:"account"`
	AtHours      float64          `json:"at_hours"`
	ClearedHours float64          `json:"cleared_hours,omitempty"`
}

// SimAccount is one account's synthetic quota state at the start.
type SimAccount struct {
	ID       router.AccountID `json:"id"`
	Capacity float64          `json:"capacity"`
	// FiveHourUsed and WeeklyUsed are starting utilization fractions.
	FiveHourUsed       float64 `json:"five_hour_used"`
	FiveHourResetHours float64 `json:"five_hour_reset_hours"`
	WeeklyUsed         float64 `json:"weekly_used"`
	WeeklyResetHours   float64 `json:"weekly_reset_hours"`
	// ModelWindows are extra weekly windows that constrain some models.
	ModelWindows []ModelWindow `json:"model_windows,omitempty"`
	// ExternalPerHour is other clients' usage, as a fraction of this
	// account's five-hour allowance per hour.
	ExternalPerHour float64 `json:"external_per_hour,omitempty"`
	// HideHeaders makes responses carry no quota observation.
	HideHeaders bool `json:"hide_headers,omitempty"`
}

// ModelWindow is a model-specific weekly window, sized as a fraction of the
// account's weekly allowance.
type ModelWindow struct {
	Models     []string `json:"models"`
	Share      float64  `json:"share"`
	Used       float64  `json:"used"`
	ResetHours float64  `json:"reset_hours"`
}

// Workload describes conversations and their requests.
type Workload struct {
	Conversations       int      `json:"conversations"`
	ArrivalMeanMinutes  float64  `json:"arrival_mean_minutes"`
	TurnsMin            int      `json:"turns_min"`
	TurnsMax            int      `json:"turns_max"`
	GapMeanMinutes      float64  `json:"gap_mean_minutes"`
	IdleProbability     float64  `json:"idle_probability"`
	IdleMinutes         float64  `json:"idle_minutes"`
	InitialContext      int      `json:"initial_context"`
	GrowthPerTurn       int      `json:"growth_per_turn"`
	OutputPerTurn       int      `json:"output_per_turn"`
	Models              []string `json:"models"`
	ModelChange         float64  `json:"model_change_probability"`
	SubagentProbability float64  `json:"subagent_probability"`
	SubagentContext     int      `json:"subagent_context"`
	NestedProbability   float64  `json:"nested_probability"`
	ParallelFirst       int      `json:"parallel_first_requests"`
	TransientRate       float64  `json:"transient_rate"`
	PartialRate         float64  `json:"partial_stream_rate"`
	CancelRate          float64  `json:"cancel_rate"`
}

// CacheModel prices a request in allowance units and sets prompt-cache life.
type CacheModel struct {
	TTLMinutes   float64 `json:"ttl_minutes"`
	InputWeight  float64 `json:"input_weight"`
	WriteWeight  float64 `json:"write_weight"`
	ReadWeight   float64 `json:"read_weight"`
	OutputWeight float64 `json:"output_weight"`
}

// AuthFault logs an account out at AtHours. The router learns it from the
// next request's 401. ReloginHours of 0 means never in the run. With
// MoveAfterMinutes set, a stalled conversation is moved manually.
type AuthFault struct {
	Account          router.AccountID `json:"account"`
	AtHours          float64          `json:"at_hours"`
	ReloginHours     float64          `json:"relogin_hours,omitempty"`
	MoveAfterMinutes float64          `json:"move_after_minutes,omitempty"`
}

func loadJSON(path string, v any) error {
	data, err := os.ReadFile(path)
	if err != nil {
		return err
	}
	if err := json.Unmarshal(data, v); err != nil {
		return fmt.Errorf("%s: %w", path, err)
	}
	return nil
}

func hours(h float64) time.Duration { return time.Duration(h * float64(time.Hour)) }

func minutes(m float64) time.Duration { return time.Duration(m * float64(time.Minute)) }
