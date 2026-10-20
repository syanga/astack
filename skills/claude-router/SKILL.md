---
name: claude-router
description: Operate the astack Claude router. Use to check whether it works, read its status or cache cost, set it up, add or remove an account, or roll Claude Code back to a direct connection.
---

# Operate the Claude router

The Claude router is a local proxy that routes Claude Code conversations
across several Claude subscription accounts. When a user with one Claude
account asks to set it up, tell them they do not need it.

## Find the command

The installer does not put `claude-router` on PATH. Print its absolute path:

```sh
echo "${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin/claude-router"
```

Below, `claude-router` stands for that path. Write the path in every command
you run and every command you print. If the file does not exist, the router is
not installed. Follow [Set up from nothing](#set-up-from-nothing).

## Run status, check, and cache yourself

These three commands read state, and `check` also sends one short test message
on one account's plan. Run them and show their lines as printed. The lines hold account names, hashes,
counters, and times, never a token or a prompt.

| Command | Use it for |
| --- | --- |
| `claude-router status` | The service, one line per account, problem lines, and a `next` line with the step to take. |
| `claude-router check` | Whether this machine routes Claude Code. Exits 0 when every line passes. It can take 2 minutes, so give Bash a timeout of 180 seconds. |
| `claude-router cache` | Each account's cache hit rate, then the account moves of the last 24 hours and the cache writes they caused. `-since 6h` sets another window. |

To answer "is the router working", run `check` and report its lines. For each
`fail` line, give the step from [Read check lines](#read-check-lines).

## Print every other command for Terminal

Give the user every other command in a code block to run in Terminal, with
the reason below. This Claude Code session sends its own requests through the
router. A command that stops, restarts, or rewires the router cuts off this
session, and every other routed session, in the middle of a turn. A sign-in
waits on a browser window that only the user can use.

| Command | Why the user runs it |
| --- | --- |
| `claude-router setup -account NAME ...` | Signs in each account in a browser, restarts the router, and points Claude Code's settings at it. |
| `claude-router account add NAME` | Stops the router, signs in in a browser, and starts the router again. |
| `claude-router account remove NAME` | Stops the router and starts it again. |
| `claude-router login -account NAME` | Signs in again in a browser. It runs only while the router is stopped. |
| `claude-router stop` | Stops the router. |
| `claude-router start` | Starts the service that this session routes through and turns it on at login. |
| `claude-router direct` | Stops the router and rewrites Claude Code's settings. |
| `./install.sh --component claude-router` | Builds the router. Over an activated router it restarts the service. |

`NAME` is the user's label for the account, such as `work`. With `-no-browser`,
`setup`, `account add`, and `login` print the sign-in URL instead of opening a
browser. `account add` leaves existing conversations on their accounts.
`account remove` refuses for the last account, and for an account with
conversations from the last 5 hours, and prints the count. With `-force`, those
conversations move to another account on their next request.

## Read check lines

`check` prints one line per check, then `check passed` or `check failed`, then
a `next` line after a failure. A `skip` line follows a failure or a stopped
router and needs no step of its own.

| Line | Step |
| --- | --- |
| `fail router not answering on ADDRESS` | Print `claude-router start`, then run `check` again. |
| `fail no accounts in config.json` | Print `claude-router setup -account NAME`. |
| `fail NAMES not signed in` | Run `status`. For an account that needs a login, print `claude-router stop`, `claude-router login -account NAME`, and `claude-router start`. |
| `fail paid overflow on for NAMES` | Ask the user to turn off paid overflow (usage beyond the plan) for those accounts on claude.ai, then run `check` again. |
| `fail paid overflow not read yet for NAMES` | Run `status` and report the account's reading line. The router reads paid overflow every 10 minutes by default. Run `check` again after the next reading. |
| `fail Claude Code settings do not point at the router` | Claude Code connects directly. To route again, print `claude-router setup`. |
| `fail no accepted record at PATH` | Print `./install.sh --component claude-router`, run from an astack checkout whose `components/claude-router/accepted.json` matches its code. |
| `fail code differs from the accepted record` | Report the indented lines below it. For `binary: installed X, running Y`, print `claude-router stop` and `claude-router start`. For a `code`, `helpers`, or `sdk` line, print the install command from the row above. |
| `fail test message got no answer through the router (REASON)` | Run `status` and report its problem lines. `claude exited 127` means `claude` is not on PATH. |

A refusal before the checks starts with `claude-router: check:` and ends with
a `next` line. Print that `next` command with the absolute path.

## Know what the router costs and refuses

A conversation stays on its account. When an account's included allowance runs
out, the router moves the conversation to another account, and the next
request writes that conversation's prompt cache again on the new account.
`cache` reports these moves and the cache writes they caused. `status` prints a
`cache` line when moves caused more than 10% of the last 24 hours' cache
writes.

The router sends requests only to accounts whose latest reading says paid
overflow is off, and `setup` refuses an account with paid overflow on. One gap
remains. If the user turns usage credits on at claude.ai after the last
reading, while the account's included windows are exhausted, one paid response
can happen before the router sees it.

## Set up from nothing

Print these steps for Terminal, run from the astack checkout. Linux uses the
same commands, with the differences in [Find the service](#find-the-service).
Linux setup is tested offline against a fake `systemctl`, not yet on real Linux.

1. Install Go 1.26 or newer from <https://go.dev/dl/>, Python 3.10 or newer,
   and Claude Code. `go version` and `claude --version` must work in the shell.
2. Download the pinned modules once, with network access:

   ```sh
   (cd components/claude-router && go mod download)
   ```

3. Build and install the router. It changes no Claude Code settings:

   ```sh
   ./install.sh --component claude-router
   ```

4. Put the command on PATH. Add the same line to `~/.zshrc` on macOS or
   `~/.bashrc` on Linux:

   ```sh
   export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin:$PATH"
   ```

5. Sign in each account, start the router, and activate it:

   ```sh
   claude-router setup -account work -account alumni
   ```

   It ends with the `check` lines and
   `activated Claude Code routes through the router`. When this machine's
   platform differs from the acceptance record's, as on Linux, it also prints
   a `note` line that names both.
   Then run `check` yourself.

## Roll back

Print `claude-router direct`. It stops the router and restores the Claude Code
settings that activation changed. Logins and assignments stay. To route again,
print `claude-router setup`. To remove the router, print
`./uninstall.sh --component claude-router`, run from the astack checkout. It
keeps logins and assignments.

## Find the service

| | macOS | Linux |
| --- | --- | --- |
| Service | launchd agent `dev.astack.claude-router` | systemd user unit `dev.astack.claude-router.service` |
| State | `launchctl print gui/$(id -u)/dev.astack.claude-router` | `systemctl --user status dev.astack.claude-router` |
| Logs | `~/.local/state/astack/claude-router/service.log` | `journalctl --user -u dev.astack.claude-router` |
| Login | Starts at each login | Starts at each login. To keep it running after the last session ends, the user runs `loginctl enable-linger "$USER"` once. |

Print the state and log commands for the user when `status` does not explain a
failure. State lives in `~/.local/state/astack/claude-router/` on both systems.
