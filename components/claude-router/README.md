# Claude router

An optional local proxy that routes Claude conversations across subscription
accounts. The behavior is specified in
[docs/specs/claude-account-routing.md](../../docs/specs/claude-account-routing.md).
[CONTRACT.md](CONTRACT.md) records what the pinned SDK guarantees, the routing
policy, and which contracts are still open.

The module has six parts.

| Path | Contents |
| --- | --- |
| `cmd/claude-router/` | The `claude-router` command: `setup`, `check`, `account`, `serve`, `status`, `cache`, `start`, `stop`, `login`, `move`, `direct`, and `token`. |
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
| `~/.local/share/astack/claude-router/lib/` | The Python helper that `direct`, `check`, and `setup` run. |
| `~/.local/share/astack/claude-router/accepted.json` | The acceptance record of the router code, copied from `components/claude-router/accepted.json` when the checkout has one. |
| `~/.local/state/astack/claude-router/` | State: `config.json`, `client-token`, `auths/`, `assignments/`, `accounts.json`, `events.jsonl`, `service.json`, `supervision.json`, `service.log` |
| `~/Library/LaunchAgents/dev.astack.claude-router.plist` | The launchd agent (macOS) |
| `~/.config/systemd/user/dev.astack.claude-router.service` | The systemd user unit (Linux) |

`XDG_DATA_HOME`, `XDG_STATE_HOME`, and `XDG_CONFIG_HOME` move these paths, as
they do for the rest of astack. The commands below default `-state` to the
state directory above. The installer seeds `config.json` with
`127.0.0.1:18787` and no accounts, and creates `client-token` with mode 0600.
The router refuses a token shorter than 32 characters or open to other users,
so the installer replaces a shorter `client-token` and sets an existing one to
mode 0600.

To serve on another port, or to install a second copy beside the first, run the
helper directly with its own service name and port. On macOS, a second copy
can live in another home:

```sh
python3 scripts/claude_router.py install --service dev.example.router --port 28787 --home /path/to/home
```

Linux support is not yet tested on real Linux. Unit tests cover the systemd
path against a fake `systemctl`.

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

To sign in each account, add it, start the router, and point Claude Code at
it in one step, run `setup` with one `-account` per account:

```sh
claude-router setup -account work -account alumni
```

Each sign-in opens a browser window. With `-no-browser`, `setup` prints the
sign-in URL instead. `setup` adds each account to `config.json` with
capacity 1, starts the router, and waits until every account is ready. It
refuses, and Claude Code stays direct, when an account has paid overflow on.
It then activates the router with the installed acceptance record and runs
`claude-router check`, as [Activate the router](#activate-the-router)
describes. `setup` refuses when Claude Code already routes through the
router.

To add an account later, run `account add`. It stops the router, signs in,
adds the account with capacity 1, starts the router again, and waits for the
account to be ready. A router that was stopped stays stopped. A failed
sign-in leaves `config.json` unchanged and starts the router again if it
ran:

```sh
claude-router account add school
```

To remove an account, run `account remove`. It refuses for the last account,
and for an account with conversations from the last 5 hours, unless you pass
`-force`. It stops the router, removes the account from `config.json`,
deletes `auths/<id>.json`, and starts the router again if it ran. The
account's conversations move to another account on their next request, and
`claude-router cache` counts each as a move. The claude.ai session stays
signed in until you sign it out on claude.ai:

```sh
claude-router account remove school
```

These commands rewrite `config.json`. They keep every value but not the
formatting: keys come back in a fixed order, and a duration such as `30m`
comes back as `30m0s`. They refuse a file with a key the router does not
know. You can also edit `config.json` by hand while the router is stopped.
`capacity` is the account's relative allowance, such as 1 for Pro and 5 for
Max 5x:

```json
"accounts": [{"id": "work", "capacity": 5}, {"id": "alumni", "capacity": 1}]
```

`overage_fresh_for` (default `30m`) and `overage_check_every` (default
`10m`) set how long a paid-overflow reading lasts and how often the service
reads it. `max_upstream_attempts` (1 to 4, default 4) caps the upstream
attempts of one client request. With 1 the router never resends a request
that may have reached the provider. A request that ends on a network
failure gets a 503 that asks the client to retry, so the client may resend
a request that the provider already ran. The router still retries a
connection that failed before the request was written, for up to 25 s.
`move_cache_warn_percent` (0 to 100, default 10) sets the share of the
last 24 hours' cache writes that account moves may cause before `status`
warns. 0 turns the warning off.

The service reads `config.json` and the credentials once, at start. To change
either by hand, run `claude-router stop`, make the change, and run
`claude-router start`. `claude-router login -account ID` signs in one account
while the router is stopped.

`start` refuses while `auths/` holds a login whose file name is not an
account in `config.json`.

## Activate the router

Activation points Claude Code at the router. It refuses unless an acceptance
record matches the router code, the installed helper scripts, and the pinned
SDK, and the running binary is the installed one. The router code digest
covers the tracked files under `components/claude-router/` except `*.md`,
`*_test.go`, `testdata/`, and `accepted.json`, so a record stays valid across
commits that change only those files. To print the digests of a checkout,
run:

```sh
python3 scripts/claude_router.py identity --code-only
```

`claude-router setup` activates with the installed record. To activate by
hand, preview the change, then apply it:

```sh
python3 scripts/claude_router.py activate --prepare
python3 scripts/claude_router.py activate
```

`--acceptance FILE` names another record. `--verify SESSION COMMAND` adds a
command that continues a conversation, such as a T3 thread, and must route
through the router.

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
`--verify` command, then `claude-router check`. Each command must exit 0 and
complete at least one request through the router for the conversation whose
session ID precedes it, and every check must pass. Otherwise activation
restores the settings file and exits 1. On a platform other than the record's,
activation prints a `note` line that names both. Ctrl-C, SIGTERM,
or closing the terminal during these commands does the same, unless the shell
started activation with that signal ignored, as `nohup` does for a hangup.
Further signals do not interrupt the restore. When a command
ends, activation kills every process in the command's process group. A
process that starts its own session or group, such as a detached Node child,
keeps running. Activation prints only each command's program name, never its
arguments. Activation, `claude-router direct`, `setup`, `account`, and the
component's install and uninstall run one at a time. A second one refuses
and changes nothing.

Run activation from the checkout that installed the router, or run the
installed copy, `~/.local/share/astack/claude-router/lib/claude_router.py`.
Activation, and uninstall while it would restore client settings, refuse when
the helper scripts they run differ from the installed ones, because the
acceptance record covers only the installed copy. `claude-router direct` runs
the installed copy without that check.

To go back to a direct connection later, run `claude-router direct`. It also
undoes an activation that was interrupted or whose settings record is gone.
Uninstall refuses until then.

## Check the router

`claude-router check` prints one line per check: the router runs, every
account in `config.json` is ready, paid overflow is off on every account, the
Claude Code settings point at the router, and the code matches the installed
acceptance record. It then runs your `claude -p` with a new session ID, no
tools, and a one-word prompt, for up to 120 seconds, and passes when
`events.jsonl` shows that session's request answered 200. It never prints the
prompt or the reply:

```text
ok        router running on 127.0.0.1:18787 (launchd)
ok        work, alumni, personal ready
ok        paid overflow off on all 3 accounts
ok        Claude Code settings point at the router
ok        code matches the accepted record (darwin/arm64)
ok        test message answered 200 on alumni in 6.2s
check     passed
```

When a check fails, `check` skips the test message, exits 1, and prints the
next step. With the router stopped, it marks the checks that need the router
`skip`. When `./install.sh --component claude-router` replaces the code of an
activated router, it restarts the service and runs `check`. It refuses, and
changes nothing, while `setup`, `account`, activation, or `claude-router
direct` runs. If the restart or the check fails, it runs `claude-router
direct` and prints `next      claude-router check`. `--dry-run` prints the
restart and the check that the install would run.

## Operate the service

| Command | What it does |
| --- | --- |
| `claude-router setup -account NAME ...` | Signs in each new account, adds it to `config.json`, starts the router, and activates it after `check` passes ([Enroll accounts and start](#enroll-accounts-and-start)). |
| `claude-router check` | Checks the router, the accounts, paid overflow, the Claude Code settings, and the accepted record, then sends one test message ([Check the router](#check-the-router)). |
| `claude-router account add NAME` | Signs in one more account and adds it to `config.json`. |
| `claude-router account remove NAME [-force]` | Removes an account and its login. Its conversations move to another account on their next request. |
| `claude-router status` | One line for the service, one per account, and one per problem. A problem is a recent move, a quota or paid-overflow reading older than 15 minutes, or a conversation that waits, is refused, is held for login, or could not reach the provider. A `cache` line warns when account moves caused more than `move_cache_warn_percent` of the last 24 hours' cache writes, also while the service is stopped. The last line names the next command to run. A stopped service shows its last start error, or that it cannot record a start. |
| `claude-router status -v` | Every reading with its age, cache counters per account, every assigned conversation, and the migrations, newest first. |
| `claude-router cache [-since 24h]` | For the window, each account's cache hit rate and token counters, then the number of account moves, the cache writes those moves caused, their share of all cache writes, and the moves per reason. It reads only `events.jsonl` and `config.json`, so it works while the service is stopped. |
| `claude-router stop` | Stops the service and keeps it off, across logins and restarts, until `claude-router start`. Assignments stay. |
| `claude-router start` | Clears the start history, turns the job back on, and starts it. It also starts at each login until the next `stop`. |
| `claude-router login -account ID` | Logs in again after a revoked login. It refuses while the service runs, because the service would write its old credential back. Run `stop`, `login`, then `start`. |
| `claude-router move -conversation HASH -to ID` | Moves a conversation, named by the hash that status shows, to another account. A prefix of 4 or more hex digits works if it matches one conversation. |
| `claude-router direct -prepare` | Lists the client settings that router activation owns and that `direct` would restore, and the backup it would write. Changes nothing. |
| `claude-router direct` | Stops the service as `stop` does and restores those client settings, backing each file up to the path that `-prepare` printed. It restores the file of an interrupted activation, or of an activation whose settings record is gone, from its backup; if that file changed since, it refuses before it stops the service, and the edit is kept. Logins, assignments, the binary, and the job stay. A key edited after activation stops it, and the edit is kept. With no client settings to restore, it changes nothing and leaves the service running. A job loaded from another install keeps running, and `direct` does not say the router stopped. |

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

`python3 scripts/claude_router.py uninstall` acts on the service name that the
home's install recorded. It refuses a `--service` that differs from it.

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
