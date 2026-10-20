---
name: claude-router
description: Operate the astack Claude router, which routes Claude Code across Claude accounts. Use to check it, read its status or cache cost, set it up, add or remove an account, or roll back to direct.
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

## Run the read-only commands yourself

Run `status`, `check`, and `cache` yourself. When `status` does not explain a
failure, also run the state and log commands in
[Find the service](#find-the-service). These commands change nothing, and
`check` also sends one short test message on one account's plan. Show their
lines as printed. The router's tests check that its status, events, and
service logs hold no token or prompt.

| Command | Use it for |
| --- | --- |
| `claude-router status` | The service, one line per account, problem lines, and a `next` line with the step to take. |
| `claude-router check` | Whether this machine routes Claude Code. Exits 0 when every line passes. It can take 2 minutes, so give the command a 180-second timeout. |
| `claude-router cache` | Each account's cache hit rate, then the account moves of the last 24 hours and the cache writes they caused. `-since 6h` sets another window. |

To answer "is the router working", run `check` and report its lines. For each
`fail` line, give the step from [Read check lines](#read-check-lines).

## Give the user every other command

Give the user every other command in a code block, to run in their own
terminal. Claude Code sessions, possibly this one, send their requests
through the router. A command that stops or restarts the router cuts off
every routed session in the middle of a turn. A sign-in waits on a browser
window that only the user can use. `start` also turns the router on at every
login, a lasting change to the user's machine. Say which reason applies.

| Command | What it does |
| --- | --- |
| `claude-router setup -account NAME ...` | Opens a browser to sign in each new account, restarts the router when it adds one, and activates the router. Activation points Claude Code's settings at the router. |
| `claude-router account add NAME` | Stops the router, opens a browser to sign in, and starts the router again. |
| `claude-router account remove NAME` | Stops the router and starts it again. |
| `claude-router login -account NAME` | Opens a browser to sign in again. It runs only while the router is stopped. |
| `claude-router stop` | Stops the router. |
| `claude-router start` | Starts the router and turns it on at login. |
| `claude-router direct` | Stops the router and rolls back activation by restoring Claude Code's settings. |
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
router and needs no step of its own.

| Text after `fail` | Step |
| --- | --- |
| `router not answering on ADDRESS` | If the settings line also fails, follow its row, because `setup` starts the router. Otherwise give `claude-router start`. After the user runs it, run `check` again. |
| `no accounts in config.json` | Give `claude-router setup -account NAME ...`. |
| `NAMES not signed in` | Run `status`. For an account that needs a login, give `claude-router stop`, `claude-router login -account NAME`, and `claude-router start`. |
| `paid overflow on for NAMES` | Ask the user to turn off paid overflow for those accounts on claude.ai. The router reads it again within 10 minutes by default. Run `check` after that, or give `claude-router stop` and `claude-router start` for an immediate reading. |
| `paid overflow not read yet for NAMES` | Run `status` and report the account's reading. The router reads paid overflow every 10 minutes by default. Run `check` again after the next reading. |
| `Claude Code settings do not point at the router` | Give `claude-router setup`. See the list below the table. |
| `no accepted record at PATH` | Give `cd CHECKOUT && ./install.sh --component claude-router`, from a checkout whose `components/claude-router/accepted.json` matches its code. |
| `code differs from the accepted record` | Report the indented lines below it. For `binary: installed X, running Y` or `code: accepted X, running Y`, give `claude-router stop` and `claude-router start`. For any other line, give `cd CHECKOUT && ./install.sh --component claude-router`, from a checkout whose `accepted.json` matches its code. |
| `test message got no answer through the router (timed out after 120 s)` | Run `status` and report its problem lines. |
| `test message got no answer through the router (claude exited 0)` | The router recorded no completed 200 for the test session. Run `status` and report its problem lines, because the request may have reached the router and failed. If `status` shows no problem, Claude Code may have answered without the router. Ask the user to look for `ANTHROPIC_BASE_URL`, `ANTHROPIC_API_KEY`, or `ANTHROPIC_AUTH_TOKEN` in project settings, managed settings, and the shell. |
| `test message got no answer through the router (claude exited N)` | Report the exit status. 127 means `claude` is not on PATH. For other values, run `status` and report its problem lines. |

For the settings line:

- `setup` with no `-account` reuses the accounts in `config.json`. If it
  refuses with `config.json lists no accounts`, give
  `claude-router setup -account NAME ...` with the user's account labels.
- If `setup` refuses with `Claude Code already routes through the router`,
  the settings changed after activation. Give `claude-router direct`, then
  the same `setup`. If `direct` refuses, give its `next` line.
- A settings file whose `env` sets `ANTHROPIC_API_KEY` or
  `ANTHROPIC_AUTH_TOKEN` also fails this line. `setup` refuses until the user
  removes the key.

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
included allowance, shown there as extra usage or usage credits. The router
sends requests only to accounts whose latest reading says paid overflow is
off, and `setup` refuses an account with it on.
One gap remains. If the user turns paid overflow on at claude.ai after the
last reading, while the account's included allowance is used up, one paid
response can happen before the router sees it.

## Set up from nothing

Give the user these steps, with the checkout's absolute path for `CHECKOUT` and
the user's account labels in place of `work` and `alumni`. Linux adds step 6.
Linux is tested on a real kernel in a Debian 13 arm64 container with systemd
257 and fake accounts, not yet with real accounts on a Linux machine.

1. Install Go 1.26 or newer from <https://go.dev/dl/>, Python 3.10 or newer,
   and Claude Code. `python3 --version`, `go version`, and `claude --version`
   must work in the shell.
2. Without an astack checkout, clone one. `CHECKOUT` is then the absolute
   path of the new `astack` directory:

   ```sh
   git clone https://github.com/syanga/astack.git
   ```

3. Download the pinned modules once, with network access:

   ```sh
   (cd CHECKOUT/components/claude-router && go mod download)
   ```

4. Build and install the router. It changes no Claude Code settings:

   ```sh
   cd CHECKOUT && ./install.sh --component claude-router
   ```

5. Run this line:

   ```sh
   export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin:$PATH"
   ```

   To keep it in new shells, add the same line to `~/.zshrc` on macOS or
   `~/.bashrc` on Linux.

6. On Linux, let the router run without a login session:

   ```sh
   sudo loginctl enable-linger "$USER"
   ```

   The Start row in [Find the service](#find-the-service) says why.
7. Sign in each account, start the router, and activate it:

   ```sh
   claude-router setup -account work -account alumni
   ```

   It prints the `check` lines, then
   `activated Claude Code routes through the router` and
   `next      claude-router status`. When this machine's platform differs from
   the accepted record's, as on Linux, it also prints a `note` line that names
   both, such as
   `note      accepted on darwin/arm64, this is linux/arm64, not yet tested on real Linux`.
   The router helper prints that text, which predates the Linux container
   run above. The `code matches` check line names the record's platform, so
   it reads `(darwin/arm64)` on Linux too.

If `setup` refuses because an account has paid overflow on, ask the user to
turn it off on claude.ai, then give `claude-router stop` and
`claude-router setup`. The running router keeps its reading for up to 10
minutes, so a rerun without the `stop` can refuse again.

After `setup` and after `direct`, tell the user to start a new Claude Code
session. A new session reads the changed settings when it starts. After the
user reports that `setup` finished, run `check`.

## Roll back

Give the user `claude-router direct`. It stops the router and restores the
Claude Code settings that activation changed. Tell the user to start a new
Claude Code session afterward. If `direct` prints
`after   claude needs its own /login`, tell the user to run `/login` in that
session. To activate the router again, give `claude-router setup`. To remove
the router, give `cd CHECKOUT && ./uninstall.sh --component claude-router`.
`direct` and uninstall both keep logins and assignments.

## Find the service

When `status` does not explain a failure, run the state and log commands for
this system and report what they show.

| | macOS | Linux |
| --- | --- | --- |
| Service | launchd agent `dev.astack.claude-router` | systemd user unit `dev.astack.claude-router.service` |
| State | `launchctl print gui/$(id -u)/dev.astack.claude-router` | `systemctl --user status --no-pager dev.astack.claude-router` |
| Logs | `tail -n 100 "${XDG_STATE_HOME:-$HOME/.local/state}/astack/claude-router/service.log"` | `journalctl --user -u dev.astack.claude-router --no-pager -n 100` |
| Start | At each login | At boot with linger. Otherwise at login, and it stops about 15 seconds after the last session ends. |

State lives in `${XDG_STATE_HOME:-$HOME/.local/state}/astack/claude-router/`
on both systems.
