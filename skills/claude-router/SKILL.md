---
name: claude-router
description: Operate the astack Claude router, which routes Claude Code across Claude accounts. Use to check it, read its status or cache cost, set it up, add or remove an account, or roll back to direct.
---

# Operate the Claude router

The Claude router is a local proxy that routes Claude Code conversations
across several Claude subscription accounts. A user with one account does not
need it.

## Find the command

An agent's shell may not have `claude-router` on PATH. This prints its
absolute path:

```sh
echo "${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin/claude-router"
```

Below, `claude-router` stands for that path. Write the absolute path in every
command you run or give. If the file is missing, the router is not installed,
and [Set up from nothing](#set-up-from-nothing) applies.

## Run the read-only commands yourself

Run `status`, `check`, and `cache` yourself. When `status` does not explain a
failure, also run the state and log commands in
[Find the service](#find-the-service). None of these commands stops, restarts,
or reconfigures the router. `check` sends one short test message on one
account's plan. Show their lines as printed. The router's tests check that its
status, events, and service logs hold no token.

| Command | Use it for |
| --- | --- |
| `claude-router status` | The service, one line per account, problem lines, and a `next` line with the step to take. |
| `claude-router check` | Whether this machine routes Claude Code. Exits 0 when every line passes. It can take 2 minutes, so give it a 180-second timeout. |
| `claude-router cache` | Each account's cache hit rate, then the last 24 hours' account moves and the cache writes they caused. `-since 6h` sets another window. |

To answer "is the router working", run `check` and report its lines, with the
step from [Read check lines](#read-check-lines) for each `fail` line.

## Give the user every other command

Give every other command to the user in a code block, to run in their own
terminal, and say which reason applies:

- A command that stops or restarts the router cuts off every routed session
  in the middle of a turn, possibly this one.
- A sign-in waits on a browser window that only the user can use.
- `start` turns the router on at every login.

| Command | What it does |
| --- | --- |
| `claude-router setup -account NAME ...` | Signs in each new account in a browser, starts the router, and activates it. Activation points Claude Code's settings at the router. |
| `claude-router account add NAME` | Stops the router, signs in the account in a browser, and starts the router again. |
| `claude-router account remove NAME` | Stops the router, removes the account, and starts the router again. |
| `claude-router login -account NAME` | Signs in an account again. It runs only while the router is stopped. |
| `claude-router stop` | Stops the router. |
| `claude-router start` | Starts the router and turns it on at login. |
| `claude-router direct` | Stops the router and restores Claude Code's settings. |
| `cd CHECKOUT && ./install.sh --component claude-router` | Builds the router. Over an activated router, it restarts the service. `astack-update` runs it itself, because invoking that skill authorizes the restart. |

`NAME` is the user's label for an account, such as `work`. With `-no-browser`,
`setup`, `account add`, and `login` print the sign-in URL. `account remove`
refuses for the last account, and for an account with conversations from the
last 5 hours. With `-force`, those conversations move to another account on
their next request.

`CHECKOUT` is the absolute path of the user's astack checkout. Ask the user
for it.

## Read check lines

`check` prints one line per check, then `check passed` or `check failed`, then
a `next` line after a failure. Each line starts with `ok`, `fail`, or `skip`.
A `skip` line follows a failure or a stopped router and needs no step.

| Text after `fail` | Step |
| --- | --- |
| `router not answering on ADDRESS` | If the settings line also fails, follow that row, because `setup` starts the router. Otherwise give `claude-router start`, and run `check` after the user runs it. |
| `no accounts in config.json` | Give `claude-router setup -account NAME ...`. |
| `NAMES not signed in` | Run `status`. For each account that needs a login, give `claude-router stop`, `claude-router login -account NAME`, and `claude-router start`. |
| `paid overflow on for NAMES` | Ask the user to turn off paid overflow for those accounts on claude.ai. The router reads it again within 10 minutes. Run `check` after that, or give `claude-router stop` and `claude-router start` for a reading now. |
| `paid overflow not read yet for NAMES` | Run `status` and report the account's reading. Run `check` again after the next reading, within 10 minutes. |
| `Claude Code settings do not point at the router` | Give `claude-router setup`. See the list below the table. |
| `no accepted record at PATH` | Give the install command, from a checkout whose `components/claude-router/accepted.json` matches its code. |
| `code differs from the accepted record` | Report the indented lines below it. For `binary: installed X, running Y` or `code: accepted X, running Y`, give `claude-router stop` and `claude-router start`. For any other line, give the install command, from a checkout whose `accepted.json` matches its code. |
| `test message got no answer through the router (timed out after 120 s)` | Run `status` and report its problem lines. |
| `test message got no answer through the router (claude exited 0)` | Run `status` and report its problem lines. If it shows none, Claude Code may have answered without the router. Ask the user to look for `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN` in project settings, managed settings, and the shell. |
| `test message got no answer through the router (claude exited N)` | Report the exit status. 127 means `claude` is not on PATH. For other values, run `status` and report its problem lines. |

For the settings line:

- `setup` with no `-account` reuses the accounts in `config.json`. If it
  refuses with `config.json lists no accounts`, give
  `claude-router setup -account NAME ...`.
- If `setup` refuses with `Claude Code already routes through the router`,
  the settings changed after activation. Give `claude-router direct`, then
  the same `setup`. If `direct` refuses, give its `next` line.
- A settings file whose `env` sets `ANTHROPIC_API_KEY` or
  `ANTHROPIC_AUTH_TOKEN` also fails this line. `setup` refuses until the user
  removes the key.

If `check` stops before the first check line, it prints a line that starts
with `claude-router:` or `astack:`. Report that line, and give the `next` line
if one follows.

## Know what the router costs and refuses

A conversation stays on its account until that account's included allowance
runs out. Then the router moves the conversation to another account, and the
next request writes the conversation's prompt cache again there. `cache`
reports these moves and their cache writes. `status` prints a `cache` line
when moves caused more than 10% of the last 24 hours' cache writes.

A Claude model newer than the router's model list gets a 503 that says the
SDK refused the pinned account without an upstream attempt. The router
reads the published model catalog at each start, so give
`claude-router stop` and `claude-router start`.

Paid overflow is the claude.ai setting that bills usage past the plan's
included allowance, shown there as extra usage or usage credits. The router
sends requests only to accounts whose latest reading shows paid overflow off.
If the user turns paid overflow on after the last reading, while the account's
included allowance is used up, one paid response can happen before the router
sees it.

## Set up from nothing

Give the user these steps. Put the checkout's absolute path in place of
`CHECKOUT`, and the user's account labels in place of `work` and `alumni`.

1. Install Go 1.26 or newer from <https://go.dev/dl/>, Python 3.10 or newer,
   and Claude Code. `python3 --version`, `go version`, and `claude --version`
   must work in the shell.
2. If the user has no astack checkout, give
   `git clone https://github.com/syanga/astack.git`. `CHECKOUT` is then the
   absolute path of the new `astack` directory.
3. Download the pinned modules once, with network access:

   ```sh
   (cd CHECKOUT/components/claude-router && go mod download)
   ```

4. Build and install the router. The install changes no Claude Code settings:

   ```sh
   cd CHECKOUT && ./install.sh --component claude-router
   ```

5. When the install asks `Add claude-router to PATH in FILE? [Y/n]`, press
   Enter. Then open a new shell.

6. On Linux only, let the router run without a login session:

   ```sh
   sudo loginctl enable-linger "$USER"
   ```

7. Sign in each account, start the router, and activate it:

   ```sh
   claude-router setup -account work -account alumni
   ```

   It ends with `activated Claude Code routes through the router`. On Linux,
   the `code matches` line ends with `(darwin/arm64)`, and a `note` line says
   `not yet tested on real Linux`. Both name the accepted record's platform,
   and neither is a failure.

If `setup` refuses because an account has paid overflow on, ask the user to
turn it off on claude.ai, then give `claude-router stop` and
`claude-router setup`. Without the `stop`, the running router keeps its old
reading for up to 10 minutes and `setup` can refuse again.

After `setup` and after `direct`, tell the user to start a new Claude Code
session, because a session reads its settings when it starts. After the user
reports that `setup` finished, run `check`.

## Roll back

Give the user `claude-router direct`, and tell them to start a new Claude Code
session. If `direct` prints `after   claude needs its own /login`, tell the
user to run `/login` in that session. `claude-router setup` activates the
router again. `cd CHECKOUT && ./uninstall.sh --component claude-router`
removes it. `direct` and uninstall keep logins and assignments.

## Find the service

When `status` does not explain a failure, run the state and log commands for
this system and report what they show.

| | macOS | Linux |
| --- | --- | --- |
| Service | launchd agent `dev.astack.claude-router` | systemd user unit `dev.astack.claude-router.service` |
| State | `launchctl print gui/$(id -u)/dev.astack.claude-router` | `systemctl --user status --no-pager dev.astack.claude-router` |
| Logs | `tail -n 100 "${XDG_STATE_HOME:-$HOME/.local/state}/astack/claude-router/service.log"` | `journalctl --user -u dev.astack.claude-router --no-pager -n 100` |
| Starts | At each login | At boot with linger. Without linger, at login, and it stops about 15 seconds after the last session ends. |
