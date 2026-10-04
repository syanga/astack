package probes

import (
	"net/http"
	"strings"
)

// Class is the router's recovery category for a failure before output.
type Class string

const (
	ClassTransient  Class = "transient"
	ClassThrottle   Class = "throttle"
	ClassExhausted  Class = "exhausted"
	ClassModelLimit Class = "model_limit"
	ClassAuth       Class = "auth"
	ClassOther      Class = "other"
)

// Classify maps a failed call's signal to a recovery class. A 429 counts as
// subscription exhaustion only when the SDK marks it credential-scoped or the
// snapshot shows a rejected five-hour or weekly window. A unified rejection
// without either is model-scoped, such as an overage-only claim. Neither the
// SDK error nor its headers distinguish that case without the snapshot.
func Classify(sig ErrorSignal) Class {
	get := func(name string) string {
		return strings.ToLower(strings.TrimSpace(sig.Headers[http.CanonicalHeaderKey(name)]))
	}
	switch {
	case sig.Status == http.StatusUnauthorized || sig.Status == http.StatusForbidden:
		return ClassAuth
	case sig.Status == http.StatusTooManyRequests:
		if sig.CredentialScoped || get("Anthropic-Ratelimit-Unified-5h-Status") == "rejected" || get("Anthropic-Ratelimit-Unified-7d-Status") == "rejected" {
			return ClassExhausted
		}
		if get("Anthropic-Ratelimit-Unified-Status") == "rejected" {
			return ClassModelLimit
		}
		return ClassThrottle
	case sig.Status == http.StatusRequestTimeout || sig.Status >= 500:
		return ClassTransient
	default:
		return ClassOther
	}
}
