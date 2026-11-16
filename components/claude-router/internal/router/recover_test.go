package router

import (
	"bytes"
	"errors"
	"fmt"
	"io"
	"maps"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"syscall"
	"testing"
	"time"
)

type testClock struct{ at time.Time }

func (c *testClock) now() time.Time { return c.at }

func (h *harness) stopClock() *testClock {
	c := &testClock{at: t0}
	h.store.now = c.now
	return c
}

func (h *harness) bindings() map[ConversationID]Binding {
	h.t.Helper()
	b, err := h.store.Bindings()
	if err != nil {
		h.t.Fatal(err)
	}
	return b
}

func TestAFailedWriteRecoversInPlaceAndTrimsItsTornRecord(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	first := h.route(t0, req("c1"))
	h.store.file = &faultyFile{journalFile: h.store.file, shortWrite: true}

	_, errWrite := h.r.Route(At(t0), req("c2"))
	clock.at = t0.Add(recoverEvery)
	errRecover := h.r.Recover()
	_, c2, _ := h.r.Lookup("c2")
	third := h.route(t0, req("c3"))
	h.restart()
	journal, _ := os.ReadFile(filepath.Join(h.dir, journalName))

	if !errors.Is(errWrite, ErrFailed) || errRecover != nil {
		t.Fatalf("short write returned %v and recovery %v, want ErrFailed then nil", errWrite, errRecover)
	}
	if c2 {
		t.Fatal("after recovery c2 is bound, want its torn record trimmed")
	}
	if h.bound("c1") != first.Account || h.bound("c3") != third.Account {
		t.Fatalf("after a restart c1 is on %s and c3 on %s, want %s and %s", h.bound("c1"), h.bound("c3"), first.Account, third.Account)
	}
	if lines := bytes.Count(journal, []byte("\n")); lines != 2 || !bytes.HasSuffix(journal, []byte("\n")) {
		t.Fatalf("journal %q, want two complete records", journal)
	}
}

func TestAFailedSyncRecoversToTheBindingsARestartLoads(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	h.route(t0, req("c1"))
	h.store.file = &faultyFile{journalFile: h.store.file, failSync: true}

	_, errSync := h.r.Route(At(t0), req("c2"))
	clock.at = t0.Add(recoverEvery)
	errRecover := h.r.Recover()
	recovered := h.bindings()
	third := h.route(t0, req("c3"))
	h.restart()
	restarted := h.bindings()

	if !errors.Is(errSync, ErrFailed) || errRecover != nil {
		t.Fatalf("failed sync returned %v and recovery %v, want ErrFailed then nil", errSync, errRecover)
	}
	if _, ok := recovered["c2"]; !ok {
		t.Fatalf("recovered bindings %v lack c2, whose record reached the file before its sync failed", recovered)
	}
	want := maps.Clone(recovered)
	want["c3"] = Binding{Account: third.Account, Reason: third.Reason, Since: t0}
	if !maps.Equal(restarted, want) {
		t.Fatalf("a restart loaded %v, want the recovered bindings plus c3: %v", restarted, want)
	}
}

type pageCache struct {
	cache, disk []byte
	dirty       []bool
	failSync    bool
}

type pageCacheFile struct {
	pc  *pageCache
	off int64
}

func (f *pageCacheFile) Read(p []byte) (int, error) {
	if f.off >= int64(len(f.pc.cache)) {
		return 0, io.EOF
	}
	n := copy(p, f.pc.cache[f.off:])
	f.off += int64(n)
	return n, nil
}

func (f *pageCacheFile) Write(p []byte) (int, error) {
	end := f.off + int64(len(p))
	for int64(len(f.pc.cache)) < end {
		f.pc.cache, f.pc.dirty = append(f.pc.cache, 0), append(f.pc.dirty, false)
	}
	copy(f.pc.cache[f.off:], p)
	for i := f.off; i < end; i++ {
		f.pc.dirty[i] = true
	}
	f.off = end
	return len(p), nil
}

func (f *pageCacheFile) Seek(offset int64, whence int) (int64, error) {
	switch whence {
	case io.SeekStart:
		f.off = offset
	case io.SeekCurrent:
		f.off += offset
	case io.SeekEnd:
		f.off = int64(len(f.pc.cache)) + offset
	}
	return f.off, nil
}

func (f *pageCacheFile) Truncate(size int64) error {
	f.pc.cache, f.pc.dirty = f.pc.cache[:size], f.pc.dirty[:size]
	return nil
}

func (f *pageCacheFile) Sync() error {
	if f.pc.failSync {
		clear(f.pc.dirty)
		return errors.New("injected sync failure that marked the dirty bytes clean")
	}
	disk := make([]byte, len(f.pc.cache))
	copy(disk, f.pc.disk)
	for i, dirty := range f.pc.dirty {
		if dirty {
			disk[i] = f.pc.cache[i]
		}
	}
	clear(f.pc.dirty)
	f.pc.disk = disk
	return nil
}

func (f *pageCacheFile) Close() error { return nil }

func TestACrashAfterRecoveryLoadsWhatTheRecoveredStoreAcknowledged(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	h.route(t0, req("c1"))
	synced, err := os.ReadFile(filepath.Join(h.dir, journalName))
	if err != nil {
		t.Fatal(err)
	}
	pc := &pageCache{cache: bytes.Clone(synced), disk: bytes.Clone(synced), dirty: make([]bool, len(synced)), failSync: true}
	h.store.file = &pageCacheFile{pc: pc, off: int64(len(synced))}
	h.store.open = func(string) (journalFile, error) { return &pageCacheFile{pc: pc}, nil }

	_, errSync := h.r.Route(At(t0), req("c2"))
	pc.failSync = false
	clock.at = t0.Add(recoverEvery)
	errRecover := h.r.Recover()
	recovered := h.bindings()
	third := h.route(t0, req("c3"))
	crashed := t.TempDir()
	if err := os.WriteFile(filepath.Join(crashed, journalName), pc.disk, 0o600); err != nil {
		t.Fatal(err)
	}
	s, errReopen := OpenStore(crashed)
	if errReopen == nil {
		defer s.Close()
	}

	if !errors.Is(errSync, ErrFailed) || errRecover != nil {
		t.Fatalf("failed sync returned %v and recovery %v, want ErrFailed then nil", errSync, errRecover)
	}
	if errReopen != nil {
		t.Fatalf("the journal left on disk after a crash does not open: %v", errReopen)
	}
	got, _ := s.Bindings()
	want := maps.Clone(recovered)
	want["c3"] = Binding{Account: third.Account, Reason: third.Reason, Since: t0}
	if !maps.Equal(got, want) {
		t.Fatalf("after a crash the journal on disk holds %v, want what the recovered store acknowledged: %v", got, want)
	}
}

func TestRecoveryThatKeepsFailingRetriesUntilTheJournalIsWritable(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	var events []JournalEvent
	h.store.Notify(func(e JournalEvent) { events = append(events, e) })
	h.route(t0, req("c1"))
	h.store.file = &faultyFile{journalFile: h.store.file, failSync: true}
	open := h.store.open
	attempts := []func(string) (journalFile, error){
		func(string) (journalFile, error) { return nil, syscall.ENOSPC },
		func(path string) (journalFile, error) {
			f, err := open(path)
			return &faultyFile{journalFile: f, failSync: true}, err
		},
		open,
	}
	h.store.open = func(path string) (journalFile, error) {
		next := attempts[0]
		attempts = attempts[1:]
		return next(path)
	}

	_, errFirst := h.r.Route(At(t0), req("c2"))
	clock.at = t0.Add(recoverEvery)
	errOpen := h.r.Recover()
	clock.at = t0.Add(2 * recoverEvery)
	errSync := h.r.Recover()
	_, errRoute := h.r.Route(At(t0), req("c3"))
	clock.at = t0.Add(3 * recoverEvery)
	errRecovered := h.r.Recover()
	placed := h.route(t0, req("c3"))

	if !errors.Is(errFirst, ErrFailed) {
		t.Fatalf("failed sync returned %v, want ErrFailed", errFirst)
	}
	if !errors.Is(errOpen, ErrFailed) || !errors.Is(errOpen, syscall.ENOSPC) {
		t.Fatalf("recovery while the journal cannot open returned %v, want ErrFailed naming ENOSPC", errOpen)
	}
	if !errors.Is(errSync, ErrFailed) || !strings.Contains(fmt.Sprint(errSync), "injected sync failure") {
		t.Fatalf("recovery whose sync failed returned %v, want ErrFailed naming the sync failure", errSync)
	}
	if !errors.Is(errRoute, ErrFailed) {
		t.Fatalf("Route between failed recoveries returned %v, want ErrFailed", errRoute)
	}
	if errRecovered != nil || placed.Account == "" || h.store.JournalFailure() != nil {
		t.Fatalf("third recovery returned %v, then c3 got %+v and the store reports %+v; want recovery and a placement", errRecovered, placed, h.store.JournalFailure())
	}
	var kinds []JournalEventKind
	for _, e := range events {
		kinds = append(kinds, e.Kind)
	}
	want := []JournalEventKind{JournalFailed, JournalRecoveryFailed, JournalRecoveryFailed, JournalRecovered}
	if fmt.Sprint(kinds) != fmt.Sprint(want) {
		t.Fatalf("journal events %v, want %v", kinds, want)
	}
	if last := events[3]; last.Attempts != 2 || !last.Since.Equal(t0) {
		t.Fatalf("recovered event %+v, want 2 failed attempts since %v", last, t0)
	}
}

func TestRecoveryIsAttemptedAtMostOnceEveryFiveSeconds(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	h.route(t0, req("c1"))
	h.store.file = &faultyFile{journalFile: h.store.file, failSync: true}
	h.store.open = func(string) (journalFile, error) { return nil, syscall.ENOSPC }
	if _, err := h.r.Route(At(t0), req("c2")); err == nil {
		t.Fatal("the failed sync returned no error")
	}

	var got []int
	for _, at := range []time.Duration{0, 4999 * time.Millisecond, 5 * time.Second, 5 * time.Second, 9999 * time.Millisecond, 10 * time.Second} {
		clock.at = t0.Add(at)
		if err := h.r.Recover(); !errors.Is(err, ErrFailed) {
			t.Fatalf("recover at +%v returned %v, want ErrFailed", at, err)
		}
		got = append(got, h.store.JournalFailure().Attempts)
	}

	if want := []int{0, 0, 1, 1, 1, 2}; fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("failed recovery attempts after each recover call %v, want %v", got, want)
	}
	if f := h.store.JournalFailure(); !f.RetryAfter.Equal(t0.Add(15 * time.Second)) {
		t.Fatalf("failure %+v, want the next attempt at %v", f, t0.Add(15*time.Second))
	}
}

func TestAStoreThatStaysFailedReportsRecoveryAttemptsSparingly(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	var events []JournalEvent
	h.store.Notify(func(e JournalEvent) { events = append(events, e) })
	h.route(t0, req("c1"))
	h.store.file = &faultyFile{journalFile: h.store.file, failSync: true}
	attempt := 0
	open := h.store.open
	h.store.open = func(path string) (journalFile, error) {
		attempt++
		switch {
		case attempt == 6:
			return nil, syscall.EACCES
		case attempt <= 9:
			return nil, syscall.ENOSPC
		}
		return open(path)
	}
	if _, err := h.r.Route(At(t0), req("c2")); err == nil {
		t.Fatal("the failed sync returned no error")
	}

	for i := 1; i <= 10; i++ {
		clock.at = t0.Add(time.Duration(i) * recoverEvery)
		_ = h.r.Recover()
	}

	var got []string
	for _, e := range events {
		got = append(got, fmt.Sprintf("%s %d", e.Kind, e.Attempts))
	}
	want := []string{
		"journal_failed 0",
		"journal_recovery_failed 1", "journal_recovery_failed 2", "journal_recovery_failed 4",
		"journal_recovery_failed 6", "journal_recovery_failed 7", "journal_recovery_failed 8",
		"journal_recovered 9",
	}
	if fmt.Sprint(got) != fmt.Sprint(want) {
		t.Fatalf("journal events %v, want %v", got, want)
	}
	if !errors.Is(events[4].Err, syscall.EACCES) || !errors.Is(events[5].Err, syscall.ENOSPC) {
		t.Fatalf("attempts 6 and 7 reported %v and %v, want EACCES then ENOSPC", events[4].Err, events[5].Err)
	}
}

func TestRequestsDuringRecoveryWaitForItAndAgreeWithReplay(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	var kinds []JournalEventKind
	h.store.Notify(func(e JournalEvent) { kinds = append(kinds, e.Kind) })
	h.route(t0, req("c0"))
	h.store.file = &faultyFile{journalFile: h.store.file, failSync: true}
	if _, err := h.r.Route(At(t0), req("lost")); err == nil {
		t.Fatal("the failed sync returned no error")
	}
	clock.at = t0.Add(recoverEvery)
	entered, release := make(chan struct{}), make(chan struct{})
	open := h.store.open
	h.store.open = func(path string) (journalFile, error) {
		close(entered)
		<-release
		return open(path)
	}

	first := make(chan error, 1)
	go func() { first <- h.r.Recover() }()
	<-entered
	const n = 8
	decisions, errs := make([]Decision, n), make([]error, n)
	var started, wg sync.WaitGroup
	var finished atomic.Int32
	for i := range n {
		started.Add(1)
		wg.Add(1)
		go func() {
			defer wg.Done()
			started.Done()
			if errs[i] = h.r.Recover(); errs[i] == nil {
				decisions[i], errs[i] = h.r.Route(At(t0), req(fmt.Sprintf("c%d", i+1)))
			}
			finished.Add(1)
		}()
	}
	started.Wait()
	time.Sleep(50 * time.Millisecond)
	early := finished.Load()
	close(release)
	wg.Wait()
	errFirst := <-first
	h.restart()

	if errFirst != nil || early != 0 {
		t.Fatalf("first recovery returned %v, and %d requests finished while it held the store; want nil and 0", errFirst, early)
	}
	if want := []JournalEventKind{JournalFailed, JournalRecovered}; fmt.Sprint(kinds) != fmt.Sprint(want) {
		t.Fatalf("journal events %v, want %v: one recovery for every request", kinds, want)
	}
	for i, d := range decisions {
		conv := ConversationID(fmt.Sprintf("c%d", i+1))
		if errs[i] != nil || d.Account == "" || h.bound(conv) != d.Account {
			t.Errorf("%s during recovery got %+v, %v and is bound to %q after a restart; want the same account", conv, d, errs[i], h.bound(conv))
		}
	}
}

func TestAClosedStoreDoesNotRecover(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	h.route(t0, req("c1"))
	h.store.Close()

	_, errWrite := h.r.Route(At(t0), req("c2"))
	clock.at = t0.Add(recoverEvery)
	errRecover := h.r.Recover()

	if !errors.Is(errWrite, ErrFailed) || !errors.Is(errRecover, ErrFailed) {
		t.Fatalf("after Close a write returned %v and recovery %v; want ErrFailed for both", errWrite, errRecover)
	}
}

func TestRecoveryRefusesAStateDirectoryWhoseLockFileWasReplaced(t *testing.T) {
	h := newHarness(t, DefaultConfig(), accts(a, 1.0, b, 1.0)...)
	clock := h.stopClock()
	h.route(t0, req("c1"))
	h.store.file = &faultyFile{journalFile: h.store.file, failSync: true}
	if _, err := h.r.Route(At(t0), req("c2")); err == nil {
		t.Fatal("the failed sync returned no error")
	}
	if err := os.Remove(filepath.Join(h.dir, lockName)); err != nil {
		t.Fatal(err)
	}

	clock.at = t0.Add(recoverEvery)
	errRemoved := h.r.Recover()
	other, errOther := OpenStore(h.dir)
	clock.at = t0.Add(2 * recoverEvery)
	errReplaced := h.r.Recover()
	if errOther == nil {
		other.Close()
	}

	if !errors.Is(errRemoved, ErrFailed) || !errors.Is(errRemoved, ErrLockReplaced) {
		t.Fatalf("recovery after the lock file was removed returned %v, want ErrFailed naming ErrLockReplaced", errRemoved)
	}
	if errOther != nil {
		t.Fatalf("a second store could not open the directory: %v", errOther)
	}
	if !errors.Is(errReplaced, ErrFailed) || !errors.Is(errReplaced, ErrLockReplaced) {
		t.Fatalf("recovery after a second store locked a new lock file returned %v, want ErrFailed naming ErrLockReplaced", errReplaced)
	}
}
