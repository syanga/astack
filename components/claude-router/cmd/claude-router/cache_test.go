package main

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

var cacheNow = time.Date(2026, 10, 15, 23, 5, 0, 0, pacific)

func ago(d time.Duration) time.Time { return cacheNow.Add(-d) }

func counters(input, write, read int64) string {
	return fmt.Sprintf(`{"input_tokens":%d,"output_tokens":10,"cache_creation_input_tokens":%d,"cache_read_input_tokens":%d}`, input, write, read)
}

func served(at time.Time, conv, account, usage string) string {
	line := fmt.Sprintf(`{"at":%q,"kind":"request","conversation":%q,"account":%q,"decision":"dispatch","status":200`,
		at.Format(time.RFC3339Nano), conv, account)
	if usage != "" {
		line += `,"usage":` + usage
	}
	return line + "}\n"
}

func movedEvent(at time.Time, conv, from, to, reason string) string {
	return fmt.Sprintf(`{"at":%q,"kind":"migrated","conversation":%q,"account":%q,"from":%q,"reason":%q}`+"\n",
		at.Format(time.RFC3339Nano), conv, to, from, reason)
}

func writeState(t *testing.T, accounts []string, extra string, events string) installation {
	t.Helper()
	state := t.TempDir()
	var list []string
	for _, a := range accounts {
		list = append(list, fmt.Sprintf(`{"id":%q,"capacity":1}`, a))
	}
	config := fmt.Sprintf(`{"listen":"127.0.0.1:18787","state_dir":%q,"client_token_file":"client-token","accounts":[%s]%s}`,
		state, strings.Join(list, ","), extra)
	if err := os.WriteFile(filepath.Join(state, "config.json"), []byte(config), 0o600); err != nil {
		t.Fatal(err)
	}
	if events != "" {
		if err := os.WriteFile(filepath.Join(state, "events.jsonl"), []byte(events), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	in, err := loadInstallation(state)
	if err != nil {
		t.Fatal(err)
	}
	return in
}

func cacheLines(t *testing.T, events string) string {
	t.Helper()
	in := writeState(t, []string{"work", "alumni"}, "", events)
	v, err := cacheReport(in.State, cacheNow, 24*time.Hour)
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	writeCache(&out, v)
	return out.String()
}

func wantLines(t *testing.T, got string, want ...string) {
	t.Helper()
	for _, w := range want {
		if !strings.Contains(got, w+"\n") {
			t.Fatalf("cache output lacks %q:\n%s", w, got)
		}
	}
}

func TestTheFirstRequestOfAConversationIsAPlacementNotAMove(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(100, 6000, 0))+
			served(ago(2*time.Hour), "bbbbbbbbbbbb", "alumni", counters(100, 2000, 0))+
			served(ago(time.Hour), "aaaaaaaaaaaa", "work", counters(50, 1000, 5000)))

	wantLines(t, got,
		"work      hit 41%  input 150  write 7k  read 5k",
		"alumni    hit  0%  input 100  write 2k  read  0",
		"moves     0  cost 0  of 9k writes  share 0.0%")
	if strings.Contains(got, "reasons") {
		t.Fatalf("cache output shows reasons without a move:\n%s", got)
	}
}

func TestAPolicyMoveCostsTheCacheWriteOfItsFirstRequestOnTheNewAccount(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(100, 6000, 0))+
			movedEvent(ago(2*time.Hour), "aaaaaaaaaaaa", "work", "alumni", "exhausted")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", counters(100, 3000, 0))+
			served(ago(time.Hour), "aaaaaaaaaaaa", "alumni", counters(100, 1000, 3000)))

	wantLines(t, got,
		"moves     1  cost 3k  of 10k writes  share 30.0%",
		"reasons   exhausted 1")
}

func TestAMoveOffARemovedAccountCountsAsAMove(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "school", counters(100, 6000, 0))+
			movedEvent(ago(2*time.Hour), "aaaaaaaaaaaa", "school", "alumni", "not_enrolled")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", counters(100, 3000, 0)))

	wantLines(t, got,
		"moves     1  cost 3k  of 9k writes  share 33.3%",
		"reasons   not_enrolled 1")
}

func TestAManualMoveShowsTheReasonOfItsMigratedEvent(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(100, 6000, 0))+
			movedEvent(ago(150*time.Minute), "aaaaaaaaaaaa", "work", "alumni", "manual")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", counters(100, 3000, 0))+
			served(ago(time.Hour), "aaaaaaaaaaaa", "alumni", counters(100, 1000, 3000)))

	wantLines(t, got,
		"moves     1  cost 3k  of 10k writes  share 30.0%",
		"reasons   manual 1")
}

func TestAMoveWhoseRequestHasNoUsageKeepsAnUnknownCostAfterALaterWrite(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0))+
			movedEvent(ago(2*time.Hour), "aaaaaaaaaaaa", "work", "alumni", "manual")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", "")+
			served(ago(time.Hour), "aaaaaaaaaaaa", "alumni", counters(0, 2000, 0)))

	wantLines(t, got,
		"alumni    hit 0%  input 0  write 2k  read 0  + 1 unknown",
		"moves     1  cost 0 + 1 unknown  of 8k writes  share 0.0% or more")
}

func TestAMoveWhoseRequestHasANullCacheWriteKeepsAnUnknownCostAfterALaterWrite(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0))+
			movedEvent(ago(2*time.Hour), "aaaaaaaaaaaa", "work", "alumni", "manual")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", `{"input_tokens":0,"cache_creation_input_tokens":null,"cache_read_input_tokens":0}`)+
			served(ago(time.Hour), "aaaaaaaaaaaa", "alumni", counters(0, 2000, 0)))

	wantLines(t, got,
		"alumni    hit 0%  input 0  write 2k  read 0  + 1 unknown",
		"moves     1  cost 0 + 1 unknown  of 8k writes  share 0.0% or more")
}

func TestAMoveCountsItsCostOnlyWhenItsRequestCountsTowardAllWrites(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 1000, 0))+
			movedEvent(ago(2*time.Hour), "aaaaaaaaaaaa", "work", "alumni", "exhausted")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", `{"input_tokens":null,"cache_creation_input_tokens":5000,"cache_read_input_tokens":0}`))

	wantLines(t, got,
		"alumni    hit --  input 0  write  0  read 0  + 1 unknown",
		"moves     1  cost 0 + 1 unknown  of 1k writes  share 0.0% or more")
}

func TestAFailedRequestWithoutUsageAddsNoAccountLine(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0))+
			fmt.Sprintf(`{"at":%q,"kind":"request","conversation":"bbbbbbbbbbbb","account":"personal","status":503}`+"\n", ago(2*time.Hour).Format(time.RFC3339)))

	want := "window    last 24h, 23:05 PDT yesterday to now\n" +
		"work      hit 0%  input 0  write 6k  read 0\n" +
		"alumni    hit --  input 0  write  0  read 0\n" +
		"moves     0  cost 0  of 6k writes  share 0.0%\n"
	if got != want {
		t.Fatalf("cache after a failed request without usage:\n%s\nwant:\n%s", got, want)
	}
}

func TestCacheIgnoresAMalformedServiceRecord(t *testing.T) {
	now := time.Now()
	in := writeState(t, []string{"work"}, "", served(now.Add(-time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0)))
	if err := os.WriteFile(filepath.Join(in.State, serviceRecord), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}

	var err error
	got := stdoutOf(t, func() { err = cacheCommand([]string{"-state", in.State}) })

	if err != nil || !strings.Contains(got, "work      hit 0%  input 0  write 6k  read 0\n") {
		t.Fatalf("cache with a malformed %s returned %v:\n%s", serviceRecord, err, got)
	}
}

func TestAMoveWithNoUsageAfterItHasAnUnknownCost(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0))+
			movedEvent(ago(2*time.Hour), "aaaaaaaaaaaa", "work", "alumni", "manual")+
			served(ago(2*time.Hour), "aaaaaaaaaaaa", "alumni", ""))

	wantLines(t, got,
		"alumni    hit --  input 0  write  0  read 0  + 1 unknown",
		"moves     1  cost 0 + 1 unknown  of 6k writes  share 0.0% or more",
		"reasons   manual 1")
}

func TestOnlyMovesWhoseRequestFallsInTheWindowCount(t *testing.T) {
	outside, inside := ago(24*time.Hour+time.Minute), ago(24*time.Hour-time.Minute)
	got := cacheLines(t,
		served(ago(30*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0))+
			movedEvent(outside, "aaaaaaaaaaaa", "work", "alumni", "exhausted")+
			served(outside, "aaaaaaaaaaaa", "alumni", "")+
			served(ago(23*time.Hour), "aaaaaaaaaaaa", "alumni", counters(0, 4000, 0))+
			served(ago(30*time.Hour), "bbbbbbbbbbbb", "work", counters(0, 5000, 0))+
			movedEvent(inside, "bbbbbbbbbbbb", "work", "alumni", "manual")+
			served(inside, "bbbbbbbbbbbb", "alumni", counters(0, 2000, 0)))

	wantLines(t, got,
		"moves     1  cost 2k  of 6k writes  share 33.3%",
		"reasons   manual 1")
}

func TestTwoConversationsThatMoveAtTheSameTimeEachCountTheirOwnCost(t *testing.T) {
	at := ago(time.Hour)
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 4000, 0))+
			served(ago(3*time.Hour), "bbbbbbbbbbbb", "work", counters(0, 4000, 0))+
			movedEvent(at, "aaaaaaaaaaaa", "work", "alumni", "exhausted")+
			movedEvent(at, "bbbbbbbbbbbb", "work", "alumni", "exhausted")+
			served(at, "aaaaaaaaaaaa", "alumni", counters(0, 3000, 0))+
			served(at, "bbbbbbbbbbbb", "alumni", counters(0, 1000, 0)))

	wantLines(t, got,
		"moves     2  cost 4k  of 12k writes  share 33.3%",
		"reasons   exhausted 2")
}

func TestAFailedRequestOnAnotherAccountIsNotAMove(t *testing.T) {
	got := cacheLines(t,
		served(ago(3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 6000, 0))+
			fmt.Sprintf(`{"at":%q,"kind":"request","conversation":"aaaaaaaaaaaa","account":"alumni","status":503}`+"\n", ago(2*time.Hour).Format(time.RFC3339))+
			fmt.Sprintf(`{"at":%q,"kind":"request","conversation":"aaaaaaaaaaaa","status":429}`+"\n", ago(2*time.Hour).Format(time.RFC3339))+
			served(ago(time.Hour), "aaaaaaaaaaaa", "work", counters(0, 1000, 5000)))

	wantLines(t, got, "moves     0  cost 0  of 7k writes  share 0.0%")
}

func TestCacheOfAnEmptyLogShowsEachAccountWithoutAShare(t *testing.T) {
	got := cacheLines(t, "")

	want := "window    last 24h, 23:05 PDT yesterday to now\n" +
		"work      hit --  input 0  write 0  read 0\n" +
		"alumni    hit --  input 0  write 0  read 0\n" +
		"moves     0  cost 0  of 0 writes  share --\n"
	if got != want {
		t.Fatalf("cache of an empty log:\n%s\nwant:\n%s", got, want)
	}
}

func layoutEvents() string {
	var b strings.Builder
	accounts := []string{"work", "alumni", "personal"}
	for i, a := range accounts {
		conv := fmt.Sprintf("%012x", i+1)
		b.WriteString(served(ago(20*time.Hour), conv, a, counters(6000, 1_700_000, 0)))
		b.WriteString(served(ago(19*time.Hour), conv, a, counters(12_000, 300_000, 41_200_000)))
	}
	moves := []struct {
		conv, from, to, reason string
		cost                   int64
	}{
		{"87ef7f9f6285", "work", "alumni", "exhausted", 104_000},
		{"3d79daedf017", "work", "alumni", "exhausted", 98_000},
		{"e91f675b4f8a", "personal", "alumni", "exhausted", 114_000},
		{"9a41c07e2b13", "alumni", "work", "manual", 96_000},
	}
	for i, m := range moves {
		at := ago(time.Duration(10-i) * time.Hour)
		b.WriteString(served(at.Add(-time.Hour), m.conv, m.from, counters(0, 0, 50_000)))
		b.WriteString(movedEvent(at, m.conv, m.from, m.to, m.reason))
		b.WriteString(served(at, m.conv, m.to, counters(0, m.cost, 0)))
	}
	return b.String()
}

func TestCacheMatchesTheSelectedLayout(t *testing.T) {
	last := served(ago(time.Hour), "9a41c07e2b13", "alumni", "")
	for _, tc := range []struct {
		fixture string
		events  string
	}{
		{"moves", layoutEvents()},
		{"unknown", layoutEvents() + movedEvent(ago(time.Hour), "9a41c07e2b13", "work", "alumni", "manual") + last},
	} {
		t.Run(tc.fixture, func(t *testing.T) {
			in := writeState(t, []string{"work", "alumni", "personal"}, "", tc.events)
			v, err := cacheReport(in.State, cacheNow, 24*time.Hour)
			if err != nil {
				t.Fatal(err)
			}
			var got bytes.Buffer
			writeCache(&got, v)
			want, err := os.ReadFile(filepath.Join("testdata", "cache", tc.fixture+".txt"))
			if err != nil {
				t.Fatal(err)
			}
			if got.String() != string(want) {
				t.Fatalf("cache output differs from %s.txt\n--- got\n%s--- want\n%s", tc.fixture, got.String(), want)
			}
			for _, line := range strings.Split(strings.TrimSuffix(got.String(), "\n"), "\n") {
				if len(line) > 80 {
					t.Fatalf("line of %d columns: %q", len(line), line)
				}
			}
		})
	}
}

func runningStatus(t *testing.T, extra string, events string) string {
	t.Helper()
	token := strings.Repeat("fake-client-token-", 2)
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.Header.Get("Authorization") != "Bearer "+token {
			w.WriteHeader(http.StatusUnauthorized)
			return
		}
		st := sampleStatus()
		st.Accounts = healthyReport().Status.Accounts
		json.NewEncoder(w).Encode(st)
	}))
	t.Cleanup(srv.Close)
	in := writeState(t, []string{"work", "alumni"}, extra, events)
	config, err := os.ReadFile(in.Config)
	if err != nil {
		t.Fatal(err)
	}
	config = bytes.Replace(config, []byte("127.0.0.1:18787"), []byte(srv.Listener.Addr().String()), 1)
	for name, data := range map[string][]byte{"client-token": []byte(token + "\n"), "config.json": config} {
		if err := os.WriteFile(filepath.Join(in.State, name), data, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	r, err := statusReport(in, cacheNow)
	if err != nil {
		t.Fatal(err)
	}
	var out bytes.Buffer
	writeStatus(&out, r, false)
	return out.String()
}

func movesWithShare(now time.Time, costPercent int64) string {
	return served(now.Add(-3*time.Hour), "aaaaaaaaaaaa", "work", counters(0, 100_000-costPercent*1000, 0)) +
		movedEvent(now.Add(-2*time.Hour), "aaaaaaaaaaaa", "work", "alumni", "exhausted") +
		served(now.Add(-2*time.Hour), "aaaaaaaaaaaa", "alumni", counters(0, costPercent*1000, 0))
}

func TestStatusWarnsAboveTheDefaultMoveShareOfTenPercent(t *testing.T) {
	over := runningStatus(t, "", movesWithShare(cacheNow, 11))
	at := runningStatus(t, "", movesWithShare(cacheNow, 10))

	warning := "cache   account moves wrote 11.0% of all cache writes in 24h, over 10%\nnext    claude-router cache\n"
	if !strings.HasSuffix(over, warning) {
		t.Fatalf("status at an 11%% move share:\n%s\nwant it to end with:\n%s", over, warning)
	}
	if strings.Contains(at, "account moves wrote") || strings.Contains(at, "next") {
		t.Fatalf("status at a 10%% move share shows a warning:\n%s", at)
	}
}

func TestTheStatusWarningSaysOrMoreWhenAMoveCostIsUnknown(t *testing.T) {
	got := runningStatus(t, "", movesWithShare(cacheNow, 14)+
		served(cacheNow.Add(-3*time.Hour), "bbbbbbbbbbbb", "work", counters(0, 0, 0))+
		movedEvent(cacheNow.Add(-time.Hour), "bbbbbbbbbbbb", "work", "alumni", "manual")+
		served(cacheNow.Add(-time.Hour), "bbbbbbbbbbbb", "alumni", ""))

	warning := "cache   account moves wrote 14.0% or more of all cache writes in 24h, over 10%\n"
	if !strings.Contains(got, warning) {
		t.Fatalf("status with one uncosted move:\n%s\nwant the line:\n%s", got, warning)
	}
	for _, line := range strings.Split(strings.TrimSuffix(got, "\n"), "\n") {
		if len(line) > 80 {
			t.Fatalf("line of %d columns: %q", len(line), line)
		}
	}
}

func TestAMoveCacheWarnPercentOfZeroTurnsTheWarningOff(t *testing.T) {
	off := runningStatus(t, `,"move_cache_warn_percent":0`, movesWithShare(cacheNow, 60))
	on := runningStatus(t, `,"move_cache_warn_percent":50`, movesWithShare(cacheNow, 60))

	if strings.Contains(off, "account moves wrote") {
		t.Fatalf("status with move_cache_warn_percent 0 shows a warning:\n%s", off)
	}
	if !strings.Contains(on, "cache   account moves wrote 60.0% of all cache writes in 24h, over 50%\n") {
		t.Fatalf("status with move_cache_warn_percent 50 at a 60%% share shows no warning:\n%s", on)
	}
}

func TestStatusAndServeRefuseAMoveCacheWarnPercentOutsideZeroToHundred(t *testing.T) {
	in := writeState(t, []string{"work"}, `,"move_cache_warn_percent":101`, "")

	r, err := statusReport(in, cacheNow)
	var out bytes.Buffer
	writeStatus(&out, r, false)
	done := make(chan error, 1)
	go func() { done <- serve([]string{"-config", in.Config}) }()
	var refused error
	select {
	case refused = <-done:
	case <-time.After(10 * time.Second):
		t.Fatal("serve with move_cache_warn_percent 101 is still running after 10 s, want a refusal")
	}

	const named = "move_cache_warn_percent: 101 is outside 0 to 100"
	if err != nil || !strings.Contains(out.String(), "        config error: "+named+"\n") {
		t.Fatalf("status %v:\n%s\nwant a config error naming %q", err, out.String(), named)
	}
	if refused == nil || !strings.Contains(refused.Error(), named) {
		t.Fatalf("serve returned %v, want a refusal naming %q", refused, named)
	}
}
