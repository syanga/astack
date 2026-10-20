# Claude router

An optional local proxy that routes Claude conversations across subscription
accounts. The behavior is specified in
[docs/specs/claude-account-routing.md](../../docs/specs/claude-account-routing.md).
[CONTRACT.md](CONTRACT.md) records what the pinned SDK guarantees, the routing
policy, and which contracts are still open.

The module has five parts.

| Path | Contents |
| --- | --- |
| `cmd/claude-router/` | The `claude-router` command: `serve`, `login`, and `token`. |
| `internal/claude/` | The local service. It embeds the pinned CLIProxyAPI SDK, authenticates clients, binds conversations through `internal/router`, and pins every upstream attempt to the bound account. |
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

Three harnesses run behind build tags. `-tags lane2` runs PR3.live.2 against
the compiled command, `-tags perf` runs PR3.perf, and `-tags perfrecover`
runs one PR4.perf run; each file's header gives its command.

The configuration hot-reload probe is excluded by default because the pinned SDK
races on that path. Run it with `-tags sdkreload`.

## Run the service

The service is not yet installed as a background service and is not the
default for any client. These steps run it by hand.

1. Build the command:

   ```sh
   GOPROXY=off GOSUMDB=off go build -o claude-router ./cmd/claude-router
   ```

2. Create a state directory and a client token:

   ```sh
   mkdir -m 700 /path/to/state
   ./claude-router token -out /path/to/state/client-token
   ```

3. Enroll each account with a browser login. The router must not be running
   on that state directory. Each login writes `auths/<id>.json`:

   ```sh
   ./claude-router login -state /path/to/state -account acct-a
   ```

4. Write a configuration file:

   ```json
   {
     "listen": "127.0.0.1:8787",
     "state_dir": "/path/to/state",
     "client_token_file": "/path/to/state/client-token",
     "accounts": [{"id": "acct-a", "capacity": 5}, {"id": "acct-b", "capacity": 1}]
   }
   ```

   `capacity` is the account's relative allowance, such as 1 for Pro and 5
   for Max 5x. `overage_fresh_for` (default `30m`) and `overage_check_every`
   (default `10m`) set how long a paid-overflow reading lasts and how often
   the service reads it. `max_upstream_attempts` (1 to 4, default 4) caps the
   upstream attempts of one client request; 1 turns off router retries.

5. Start the service:

   ```sh
   ./claude-router serve -config /path/to/config.json
   ```

6. Point a client at it with `ANTHROPIC_BASE_URL=http://127.0.0.1:8787` and
   the client token as `ANTHROPIC_AUTH_TOKEN` or `ANTHROPIC_API_KEY`. Set
   `CLAUDE_CODE_RETRY_WATCHDOG=1` in the client's environment so that it
   waits out a long reset instead of stopping.

To change the configuration or a credential, stop the service, make the
change, and start it again. The service never reloads either while it runs.

The service dispatches to an account only while it holds a reading, at most
30 minutes old, that the account's paid overflow is disabled. It stops
routing to an account as soon as a response shows paid use. One gap remains:
if usage credits are turned on outside the router after the last reading,
while the account's included windows are exhausted, one paid response can
happen before the router sees it.

When an account's included allowance runs out before a response starts,
the router moves the conversation to another eligible account and answers
the same request from there. When no account can serve, it answers with a
429 that carries the earliest reset at which an account can serve again,
and the client sleeps until then and sends again. A conversation never
returns to its first account on its own. When an account needs a new
login, its conversations get an error that names `claude-router login`,
and new conversations go elsewhere. To move one by hand, send
`POST /claude-router/move` with the client token and
`{"conversation": "<session id>", "to": "<account>"}`. Exhaustion, login,
and paid-overflow state survive a restart.

`GET /claude-router/status` with the client token reports each account's
registration, last reading, login requirement, and known rejections. `events.jsonl` in the state directory records
requests, failures, readings, and token counters, without tokens or prompts.
CONTRACT.md's PR3 section lists every local answer and event.

## Use the routing policy

`router.OpenStore(dir)` locks the state directory and replays its journal.
`router.New(router.DefaultConfig(), accounts, store)` returns a `Router`
that keeps account state in memory. `router.Open(cfg, accounts, store, now)`
also loads and saves it in the state directory, as the service does. For
each request, call `Route(now, request)` and dispatch only on the returned
`Decision`:

- `place`, `dispatch`, `migrate`, and `retry` name the account to pin the
  request to. `Route` has already synced any new assignment to disk.
- `wait` gives the earliest usable reset in `Until`.
- `refuse`, `reauth`, `fail`, `unavailable`, and `reject` are answered
  locally. `RecheckOverage` asks for a fresh read of the paid-overflow
  setting.

If `Route` returns an error wrapping `router.ErrFailed`, a journal write, or
a write of the account state of a router made by `Open`, failed. Every call that could name an account fails the same way until you
close the store and open it again.

Feed the router what it cannot see:

- `Observe` for the quota headers of one response, with the start time of
  the attempt that produced it.
- `Report` for a failure before output. Pass the class it returns as
  `LastFailure` on the next attempt.
- `ObserveOverage` for each paid-overflow check or observed paid use.
- `Served` after the first successful response on an assignment.
- `Relogin` after a browser login, `LoginConfirmed` after a request that
  began after an auth failure succeeded, and `Move` for a manual override.

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
