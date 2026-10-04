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

// ErrFailed reports that an earlier journal write or sync failed. The store
// acknowledges nothing more until it is closed and reopened, because the
// failed record may or may not be on disk.
var ErrFailed = errors.New("assignment journal write failed; reopen to recover")

// ErrIncomplete reports a record without a conversation, account, time, or
// migration source. The store refuses it before writing.
var ErrIncomplete = errors.New("incomplete assignment record")

// ErrUnassigned reports a migration of a conversation that has no assignment.
var ErrUnassigned = errors.New("conversation has no assignment")

// Binding is a conversation's durable assignment.
type Binding struct {
	Account AccountID `json:"account"`
	Reason  Reason    `json:"reason"`
	Since   time.Time `json:"since"`
}

// Store is the durable assignment journal: one writer process, an
// append-only JSON-lines file, and an fsync before a commit returns.
type Store struct {
	mu       sync.RWMutex
	file     journalFile
	lock     *os.File
	bindings map[ConversationID]Binding
	failed   error
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
)

type record struct {
	Op           op             `json:"op"`
	Conversation ConversationID `json:"c"`
	Account      AccountID      `json:"a"`
	From         AccountID      `json:"from,omitempty"`
	Reason       Reason         `json:"why"`
	At           time.Time      `json:"t"`
}

const journalName = "assignments.jsonl"

// OpenStore locks dir against other processes and replays its journal. A torn
// final record, left by a crash during append, is truncated. A corrupt
// interior record fails the open rather than losing assignments.
func OpenStore(dir string) (*Store, error) {
	if err := makeDurableDir(dir); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(dir, "lock"), os.O_RDWR|os.O_CREATE, 0o600)
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
	path := filepath.Join(dir, journalName)
	_, statErr := os.Stat(path)
	file, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		lock.Close()
		return nil, err
	}
	s := &Store{file: file, lock: lock, bindings: map[ConversationID]Binding{}}
	if errors.Is(statErr, os.ErrNotExist) {
		if err := syncDir(dir); err != nil {
			s.Close()
			return nil, err
		}
	}
	if err := s.replay(); err != nil {
		s.Close()
		return nil, err
	}
	return s, nil
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

func (s *Store) replay() error {
	data, err := io.ReadAll(s.file)
	if err != nil {
		return err
	}
	good := 0
	for good < len(data) {
		end := bytes.IndexByte(data[good:], '\n')
		if end < 0 {
			break
		}
		var rec record
		if err := json.Unmarshal(data[good:good+end], &rec); err != nil {
			return fmt.Errorf("corrupt journal record at byte %d: %w", good, err)
		}
		if err := s.apply(rec); err != nil {
			return fmt.Errorf("journal record at byte %d: %w", good, err)
		}
		good += end + 1
	}
	if good < len(data) {
		if err := s.file.Truncate(int64(good)); err != nil {
			return err
		}
		if err := s.file.Sync(); err != nil {
			return err
		}
	}
	_, err = s.file.Seek(int64(good), io.SeekStart)
	return err
}

func (rec record) validate() error {
	switch {
	case rec.Op != opAssign && rec.Op != opMigrate:
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
	}
	return nil
}

func (s *Store) append(rec record) error {
	if s.failed != nil {
		return fmt.Errorf("%w: %v", ErrFailed, s.failed)
	}
	if err := rec.validate(); err != nil {
		return err
	}
	line, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if _, err := s.file.Write(append(line, '\n')); err != nil {
		s.failed = err
		return err
	}
	if err := s.file.Sync(); err != nil {
		s.failed = err
		return err
	}
	return nil
}

// Assign returns the conversation's binding, durably recording proposed as
// its first assignment when it has none. Concurrent callers for one
// conversation receive the same binding.
func (s *Store) Assign(conv ConversationID, proposed AccountID, reason Reason, at time.Time) (Binding, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
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

// Lookup returns the conversation's binding.
func (s *Store) Lookup(conv ConversationID) (Binding, bool) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	b, ok := s.bindings[conv]
	return b, ok
}

// Bindings returns a copy of every binding.
func (s *Store) Bindings() map[ConversationID]Binding {
	s.mu.RLock()
	defer s.mu.RUnlock()
	out := make(map[ConversationID]Binding, len(s.bindings))
	for k, v := range s.bindings {
		out[k] = v
	}
	return out
}

func (s *Store) each(f func(ConversationID, Binding)) {
	s.mu.RLock()
	defer s.mu.RUnlock()
	for k, v := range s.bindings {
		f(k, v)
	}
}

// Close releases the journal and the process lock.
func (s *Store) Close() error {
	err := s.file.Close()
	if errLock := s.lock.Close(); err == nil {
		err = errLock
	}
	return err
}
