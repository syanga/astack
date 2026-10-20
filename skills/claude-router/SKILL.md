---
name: claude-router
description: Operate the astack Claude router, which spreads Claude Code across several Claude accounts. Use to check it, read its status or cache cost, set it up, add or remove an account, or roll back.
---

# Operate the Claude router

The Claude router is a local proxy that routes Claude Code conversations
across several Claude subscription accounts. When a user with one Claude
account asks to set it up, tell them they do not need it.

## Find the command

The installer does not put `claude-router` on PATH. Run this to get its
absolute path:

```sh
echo "${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin/claude-router"
```

Below, `claude-router` stands for that path. Write the path in every command
you run and every command you give the user. If the file does not exist, the
router is not installed. Follow [Set up from nothing](#set-up-from-nothing).

## Run status, check, and cache yourself

These three commands read state, and `check` also sends one short test
message on one account's plan. Run them and show their lines as printed. The
lines hold account names, hashes, counters, and times, never a token or a
prompt.

| Command | Use it for |
| --- | --- |
| `claude-router status` | The service, one line per account, problem lines, and a `next` line with the step to take. |
| `claude-router check` | Whether this machine routes Claude Code. Exits 0 when every line passes. It can take 2 minutes, so give Bash a timeout of 180 seconds. |
| `claude-router cache` | Each account's cache hit rate, then the account moves of the last 24 hours and the cache writes they caused. `-since 6h` sets another window. |

To answer "is the router working", run `check` and report its lines. For each
`fail` line, give the step from [Read check lines](#read-check-lines).

## Give the user every other command

Give the user every other command in a code block, to run in their own
terminal. Claude Code sessions, possibly this one, send their requests
through the router. A command that stops, restarts, or rewires the router cuts
off every routed session in the middle of a turn. A sign-in waits on a browser
window that only the user can use. Say which of these reasons applies.

| Command | What it does |
| --- | --- |
| `claude-router setup -account NAME ...` | Opens a browser to sign in each account, restarts the router, and points Claude Code's settings at it. |
| `claude-router account add NAME` | Stops the router, opens a browser to sign in, and starts the router again. |
| `claude-router account remove NAME` | Stops the router and starts it again. |
| `claude-router login -account NAME` | Opens a browser to sign in again. It runs only while the router is stopped. |
| `claude-router stop` | Stops the router. |
| `claude-router start` | Starts the router and turns it on at login. |
| `claude-router direct` | Stops the router and rewrites Claude Code's settings. |
| `./install.sh --component claude-router` | Builds the router. Over an activated router it restarts the service. |

`NAME` is the user's own label for an account, such as `work`. With
`-no-browser`, `setup`, `account add`, and `login` print the sign-in URL
instead of opening a browser. `account add` leaves existing conversations on
their accounts. `account remove` refuses for the last account, and for an
account with conversations from the last 5 hours, and prints the count. With
`-force`, those conversations move to another account on their next request.

Run `./install.sh` and `./uninstall.sh` from the user's astack checkout. Ask
for its path, and write commands as `cd CHECKOUT && ./install.sh ...`.

## Read check lines

`check` prints one line per check, then `check passed` or `check failed`, then
a `next` line after a failure. Each line starts with a label padded to 10
columns: `ok`, `fail`, or `skip`. A `skip` line follows a failure or a stopped
router and needs no step of its own. "After the user runs it" means wait for
the user to say so, then run `check` again.

| Text after `fail` | Step |
| --- | --- |
| `router not answering on ADDRESS` | If the settings line also fails, follow its row, because `setup` starts the router. Otherwise give `claude-router start`, and after the user runs it, run `check` again. |
| `no accounts in config.json` | Give `claude-router setup -account NAME ...`. |
| `NAMES not signed in` | Run `status`. For an account that needs a login, give `claude-router stop`, `claude-router login -account NAME`, and `claude-router start`. |
| `paid overflow on for NAMES` | Ask the user to turn off paid overflow for those accounts on claude.ai. After the user does, run `check` again. |
| `paid overflow not read yet for NAMES` | Run `status` and report the account's reading. The router reads paid overflow every 10 minutes by default. Run `check` again after the next reading. |
| `Claude Code settings do not point at the router` | Give `claude-router setup -account NAME ...` with the user's account labels. Accounts already added skip the sign-in. If `setup` refuses with `Claude Code already routes through the router`, the settings changed after activation: give `claude-router direct`, then the same `setup`. If `direct` refuses, give its `next` line. A settings file that sets `ANTHROPIC_API_KEY` or `ANTHROPIC_AUTH_TOKEN` also fails this line, and `setup` refuses until the user removes the key. |
| `no accepted record at PATH` | Give `cd CHECKOUT && ./install.sh --component claude-router`, from a checkout whose `components/claude-router/accepted.json` matches its code. |
| `code differs from the accepted record` | Report the indented lines below it. For `binary: installed X, running Y` or `code: accepted X, running Y`, give `claude-router stop` and `claude-router start`. For any other line, give `cd CHECKOUT && ./install.sh --component claude-router`, from a checkout whose `accepted.json` matches its code. |
| `test message got no answer through the router (timed out after 120 s)` | Run `status` and report its problem lines. |
| `test message got no answer through the router (claude exited 0)` | Claude Code answered without the router. Another settings file or the environment overrides it. Ask the user to look for `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN` in project settings, managed settings, and the shell. |
| `test message got no answer through the router (claude exited N)` | Report the exit status. 127 means `claude` is not on PATH. For other values, run `status` and report its problem lines. |

When `check` stops before the first check line, it prints a line that starts
with `claude-router:` or `astack:`. Report that line, and give the `next` line
when one follows.

## Know what the router costs and refuses

A conversation stays on its account. When an account's included allowance
runs out, the router moves the conversation to another account. The next
request writes that conversation's prompt cache again on the new account.
`cache` reports these moves and the cache writes they caused. `status` prints
a `cache` line when moves caused more than 10% of the last 24 hours' cache
writes.

Paid overflow is the claude.ai setting that bills usage past the plan's
included allowance. The router sends requests only to accounts whose latest
reading says paid overflow is off, and `setup` refuses an account with it on.
One gap remains. If the user turns paid overflow on at claude.ai after the
last reading, while the account's included allowance is used up, one paid
response can happen before the router sees it.

## Set up from nothing

Give the user these steps. Linux uses the same commands, with the differences
in [Find the service](#find-the-service). Linux setup is tested offline
against a fake `systemctl`, not yet on real Linux.

1. Install Go 1.26 or newer from <https://go.dev/dl/>, Python 3.10 or newer,
   and Claude Code. `go version` and `claude --version` must work in the shell.
2. Without an astack checkout, clone one:

   ```sh
   git clone https://github.com/syanga/astack.git
   cd astack
   ```

3. From the checkout, download the pinned modules once, with network access:

   ```sh
   (cd components/claude-router && go mod download)
   ```

4. Build and install the router. It changes no Claude Code settings:

   ```sh
   ./install.sh --component claude-router
   ```

5. Run this line, and add it to `~/.zshrc` on macOS or `~/.bashrc` on Linux:

   ```sh
   export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin:$PATH"
   ```

6. Sign in each account, start the router, and activate it, with the user's
   own labels in place of `work` and `alumni`:

   ```sh
   claude-router setup -account work -account alumni
   ```

   It prints the `check` lines, then
   `activated Claude Code routes through the router` and
   `next      claude-router status`. When this machine's platform differs from
   the accepted record's, as on Linux, it also prints a `note` line that names
   both.

After the user reports that `setup` finished, run `check`.

## Roll back

Give the user `claude-router direct`. It stops the router and restores the
Claude Code settings that activation changed. If it prints
`after   claude needs its own /login`, the user runs `/login` in Claude Code.
To route again, give `claude-router setup -account NAME ...` with the
user's account labels. To remove the router, give
`cd CHECKOUT && ./uninstall.sh --component claude-router`. `direct` and
uninstall both keep logins and assignments.

## Find the service

When `status` does not explain a failure, give the user the state and log
commands for their system.

| | macOS | Linux |
| --- | --- | --- |
| Service | launchd agent `dev.astack.claude-router` | systemd user unit `dev.astack.claude-router.service` |
| State | `launchctl print gui/$(id -u)/dev.astack.claude-router` | `systemctl --user status dev.astack.claude-router` |
| Logs | `${XDG_STATE_HOME:-$HOME/.local/state}/astack/claude-router/service.log` | `journalctl --user -u dev.astack.claude-router` |
| Login | Starts at each login | Starts at each login. To keep it running after the last session ends, the user runs `loginctl enable-linger "$USER"` once. |

State lives in `${XDG_STATE_HOME:-$HOME/.local/state}/astack/claude-router/`
on both systems.
