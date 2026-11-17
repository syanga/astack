package router

import (
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"syscall"
	"time"
)

// ErrLocked reports that another process holds the assignment store.
var ErrLocked = errors.New("assignment store is held by another process")

// ErrFailed reports that a journal write or sync, or a write of the account
// state, failed and the store has not recovered yet. The store acknowledges
// nothing until Router.Recover succeeds, because the failed record may or may
// not be on disk. The error wraps the latest cause.
var ErrFailed = errors.New("assignment journal unavailable")

// RecoverEvery is the least time between a failed write or sync and the
// first recovery attempt, and between recovery attempts.
const RecoverEvery = 5 * time.Second

// ErrLockReplaced reports that the lock file in the state directory is no
// longer the one the store locked, so the store cannot recover without
// opening it again.
var ErrLockReplaced = errors.New("the state directory's lock file was replaced; restart the router")

// ErrIncomplete reports a record without a conversation, account, time, or
// migration source. The store refuses it before writing.
var ErrIncomplete = errors.New("incomplete assignment record")

// ErrUnassigned reports a migration of a conversation that has no assignment.
var ErrUnassigned = errors.New("conversation has no assignment")

// Binding is a conversation's durable assignment.
// Served is true once the binding has served a successful response. A
// migration starts a new binding with Served false.
type Binding struct {
	Account AccountID `json:"account"`
	Reason  Reason    `json:"reason"`
	Since   time.Time `json:"since"`
	Served  bool      `json:"served,omitempty"`
}

// Store is the durable assignment journal: one writer process, an
// append-only JSON-lines file, and an fsync before a commit returns.
type Store struct {
	mu       sync.RWMutex
	dir      string
	open     func(path string) (journalFile, error)
	now      func() time.Time
	notify   func(JournalEvent)
	file     journalFile
	lock     *os.File
	bindings map[ConversationID]Binding
	failure  *JournalFailure
	closed   bool
}

// JournalFailure describes a store that stopped after a failed write or
// sync.
type JournalFailure struct {
	// Err is the latest error: the failed write or sync, or the latest
	// failed recovery attempt.
	Err error `json:"-"`
	// Since is when the write or sync failed.
	Since time.Time `json:"since"`
	// Attempts counts the recovery attempts that failed since then.
	Attempts int `json:"recovery_attempts"`
	// RetryAfter is the earliest time of the next recovery attempt.
	RetryAfter time.Time `json:"retry_after"`
}

// JournalEventKind names a change in the store's health.
type JournalEventKind string

const (
	JournalFailed         JournalEventKind = "journal_failed"
	JournalRecoveryFailed JournalEventKind = "journal_recovery_failed"
	JournalRecovered      JournalEventKind = "journal_recovered"
)

// JournalEvent is a change in the store's health and the failure after it.
// For JournalRecovered, it is the failure that recovery ended.
type JournalEvent struct {
	Kind JournalEventKind
	JournalFailure
}

type journalFile interface {
	io.ReadWriteSeeker
	Sync() error
	Truncate(size int64) error
	Close() error
}

type op string

const (
	opAssign  op = "assign"
	opMigrate op = "migrate"
	opServed  op = "served"
)

type record struct {
	Op           op             `json:"op"`
	Conversation ConversationID `json:"c"`
	Account      AccountID      `json:"a"`
	From         AccountID      `json:"from,omitempty"`
	Reason       Reason         `json:"why"`
	At           time.Time      `json:"t"`
}

const (
	journalName = "assignments.jsonl"
	lockName    = "lock"
)

// OpenStore locks dir against other processes and replays its journal. A torn
// final record, left by a crash during append, is truncated. A corrupt
// interior record fails the open rather than losing assignments.
func OpenStore(dir string) (*Store, error) {
	if err := makeDurableDir(dir); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(dir, lockName), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, ErrLocked
		}
		return nil, err
	}
	s := &Store{dir: dir, open: openFile, now: time.Now, lock: lock}
	if s.file, s.bindings, _, err = s.load(); err != nil {
		lock.Close()
		return nil, err
	}
	return s, nil
}

func openFile(path string) (journalFile, error) {
	return os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
}

func (s *Store) load() (file journalFile, bindings map[ConversationID]Binding, records []byte, err error) {
	path := filepath.Join(s.dir, journalName)
	_, statErr := os.Stat(path)
	if file, err = s.open(path); err != nil {
		return nil, nil, nil, err
	}
	if errors.Is(statErr, os.ErrNotExist) {
		if err := syncDir(s.dir); err != nil {
			file.Close()
			return nil, nil, nil, err
		}
	}
	if bindings, records, err = replay(file); err != nil {
		file.Close()
		return nil, nil, nil, err
	}
	return file, bindings, records, nil
}

func makeDurableDir(dir string) error {
	if _, err := os.Stat(dir); err == nil {
		return nil
	}
	parent := filepath.Dir(filepath.Clean(dir))
	if err := makeDurableDir(parent); err != nil {
		return err
	}
	if err := os.Mkdir(dir, 0o700); err != nil && !errors.Is(err, os.ErrExist) {
		return err
	}
	return syncDir(parent)
}

func syncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

func replay(file journalFile) (map[ConversationID]Binding, []byte, error) {
	data, err := io.ReadAll(file)
	if err != nil {
		return nil, nil, err
	}
	s := &Store{bindings: map[ConversationID]Binding{}}
	good, err := s.applyAll(data)
	if err != nil {
		return nil, nil, err
	}
	if good < len(data) {
		if err := file.Truncate(int64(good)); err != nil {
			return nil, nil, err
		}
		if err := file.Sync(); err != nil {
			return nil, nil, err
		}
	}
	if _, err := file.Seek(int64(good), io.SeekStart); err != nil {
		return nil, nil, err
	}
	return s.bindings, data[:good], nil
}

func (s *Store) applyAll(data []byte) (int, error) {
	good := 0
	for good < len(data) {
		end := bytes.IndexByte(data[good:], '\n')
		if end < 0 {
			break
		}
		var rec record
		if err := json.Unmarshal(data[good:good+end], &rec); err != nil {
			return good, fmt.Errorf("corrupt journal record at byte %d: %w", good, err)
		}
		if err := s.apply(rec); err != nil {
			return good, fmt.Errorf("journal record at byte %d: %w", good, err)
		}
		good += end + 1
	}
	return good, nil
}

// ReadBindings replays the journal in dir without its lock, for a reader
// beside a running service. It skips a torn final record and changes nothing.
func ReadBindings(dir string) (map[ConversationID]Binding, error) {
	data, err := os.ReadFile(filepath.Join(dir, journalName))
	if errors.Is(err, os.ErrNotExist) {
		return map[ConversationID]Binding{}, nil
	}
	if err != nil {
		return nil, err
	}
	s := &Store{bindings: map[ConversationID]Binding{}}
	if _, err := s.applyAll(data); err != nil {
		return nil, err
	}
	return s.bindings, nil
}

func (rec record) validate() error {
	switch {
	case rec.Op != opAssign && rec.Op != opMigrate && rec.Op != opServed:
		return fmt.Errorf("unknown op %q", rec.Op)
	case rec.Conversation == "" || rec.Account == "" || rec.At.IsZero():
		return fmt.Errorf("%w: missing conversation, account, or time", ErrIncomplete)
	case rec.Op == opMigrate && rec.From == "":
		return fmt.Errorf("%w: migration without a source account", ErrIncomplete)
	}
	return nil
}

func (s *Store) apply(rec record) error {
	if err := rec.validate(); err != nil {
		return err
	}
	current, assigned := s.bindings[rec.Conversation]
	switch rec.Op {
	case opAssign:
		if !assigned {
			s.bindings[rec.Conversation] = Binding{Account: rec.Account, Reason: rec.Reason, Since: rec.At}
		}
	case opMigrate:
		if assigned && current.Account == rec.From {
			s.bindings[rec.Conversation] = Binding{Account: rec.Account, Reason: rec.Reason, Since: rec.At}
		}
	case opServed:
		if assigned && current.Account == rec.Account {
			current.Served = true
			s.bindings[rec.Conversation] = current
		}
	}
	return nil
}

func (s *Store) err() error {
	if s.failure != nil {
		return fmt.Errorf("%w: %w", ErrFailed, s.failure.Err)
	}
	return nil
}

// Err returns ErrFailed, wrapping the latest cause, while the store is
// failed, and nil otherwise.
func (s *Store) Err() error {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.err()
}

// JournalFailure returns the store's failure, or nil when it is healthy.
func (s *Store) JournalFailure() *JournalFailure {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if s.failure == nil {
		return nil
	}
	f := *s.failure
	return &f
}

// Notify sets the function the store calls, with its lock held, when a write
// or sync fails and when the store recovers. While the store stays failed, it
// calls f for a failed recovery attempt only when the attempt's error differs
// from the one before it or the attempt count is a power of two. Calls never
// overlap.
func (s *Store) Notify(f func(JournalEvent)) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.notify = f
}

func (s *Store) emit(kind JournalEventKind) {
	if s.notify != nil {
		s.notify(JournalEvent{Kind: kind, JournalFailure: *s.failure})
	}
}

func (s *Store) fail(err error) error {
	now := s.now()
	s.failure = &JournalFailure{Err: err, Since: now, RetryAfter: now.Add(RecoverEvery)}
	s.emit(JournalFailed)
	return s.err()
}

func (s *Store) due(now time.Time) bool {
	return s.failure != nil && !s.closed && !now.Before(s.failure.RetryAfter)
}

func (s *Store) recoveryDue() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.due(s.now())
}

func (s *Store) recover(save func() error) (recovered bool, err error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	now := s.now()
	if !s.due(now) {
		return false, s.err()
	}
	f := s.failure
	if err := s.reload(save); err != nil {
		changed := err.Error() != f.Err.Error()
		f.Err, f.Attempts, f.RetryAfter = err, f.Attempts+1, now.Add(RecoverEvery)
		if changed || f.Attempts&(f.Attempts-1) == 0 {
			s.emit(JournalRecoveryFailed)
		}
		return false, s.err()
	}
	s.emit(JournalRecovered)
	s.failure = nil
	return true, nil
}

func (s *Store) reload(save func() error) error {
	if s.file != nil {
		_ = s.file.Close()
		s.file = nil
	}
	held, err := s.lock.Stat()
	if err != nil {
		return err
	}
	current, err := os.Stat(filepath.Join(s.dir, lockName))
	if errors.Is(err, os.ErrNotExist) || err == nil && !os.SameFile(held, current) {
		return ErrLockReplaced
	}
	if err != nil {
		return err
	}
	file, bindings, data, err := s.load()
	if err != nil {
		return err
	}
	if err := rewrite(file, data); err != nil {
		file.Close()
		return err
	}
	if save != nil {
		if err := save(); err != nil {
			file.Close()
			return err
		}
	}
	s.file, s.bindings = file, bindings
	return nil
}

// After a failed fsync, Linux can keep a file's pages marked clean although
// they never reached the disk, and a later fsync does not write them.
// Writing the same bytes again marks them dirty, so the sync writes them.
func rewrite(file journalFile, data []byte) error {
	if _, err := file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	if _, err := file.Write(data); err != nil {
		return err
	}
	return file.Sync()
}

func (s *Store) append(rec record) error {
	if err := s.err(); err != nil {
		return err
	}
	if err := rec.validate(); err != nil {
		return err
	}
	line, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if _, err := s.file.Write(append(line, '\n')); err != nil {
		return s.fail(err)
	}
	if err := s.file.Sync(); err != nil {
		return s.fail(err)
	}
	return nil
}

// Assign returns the conversation's binding, durably recording proposed as
// its first assignment when it has none. Concurrent callers for one
// conversation receive the same binding.
func (s *Store) Assign(conv ConversationID, proposed AccountID, reason Reason, at time.Time) (Binding, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.err(); err != nil {
		return Binding{}, err
	}
	if b, ok := s.bindings[conv]; ok {
		return b, nil
	}
	rec := record{Op: opAssign, Conversation: conv, Account: proposed, Reason: reason, At: at}
	if err := s.append(rec); err != nil {
		return Binding{}, err
	}
	_ = s.apply(rec)
	return s.bindings[conv], nil
}

// Migrate moves the conversation from one account to another when it is still
// on from, and returns the resulting binding. When another caller already
// moved it, the binding is returned unchanged.
func (s *Store) Migrate(conv ConversationID, from, to AccountID, reason Reason, at time.Time) (Binding, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.err(); err != nil {
		return Binding{}, err
	}
	b, ok := s.bindings[conv]
	if !ok {
		return Binding{}, ErrUnassigned
	}
	if b.Account != from || from == to {
		return b, nil
	}
	rec := record{Op: opMigrate, Conversation: conv, Account: to, From: from, Reason: reason, At: at}
	if err := s.append(rec); err != nil {
		return Binding{}, err
	}
	_ = s.apply(rec)
	return s.bindings[conv], nil
}

// MarkServed durably records that the conversation's binding on account has
// served a successful response. It writes once per binding and does nothing
// when the conversation is no longer on account.
func (s *Store) MarkServed(conv ConversationID, account AccountID, at time.Time) (Binding, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.err(); err != nil {
		return Binding{}, err
	}
	b, ok := s.bindings[conv]
	if !ok {
		return Binding{}, ErrUnassigned
	}
	if b.Account != account || b.Served {
		return b, nil
	}
	rec := record{Op: opServed, Conversation: conv, Account: account, At: at}
	if err := s.append(rec); err != nil {
		return Binding{}, err
	}
	_ = s.apply(rec)
	return s.bindings[conv], nil
}

// Lookup returns the conversation's binding.
func (s *Store) Lookup(conv ConversationID) (Binding, bool, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if err := s.err(); err != nil {
		return Binding{}, false, err
	}
	b, ok := s.bindings[conv]
	return b, ok, nil
}

// Bindings returns a copy of every binding.
func (s *Store) Bindings() (map[ConversationID]Binding, error) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if err := s.err(); err != nil {
		return nil, err
	}
	out := make(map[ConversationID]Binding, len(s.bindings))
	for k, v := range s.bindings {
		out[k] = v
	}
	return out, nil
}

func (s *Store) each(f func(ConversationID, Binding)) error {
	s.mu.RLock()
	defer s.mu.RUnlock()
	if err := s.err(); err != nil {
		return err
	}
	for k, v := range s.bindings {
		f(k, v)
	}
	return nil
}

// Close releases the journal and the process lock.
func (s *Store) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.closed = true
	var err error
	if s.file != nil {
		err = s.file.Close()
	}
	if errLock := s.lock.Close(); err == nil {
		err = errLock
	}
	return err
}
