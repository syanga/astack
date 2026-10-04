# Claude router

An optional local proxy that routes Claude conversations across subscription
accounts. The behavior is specified in
[docs/specs/claude-account-routing.md](../../docs/specs/claude-account-routing.md).
[CONTRACT.md](CONTRACT.md) records what the pinned SDK guarantees, the routing
policy, and which contracts are still open.

The module has three parts. None of them serves clients yet.

| Path | Contents |
| --- | --- |
| `probes/` | Feasibility probes. They embed the pinned CLIProxyAPI SDK, serve two or more fake accounts from an in-process fake Anthropic API, and refuse every other host. |
| `internal/router/` | The production routing policy and the durable assignment journal. It uses only the standard library. |
| `cmd/claude-router-sim/` | A simulator that drives `internal/router` with synthetic workloads, scripted fixtures, crash tests, and a performance workload. |

You need Go 1.26.0 or later. Download the pinned dependencies once, with
network access:

```sh
go mod download
```

## Run the tests

Run every test offline:

```sh
GOPROXY=off GOSUMDB=off go test -race ./...
```

To save a sanitized JSON transcript per probe test, set
`CLAUDE_ROUTER_PROBE_EVIDENCE` to a directory. Transcripts hold account labels,
token hash prefixes, statuses, event names, and timing. They hold no tokens and
no prompts.

The configuration hot-reload probe is excluded by default because the pinned SDK
races on that path. Run it with `-tags sdkreload`.

## Use the routing policy

`router.OpenStore(dir)` locks the state directory and replays its journal.
`router.New(router.DefaultConfig(), accounts, store)` returns a `Router`. For
each request, call `Route(now, request)` and dispatch only on the returned
`Decision`:

- `place`, `dispatch`, `migrate`, and `retry` name the account to pin the
  request to. `Route` has already synced any new assignment to disk.
- `wait` gives the earliest usable reset in `Until`.
- `refuse`, `reauth`, `fail`, `unavailable`, and `reject` are answered
  locally. `RecheckOverage` asks for a fresh read of the paid-overflow
  setting.

If `Route` returns an error wrapping `router.ErrFailed`, a journal write
failed. Every call that could name an account fails the same way until you
close the store and open it again.

Feed the router what it cannot see:

- `Observe` for the quota headers of one response, with the start time of
  the attempt that produced it.
- `Report` for a failure before output. Pass the class it returns as
  `LastFailure` on the next attempt.
- `ObserveOverage` for each paid-overflow check or observed paid use.
- `Served` after the first successful response on an assignment.
- `Relogin` after a browser login, and `Move` for a manual override.

CONTRACT.md's PR2 section gives the placement rule, the defaults, and the
journal rules.

## Run the simulator

Build it once:

```sh
GOPROXY=off GOSUMDB=off go build -o claude-router-sim ./cmd/claude-router-sim
```

| Command | What it does |
| --- | --- |
| `claude-router-sim check -scripts testdata/scripts` | Runs each scripted fixture under both placement rules and compares every decision with the fixture's hand-derived expectation. Exits 1 on a mismatch. |
| `claude-router-sim run -fixture testdata/sim/mixed-48h.json -seed 1 -out run.json` | Simulates one workload and writes metrics and the routing trace. The same seed and fixture give byte-identical output. |
| `claude-router-sim compare -fixtures A.json,B.json -seeds 1,2,3 -out comparison.json` | Runs capacity-only and a sweep of reset-aware variants on the same workloads. |
| `claude-router-sim serve -state DIR` | Reads routing commands from stdin, one per line, and prints `ACK` after each durable commit. The crash test in `cmd/claude-router-sim/crash_test.go` drives it with SIGKILL. |
| `claude-router-sim perf -state DIR` | Places 10,000 conversations on 100 accounts, sends 10,000 more requests, and reports decision and commit latency. `recover -state DIR` times a cold reopen of that state. |

Every quota, cost, and cache quantity in `testdata/sim/` is synthetic. Each
fixture labels its assumptions in its `assumptions` field, and every result
copies them.
