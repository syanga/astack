# Claude router

An optional local proxy that routes Claude conversations across subscription
accounts. The behavior is specified in
[docs/specs/claude-account-routing.md](../../docs/specs/claude-account-routing.md).
[CONTRACT.md](CONTRACT.md) records what the pinned SDK guarantees, the routing
policy, and which contracts are still open.

The module has six parts.

| Path | Contents |
| --- | --- |
| `cmd/claude-router/` | The `claude-router` command: `serve`, `status`, `start`, `stop`, `login`, `move`, `direct`, and `token`. |
| `internal/claude/` | The local service. It embeds the pinned CLIProxyAPI SDK, authenticates clients, binds conversations through `internal/router`, and pins every upstream attempt to the bound account. |
| `probes/` | Feasibility probes. They embed the pinned CLIProxyAPI SDK, serve two or more fake accounts from an in-process fake Anthropic API, and refuse every other host. |
| `internal/router/` | The production routing policy and the durable assignment journal. It uses only the standard library. |
| `service/` | The launchd and systemd descriptor templates the installer fills in. |
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

## Install the service

The astack installer builds the command from the pinned module cache and sets
it up as a per-user service: a launchd agent on macOS, or a systemd user unit
on Linux. It never changes client settings. From the repository root:

```sh
./install.sh --component claude-router --dry-run
./install.sh --component claude-router
```

| Path | Contents |
| --- | --- |
| `~/.local/share/astack/claude-router/bin/claude-router` | The command. It keeps working after the checkout moves. |
| `~/.local/share/astack/claude-router/lib/` | The Python helper that `direct` runs. |
| `~/.local/state/astack/claude-router/` | State: `config.json`, `client-token`, `auths/`, `assignments/`, `accounts.json`, `events.jsonl`, `service.json`, `supervision.json`, `service.log` |
| `~/Library/LaunchAgents/dev.astack.claude-router.plist` | The launchd agent (macOS) |
| `~/.config/systemd/user/dev.astack.claude-router.service` | The systemd user unit (Linux) |

`XDG_DATA_HOME`, `XDG_STATE_HOME`, and `XDG_CONFIG_HOME` move these paths, as
they do for the rest of astack. The commands below default `-state` to the
state directory above. The installer seeds `config.json` with
`127.0.0.1:18787` and no accounts, and creates `client-token` with mode 0600.

To serve on another port, or to install a second copy beside the first, run the
helper directly with its own service name and port. On macOS, a second copy
can live in another home:

```sh
python3 scripts/claude_router.py install --service dev.example.router --port 28787 --home /path/to/home
```

On Linux, a second home does not work: systemd reads user units only from the
account's own directories, so it never loads a unit installed under another
`--home`, and `start` refuses with `systemd does not load …`.

Two installs with one service name share one launchd label or systemd unit.
`start`, `stop`, `direct`, and uninstall act only on the job loaded from their
own descriptor. They refuse to start, or leave running, a job loaded from the
other install. launchd keeps one on/off switch per label, and the install whose
plist is in your own `~/Library/LaunchAgents` owns it: the other install's
`stop` leaves it on, and its `start` refuses while that install has turned it
off. The owning install's `stop` turns the switch off even while the other
install's job runs, leaves that job running, and says so. Its uninstall turns
the switch back on and also leaves that job running.

## Enroll accounts and start

1. Log in to each account. The router must be stopped. The login opens a
   browser and writes `auths/<id>.json`:

   ```sh
   claude-router login -account work
   ```

2. Add each account to `accounts` in `config.json`. `capacity` is the
   account's relative allowance, such as 1 for Pro and 5 for Max 5x:

   ```json
   "accounts": [{"id": "work", "capacity": 5}, {"id": "alumni", "capacity": 1}]
   ```

   `overage_fresh_for` (default `30m`) and `overage_check_every` (default
   `10m`) set how long a paid-overflow reading lasts and how often the service
   reads it. `max_upstream_attempts` (1 to 4, default 4) caps the upstream
   attempts of one client request; 1 turns off router retries.

3. Start the service. `start` waits until the service answers:

   ```sh
   claude-router start
   ```

4. Make the router Claude Code's default connection, as
   [Activate the router](#activate-the-router) describes.

The service reads `config.json` and the credentials once, at start. To change
either, run `claude-router stop`, make the change, and run `claude-router
start`.

## Activate the router

Activation points Claude Code at the router. It refuses unless an acceptance
record matches the installed commit, the installed and running binary, the
pinned SDK, and both clients: the terminal `claude` and the Agent SDK that T3
runs. `claude_router.py identity` prints that identity for the record; the
acceptance run adds `"result": "pass"`. From the repository root, preview the
change, then apply it:

```sh
python3 scripts/claude_router.py activate --prepare --acceptance acceptance.json \
  --claude "$(command -v claude)" --agent-sdk /path/to/node_modules/@anthropic-ai/claude-agent-sdk
python3 scripts/claude_router.py activate --acceptance acceptance.json \
  --claude "$(command -v claude)" --agent-sdk /path/to/node_modules/@anthropic-ai/claude-agent-sdk \
  --verify 'claude -p --resume SESSION_ID "Reply with one word."' --verify 'T3_CONTINUATION_COMMAND'
```

The router must be running with at least one usable account. Activation
writes three keys into `~/.claude/settings.json`, or into
`$CLAUDE_CONFIG_DIR/settings.json`:

```json
{
  "apiKeyHelper": "cat ~/.local/state/astack/claude-router/client-token",
  "env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:18787", "CLAUDE_CODE_RETRY_WATCHDOG": "1"}
}
```

Claude Code runs `apiKeyHelper` with `/bin/sh` and sends its output as both
`X-Api-Key` and `Authorization: Bearer`; the router accepts either. The token
stays in `client-token`. Claude Code uses `ANTHROPIC_AUTH_TOKEN` or
`ANTHROPIC_API_KEY` before `apiKeyHelper`, so activation refuses a settings
file that sets either one, and prints a `warn` line for each other place it
finds one: a project named with `--project`, managed settings, the shell's
environment, and the environment launchd or systemd gives apps.
`CLAUDE_CODE_RETRY_WATCHDOG=1` makes the client wait out a long reset instead
of stopping.

Before it writes, activation backs the settings file up under
`~/.local/state/astack/backups/activate-*/` and records the change in
`activation.json` in the state directory. After it writes, it runs each
`--verify` command. Each must exit 0 and complete at least one request
through the router; otherwise activation restores the settings file and
exits 1. To go back to a direct connection later, run `claude-router direct`.
It also undoes an activation that was interrupted. Uninstall refuses until
then.

## Operate the service

| Command | What it does |
| --- | --- |
| `claude-router status` | One line for the service, one per account, and one per problem: a conversation that waits, is refused, or is held for login, a recent move, or a quota or paid-overflow reading older than 15 minutes. The last line names the next command to run. A stopped service shows its last start error, or that it cannot record a start. |
| `claude-router status -v` | Every reading with its age, cache counters per account, every assigned conversation, and the migrations, newest first. |
| `claude-router stop` | Stops the service and keeps it off, across logins and restarts, until `claude-router start`. Assignments stay. |
| `claude-router start` | Clears the start history, turns the job back on, and starts it. It also starts at each login until the next `stop`. |
| `claude-router login -account ID` | Logs in again after a revoked login. It refuses while the service runs, because the service would write its old credential back. Run `stop`, `login`, then `start`. |
| `claude-router move -conversation HASH -to ID` | Moves a conversation, named by the hash that status shows, to another account. A prefix of 4 or more hex digits works if it matches one conversation. |
| `claude-router direct -prepare` | Lists the client settings that router activation owns and that `direct` would restore, and the backup it would write. Changes nothing. |
| `claude-router direct` | Stops the service as `stop` does and restores those client settings, backing each file up to the path that `-prepare` printed. It restores the file of an interrupted activation from its backup. Logins, assignments, the binary, and the job stay. A key edited after activation stops it, and the edit is kept. With no client settings to restore, it changes nothing and leaves the service running. A job loaded from another install keeps running, and `direct` does not say the router stopped. |

Status shows hashes, account names, counters, and times, never a token or a
prompt. A `direct` backup is a copy of the activated settings file with mode
0600. It names the token file, not the token.

The managed job runs `claude-router serve -supervised`. It counts each start
before it reads `config.json`. After 5 starts within 10 minutes that did not
end in a clean stop, it records that it gave up and exits 0. launchd
(`KeepAlive` with `SuccessfulExit` false) and systemd (`Restart=on-failure`)
do not restart a job that exits 0, so a crash loop ends there, including one
caused by a mistake in `config.json`. `status` shows `failed` with the last
start error until the next `start`. A mistake in `config.json` shows as
`config error:` until supervision gives up, about 50 seconds after the first
failed start; from then on `failed` takes precedence and the error shows as
`last error:`. launchd waits 10 seconds between starts (`ThrottleInterval`)
and systemd waits 10 seconds (`RestartSec`). launchd has no restart count of
its own; the limit is the router's.

`stop` turns the job off with `launchctl disable` or `systemctl --user
disable` before it stops the job, so a `stop` that fails or is interrupted
still leaves it off at login. `start` turns it back on with `enable`, also when
the router is still running. Uninstall stops the job and leaves no setting that
keeps a later install from starting at login: it turns the launchd switch back
on, also while another install's job runs, and disables the systemd unit, whose
file it then removes. A launchd switch that another install owns stays as that
install set it.

Durability across a power loss or OS crash is not tested. The journal relies
on `F_FULLFSYNC` on macOS and `fsync` on Linux (CONTRACT.md, PS-5).

## Run in the foreground

Where no user service manager runs, such as a container without systemd,
install with `--manager foreground` and run the service yourself:

```sh
python3 scripts/claude_router.py install --manager foreground
claude-router serve -config ~/.local/state/astack/claude-router/config.json
```

Nothing restarts a foreground service when it exits, and it says so when it
starts. `start` and `stop` refuse; stop it with Ctrl-C or SIGTERM. Run it
under your own supervisor with `-supervised` to get the same restart limit.

## Remove the service

```sh
./uninstall.sh --component claude-router --dry-run
./uninstall.sh --component claude-router
```

Uninstall stops the job with the installed `claude-router stop -uninstall`,
then removes the binary, the helper, the job descriptor, and `service.json`.
It keeps `auths/`, `assignments/`, `client-token`, `events.jsonl`, and an
edited `config.json`. If router activation owns client settings, uninstall restores
them as `direct` does.

## Build and run by hand

1. Build the command:

   ```sh
   GOPROXY=off GOSUMDB=off go build -o claude-router ./cmd/claude-router
   ```

2. Create a state directory and a client token:

   ```sh
   mkdir -m 700 /path/to/state
   ./claude-router token -out /path/to/state/client-token
   ```

3. Log in with `./claude-router login -state /path/to/state -account acct-a`,
   and write `/path/to/state/config.json`:

   ```json
   {
     "listen": "127.0.0.1:8787",
     "state_dir": "/path/to/state",
     "client_token_file": "/path/to/state/client-token",
     "accounts": [{"id": "acct-a", "capacity": 5}, {"id": "acct-b", "capacity": 1}]
   }
   ```

4. Run `./claude-router serve -config /path/to/state/config.json`. The other
   commands take `-state /path/to/state`.

## How the service routes

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
and new conversations go elsewhere. `claude-router move` sends
`POST /claude-router/move` with the client token and
`{"conversation": "<session id or hash>", "to": "<account>"}`. Exhaustion,
login, and paid-overflow state survive a restart.

`GET /claude-router/status` with the client token reports each account's
registration, capacity, latest quota observation, paid-overflow reading,
login requirement, known rejections, and bound and active conversation
counts, and each assigned conversation. `claude-router status` formats it
with `events.jsonl`. `events.jsonl` in the state directory records
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
