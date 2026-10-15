package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"io"
	"os"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

type usageTotals struct {
	Requests                   int
	Input, Write, Read, Output int64
}

func (u usageTotals) readShare() (int, bool) {
	total := u.Input + u.Write + u.Read
	if total == 0 {
		return 0, false
	}
	return int(u.Read * 100 / total), true
}

type conversationDigest struct {
	Last     time.Time
	Decision router.Kind
	Reason   string
	Since    time.Time
	Until    time.Time
	Context  int64
}

type migration struct {
	At               time.Time
	Conversation     string
	From, To, Reason string
}

type digest struct {
	Accounts      map[router.AccountID]*usageTotals
	Conversations map[string]*conversationDigest
	Migrations    []migration
}

func readDigest(path string) (digest, error) {
	d := digest{Accounts: map[router.AccountID]*usageTotals{}, Conversations: map[string]*conversationDigest{}}
	f, err := os.Open(path)
	if errors.Is(err, os.ErrNotExist) {
		return d, nil
	}
	if err != nil {
		return d, err
	}
	defer f.Close()
	return d, d.read(f)
}

func (d *digest) read(r io.Reader) error {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64<<10), 4<<20)
	for sc.Scan() {
		var e claude.Event
		if json.Unmarshal(sc.Bytes(), &e) != nil {
			continue
		}
		d.add(e)
	}
	return sc.Err()
}

func (d *digest) add(e claude.Event) {
	switch e.Kind {
	case "migrated":
		d.Migrations = append(d.Migrations, migration{At: e.At, Conversation: e.Conversation, From: string(e.From), To: string(e.Account), Reason: e.Reason})
	case "request":
		if e.Conversation == "" {
			return
		}
		c := d.Conversations[e.Conversation]
		if c == nil {
			c = &conversationDigest{}
			d.Conversations[e.Conversation] = c
		}
		if c.Decision != e.Decision || c.Since.IsZero() {
			c.Since = e.At
		}
		c.Last, c.Decision, c.Reason, c.Until = e.At, e.Decision, e.Reason, e.Until
		if e.Usage == nil || e.Account == "" {
			return
		}
		u := d.Accounts[e.Account]
		if u == nil {
			u = &usageTotals{}
			d.Accounts[e.Account] = u
		}
		u.Requests++
		in, wr, rd, out := val(e.Usage.InputTokens), val(e.Usage.CacheCreationInputTokens), val(e.Usage.CacheReadInputTokens), val(e.Usage.OutputTokens)
		u.Input += in
		u.Write += wr
		u.Read += rd
		u.Output += out
		c.Context = in + wr + rd
	}
}

func val(p *int64) int64 {
	if p == nil {
		return 0
	}
	return *p
}
