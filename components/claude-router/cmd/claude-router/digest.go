package main

import (
	"bufio"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"os"
	"time"

	"github.com/syanga/astack/components/claude-router/internal/claude"
	"github.com/syanga/astack/components/claude-router/internal/router"
)

type usageTotals struct {
	Requests                   int
	Input, Write, Read, Output int64
	Unknown                    int
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

	servedOn   router.AccountID
	moveReason string
}

type accountMove struct {
	At           time.Time
	Conversation string
	From, To     router.AccountID
	Reason       string
	Cost         *int64
}

type cacheWindow struct {
	Since    time.Time
	Accounts map[router.AccountID]*usageTotals
	Moves    []accountMove
}

func (w cacheWindow) moveCost() (known int64, unknown int) {
	for _, m := range w.Moves {
		if m.Cost == nil {
			unknown++
		} else {
			known += *m.Cost
		}
	}
	return known, unknown
}

func (w cacheWindow) writes() int64 {
	var n int64
	for _, u := range w.Accounts {
		n += u.Write
	}
	return n
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
	Cache         cacheWindow
}

func readDigest(path string, since time.Time) (digest, error) {
	d := digest{Accounts: map[router.AccountID]*usageTotals{}, Conversations: map[string]*conversationDigest{},
		Cache: cacheWindow{Since: since, Accounts: map[router.AccountID]*usageTotals{}}}
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
		if e.Conversation != "" {
			d.conversation(e.Conversation).moveReason = e.Reason
		}
	case "request":
		if !e.At.Before(d.Cache.Since) && e.Account != "" {
			d.Cache.count(e)
		}
		if e.Conversation == "" {
			return
		}
		c := d.conversation(e.Conversation)
		d.Cache.track(c, e)
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

func (d *digest) conversation(hash string) *conversationDigest {
	if d.Conversations == nil {
		d.Conversations = map[string]*conversationDigest{}
	}
	c := d.Conversations[hash]
	if c == nil {
		c = &conversationDigest{}
		d.Conversations[hash] = c
	}
	return c
}

func (w *cacheWindow) count(e claude.Event) {
	if e.Usage == nil && e.Status != http.StatusOK {
		return
	}
	if w.Accounts == nil {
		w.Accounts = map[router.AccountID]*usageTotals{}
	}
	u := w.Accounts[e.Account]
	if u == nil {
		u = &usageTotals{}
		w.Accounts[e.Account] = u
	}
	if !windowCounted(e.Usage) {
		u.Unknown++
		return
	}
	u.Requests++
	u.Input += *e.Usage.InputTokens
	u.Write += *e.Usage.CacheCreationInputTokens
	u.Read += *e.Usage.CacheReadInputTokens
	u.Output += val(e.Usage.OutputTokens)
}

func windowCounted(u *claude.Usage) bool {
	return u != nil && u.InputTokens != nil && u.CacheCreationInputTokens != nil && u.CacheReadInputTokens != nil
}

func (w *cacheWindow) track(c *conversationDigest, e claude.Event) {
	if e.Status != http.StatusOK || e.Account == "" {
		return
	}
	if c.servedOn != "" && c.servedOn != e.Account && !e.At.Before(w.Since) {
		reason := c.moveReason
		if reason == "" {
			reason = e.Reason
		}
		m := accountMove{At: e.At, Conversation: e.Conversation, From: c.servedOn, To: e.Account, Reason: reason}
		if windowCounted(e.Usage) {
			cost := *e.Usage.CacheCreationInputTokens
			m.Cost = &cost
		}
		w.Moves = append(w.Moves, m)
	}
	c.servedOn, c.moveReason = e.Account, ""
}

func val(p *int64) int64 {
	if p == nil {
		return 0
	}
	return *p
}
