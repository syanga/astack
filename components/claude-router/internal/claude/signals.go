package claude

import (
	"errors"
	"net/http"
	"strconv"
	"strings"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/router"
)

const unifiedPrefix = "Anthropic-Ratelimit-Unified-"

func unified(h http.Header, name string) string {
	return strings.ToLower(strings.TrimSpace(h.Get(unifiedPrefix + name)))
}

func observationFrom(h http.Header, at time.Time) (router.Observation, bool) {
	obs := router.Observation{At: at}
	for _, w := range []struct {
		prefix string
		kind   router.WindowKind
	}{{"5h", router.FiveHour}, {"7d", router.Weekly}} {
		status := unified(h, w.prefix+"-Status")
		util := unified(h, w.prefix+"-Utilization")
		reset := unified(h, w.prefix+"-Reset")
		if status == "" && util == "" && reset == "" {
			continue
		}
		win := router.Window{Kind: w.kind, Rejected: status == "rejected"}
		if v, err := strconv.ParseFloat(util, 64); err == nil {
			win.Utilization = &v
		}
		if v, err := strconv.ParseInt(reset, 10, 64); err == nil && v > 0 {
			win.ResetsAt = time.Unix(v, 0)
		}
		obs.Windows = append(obs.Windows, win)
	}
	return obs, len(obs.Windows) > 0
}

// overageFrom reads the paid-overflow state a response reports. Paid use is
// a representative claim of overage or overage-in-use true. Otherwise the
// overage status reports the account setting at the time of the response.
// A response without overage headers reports nothing, which stays unknown.
func overageFrom(h http.Header) (router.Overage, bool) {
	if unified(h, "Representative-Claim") == "overage" || unified(h, "Overage-In-Use") == "true" {
		return router.OveragePaidUse, true
	}
	switch unified(h, "Overage-Status") {
	case "rejected":
		return router.OverageDisabled, true
	case "allowed", "allowed_warning":
		return router.OverageEnabled, true
	}
	return "", false
}

// classify maps a failure before output to its recovery class from per-call
// signals only: the SDK error's status and scope, and the headers of the
// call's own last upstream attempt. It never reads the SDK's passive quota
// snapshot (RP-15).
func classify(status int, err error, attempt http.Header) router.Class {
	switch {
	case status == http.StatusUnauthorized || status == http.StatusForbidden:
		return router.ClassAuth
	case status == http.StatusRequestTimeout || status >= 500:
		return router.ClassTransient
	}
	var requestScoped interface{ IsRequestScoped() bool }
	if errors.As(err, &requestScoped) && requestScoped.IsRequestScoped() {
		return router.ClassRequestScoped
	}
	if status == http.StatusTooManyRequests {
		var credScoped interface{ IsCredentialScoped() bool }
		credential := errors.As(err, &credScoped) && credScoped.IsCredentialScoped()
		if credential || unified(attempt, "5h-Status") == "rejected" || unified(attempt, "7d-Status") == "rejected" {
			return router.ClassExhausted
		}
		if unified(attempt, "Status") == "rejected" {
			return router.ClassModelLimit
		}
		return router.ClassThrottle
	}
	return router.ClassRequestScoped
}

func classifyStreamError(errorType string) router.Class {
	switch errorType {
	case "overloaded_error", "api_error":
		return router.ClassTransient
	case "rate_limit_error":
		return router.ClassThrottle
	case "authentication_error", "permission_error":
		return router.ClassAuth
	}
	return router.ClassRequestScoped
}

func exhaustionEvidence(attempt http.Header, at time.Time, class router.Class) router.Observation {
	obs, _ := observationFrom(attempt, at)
	obs.At = at
	if class != router.ClassExhausted {
		return obs
	}
	for _, w := range obs.Windows {
		if w.Rejected {
			return obs
		}
	}
	reset := time.Time{}
	if v, err := strconv.ParseInt(unified(attempt, "Reset"), 10, 64); err == nil && v > 0 {
		reset = time.Unix(v, 0)
	}
	obs.Windows = append(obs.Windows, router.Window{Kind: router.Unspecified, Rejected: true, ResetsAt: reset})
	return obs
}
