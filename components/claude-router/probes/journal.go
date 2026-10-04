package probes

import (
	"bufio"
	"bytes"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"os"
	"path/filepath"
	"sync"
	"syscall"
)

// ErrJournalLocked reports that another process holds the journal.
var ErrJournalLocked = errors.New("assignment journal is held by another process")

// Journal is the storage design PR1 selects for durable assignments: one
// writer process, an append-only JSON-lines file, and an fsync before an
// assignment is acknowledged. This copy exists to encode the crash
// experiments; PR2 owns the production implementation.
type Journal struct {
	mu       sync.Mutex
	file     *os.File
	lock     *os.File
	assigned map[string]string
}

type journalRecord struct {
	Conversation string `json:"c"`
	Account      string `json:"a"`
}

// OpenJournal takes an exclusive lock on dir, replays the journal, and
// truncates a torn final record left by a crash during append.
func OpenJournal(dir string) (*Journal, error) {
	if err := os.MkdirAll(dir, 0o700); err != nil {
		return nil, err
	}
	lock, err := os.OpenFile(filepath.Join(dir, "lock"), os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		return nil, err
	}
	if err := syscall.Flock(int(lock.Fd()), syscall.LOCK_EX|syscall.LOCK_NB); err != nil {
		lock.Close()
		if errors.Is(err, syscall.EWOULDBLOCK) {
			return nil, ErrJournalLocked
		}
		return nil, err
	}
	path := filepath.Join(dir, "assignments.jsonl")
	_, statErr := os.Stat(path)
	file, err := os.OpenFile(path, os.O_RDWR|os.O_CREATE, 0o600)
	if err != nil {
		lock.Close()
		return nil, err
	}
	j := &Journal{file: file, lock: lock, assigned: map[string]string{}}
	if errors.Is(statErr, os.ErrNotExist) {
		if err := syncDir(dir); err != nil {
			j.Close()
			return nil, err
		}
	}
	if err := j.replay(); err != nil {
		j.Close()
		return nil, err
	}
	return j, nil
}

func syncDir(dir string) error {
	d, err := os.Open(dir)
	if err != nil {
		return err
	}
	defer d.Close()
	return d.Sync()
}

func (j *Journal) replay() error {
	data, err := io.ReadAll(j.file)
	if err != nil {
		return err
	}
	good := 0
	for good < len(data) {
		end := bytes.IndexByte(data[good:], '\n')
		if end < 0 {
			break
		}
		var rec journalRecord
		if err := json.Unmarshal(data[good:good+end], &rec); err != nil {
			return fmt.Errorf("corrupt journal record at byte %d: %w", good, err)
		}
		if _, exists := j.assigned[rec.Conversation]; !exists {
			j.assigned[rec.Conversation] = rec.Account
		}
		good += end + 1
	}
	if good < len(data) {
		if err := j.file.Truncate(int64(good)); err != nil {
			return err
		}
		if err := j.file.Sync(); err != nil {
			return err
		}
	}
	_, err = j.file.Seek(int64(good), io.SeekStart)
	return err
}

// Assign returns the conversation's existing account, or durably records
// proposed as its first assignment and returns it. Concurrent callers for one
// conversation receive the same account.
func (j *Journal) Assign(conversation, proposed string) (string, error) {
	j.mu.Lock()
	defer j.mu.Unlock()
	if account, ok := j.assigned[conversation]; ok {
		return account, nil
	}
	line, err := json.Marshal(journalRecord{Conversation: conversation, Account: proposed})
	if err != nil {
		return "", err
	}
	if _, err := j.file.Write(append(line, '\n')); err != nil {
		return "", err
	}
	if err := j.file.Sync(); err != nil {
		return "", err
	}
	j.assigned[conversation] = proposed
	return proposed, nil
}

// Lookup returns the recorded account for a conversation.
func (j *Journal) Lookup(conversation string) (string, bool) {
	j.mu.Lock()
	defer j.mu.Unlock()
	account, ok := j.assigned[conversation]
	return account, ok
}

// Len returns the number of recorded conversations.
func (j *Journal) Len() int {
	j.mu.Lock()
	defer j.mu.Unlock()
	return len(j.assigned)
}

// Close releases the file and the process lock.
func (j *Journal) Close() error {
	err := j.file.Close()
	if errLock := j.lock.Close(); err == nil {
		err = errLock
	}
	return err
}

// JournalPath returns the journal file inside dir.
func JournalPath(dir string) string { return filepath.Join(dir, "assignments.jsonl") }

// ReadAcks parses "ACK <conversation> <account>" lines written by a journal
// writer after each Assign returned.
func ReadAcks(r io.Reader) map[string][]string {
	acks := map[string][]string{}
	sc := bufio.NewScanner(r)
	for sc.Scan() {
		var conv, account string
		if n, _ := fmt.Sscanf(sc.Text(), "ACK %s %s", &conv, &account); n == 2 {
			acks[conv] = append(acks[conv], account)
		}
	}
	return acks
}
