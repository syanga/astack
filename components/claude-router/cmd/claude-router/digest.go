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
	lastStart  time.Time
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
	QuotaSince    time.Time
	QuotaSamples  map[windowID][]quotaSample
	RequestSpans  map[router.AccountID][]requestSpan
}

func readDigest(path string, since time.Time) (digest, error) {
	return readDigestAt(path, since, time.Time{})
}

func readDigestAt(path string, since, quotaSince time.Time) (digest, error) {
	d := digest{
		Accounts:      map[router.AccountID]*usageTotals{},
		Conversations: map[string]*conversationDigest{},
		Cache:         cacheWindow{Since: since, Accounts: map[router.AccountID]*usageTotals{}},
		QuotaSince:    quotaSince,
		QuotaSamples:  map[windowID][]quotaSample{},
		RequestSpans:  map[router.AccountID][]requestSpan{},
	}
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

const maxEventLineBytes = 4 << 20

func (d *digest) read(r io.Reader) error {
	br := bufio.NewReaderSize(r, 64<<10)
	var buf []byte
	for {
		chunk, err := br.ReadSlice('\n')
		oversized := false
		if errors.Is(err, bufio.ErrBufferFull) {
			buf = append(buf[:0], chunk...)
			for errors.Is(err, bufio.ErrBufferFull) {
				chunk, err = br.ReadSlice('\n')
				if !oversized && len(buf)+len(chunk) <= maxEventLineBytes {
					buf = append(buf, chunk...)
				} else {
					oversized = true
				}
			}
			chunk = buf
		}
		if !oversized && len(chunk) > 0 {
			var e claude.Event
			if json.Unmarshal(chunk, &e) == nil {
				d.add(e)
			}
		}
		if errors.Is(err, io.EOF) {
			return nil
		}
		if err != nil {
			return err
		}
	}
}

func (d *digest) add(e claude.Event) {
	if e.Account != "" && e.Quota != nil {
		d.addQuota(e.Account, e.At, e.Quota)
	}
	switch e.Kind {
	case "migrated":
		d.Migrations = append(d.Migrations, migration{At: e.At, Conversation: e.Conversation, From: string(e.From), To: string(e.Account), Reason: e.Reason})
		if e.Conversation != "" {
			d.conversation(e.Conversation).moveReason = e.Reason
		}
	case "attempt_failed":
		if e.Account != "" && e.Class != router.ClassNetwork {
			start := e.At
			if !e.AttemptStart.IsZero() && e.AttemptStart.Before(start) {
				start = e.AttemptStart
			}
			d.recordRequestSpan(e.Account, start, e.At, e.Quota, e.Model)
		}
	case "request":
		if e.Account != "" && requestHitUpstream(e) {
			start := e.AttemptStart
			if start.IsZero() {
				start = requestStart(e)
			}
			d.recordRequestSpan(e.Account, start, e.At, e.Quota, e.Model)
		}
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
	start := requestStart(e)
	if c.servedOn != "" && c.servedOn != e.Account && !e.At.Before(w.Since) {
		reason := c.moveReason
		if reason == "" {
			reason = e.Reason
		}
		m := accountMove{At: e.At, Conversation: e.Conversation, From: c.servedOn, To: e.Account, Reason: reason}
		switch {
		case reason == string(router.ReasonIdle) && start.Sub(c.lastStart) > longestCacheLifetime:
			m.Cost = new(int64)
		case windowCounted(e.Usage):
			cost := *e.Usage.CacheCreationInputTokens
			m.Cost = &cost
		}
		w.Moves = append(w.Moves, m)
	}
	if start.After(c.lastStart) {
		c.lastStart = start
	}
	c.servedOn, c.moveReason = e.Account, ""
}

const longestCacheLifetime = time.Hour

func requestStart(e claude.Event) time.Time {
	if e.DurationMS == nil {
		return e.At
	}
	return e.At.Add(-time.Duration(*e.DurationMS) * time.Millisecond)
}

func val(p *int64) int64 {
	if p == nil {
		return 0
	}
	return *p
}

func (d *digest) addQuota(account router.AccountID, fallback time.Time, q *claude.UsageReading) {
	at := q.At
	if at.IsZero() {
		at = fallback
	}
	if !d.QuotaSince.IsZero() && at.Before(d.QuotaSince) {
		return
	}
	if d.QuotaSamples == nil {
		d.QuotaSamples = map[windowID][]quotaSample{}
	}
	for _, w := range q.Windows {
		if w.Used == nil || w.Kind.Period() == 0 {
			continue
		}
		id := windowID{Account: account, Kind: w.Kind, Model: w.Model}
		d.QuotaSamples[id] = append(d.QuotaSamples[id], quotaSample{
			At:       at,
			Used:     min(max(*w.Used, 0), 1),
			ResetsAt: w.ResetsAt,
		})
	}
}

func requestHitUpstream(e claude.Event) bool {
	if e.Status == http.StatusOK || e.Quota != nil {
		return true
	}
	switch e.Outcome {
	case "completed", "incomplete", "error_after_output", "served_mark_failed":
		return true
	}
	return false
}

func (d *digest) recordRequestSpan(account router.AccountID, start, end time.Time, q *claude.UsageReading, model string) {
	if end.Before(start) {
		end = start
	}
	if q != nil && !q.At.IsZero() {
		if q.At.Before(start) {
			start = q.At
		}
		if q.At.After(end) {
			end = q.At
		}
	}
	d.addRequestSpan(account, start, end, model)
}

func (d *digest) addRequestSpan(account router.AccountID, start, end time.Time, model string) {
	if !d.QuotaSince.IsZero() && end.Before(d.QuotaSince) {
		return
	}
	if d.RequestSpans == nil {
		d.RequestSpans = map[router.AccountID][]requestSpan{}
	}
	d.RequestSpans[account] = append(d.RequestSpans[account], requestSpan{Start: start, End: end, Model: model})
}
