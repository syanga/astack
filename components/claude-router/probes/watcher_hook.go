package probes

import (
	"strings"
	"sync"

	log "github.com/sirupsen/logrus"
)

// The SDK exposes no signal for the end of watcher startup. Service.Run logs
// this message once the watcher's initial scan and configuration apply return
// (sdk/cliproxy/service_lifecycle.go:204).
const watcherStartedMessage = "file watcher started for config and auth directory changes"

type watcherStartHook struct {
	mu      sync.Mutex
	waiting chan struct{}
}

var (
	watcherHook     = &watcherStartHook{}
	watcherHookOnce sync.Once
)

func (h *watcherStartHook) Levels() []log.Level { return log.AllLevels }

func (h *watcherStartHook) Fire(e *log.Entry) error {
	if !strings.HasPrefix(e.Message, watcherStartedMessage) {
		return nil
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	if h.waiting != nil {
		close(h.waiting)
		h.waiting = nil
	}
	return nil
}

// expectWatcherStart returns a channel that closes when the next service
// logs the end of its watcher startup.
func expectWatcherStart() <-chan struct{} {
	watcherHookOnce.Do(func() { log.AddHook(watcherHook) })
	ch := make(chan struct{})
	watcherHook.mu.Lock()
	watcherHook.waiting = ch
	watcherHook.mu.Unlock()
	return ch
}
