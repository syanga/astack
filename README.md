# astack

astack is Alan's agent setup. I use it in [T3 Code](https://t3.codes) with
Claude Code and Codex. It is based on
[pstack](https://github.com/cursor/plugins/tree/main/pstack),
[Matt Pocock's skills](https://github.com/mattpocock/skills), and
[gstack](https://github.com/garrytan/gstack). See [Credits](#credits).

It keeps coding agents' instructions, skills, and settings in one repository
and installs them across macOS and Linux machines.

astack provides:

- Shared instructions with additions for each agent.
- Reusable skills, including their scripts and supporting files.
- Selected settings merged into agent configuration without replacing unrelated keys.
- Repeatable installation and removal, with conflict checks for local edits and
  restoration of managed settings to their original values.
- An optional [Gitleaks pre-push hook](skills/open-pr/scripts/git_hooks.py)
  to scan commits for secrets. Install and remove it separately from agent configuration.

## Install

Installation requires Git, a POSIX shell, and Python 3.10 or newer. Install coding agents
separately.

```sh
git clone https://github.com/syanga/astack.git
cd astack
./install.sh --help
./install.sh --dry-run
./install.sh
```

Use `--help` to see supported targets and defaults. To choose agents, pass
`--target <name>` for each one, or `--target all` for every supported agent.
Use the same targets for the preview and installation.

## Customize

Edit the source files, then rerun `./install.sh` with the chosen targets.
Installed instructions and skills are copies, so moving the checkout does not
break them. Restart the agent or reload its instructions and skills after installation.

The repository defines what gets installed:

| Source | Purpose |
| --- | --- |
| [instructions/](instructions/) | Shared preferences and additions for each agent |
| [skills/](skills/) | Available skills and their usage instructions |
| [settings/](settings/) | Settings to manage |
| [harnesses.json](harnesses.json) | Supported agents, destinations, and configuration sources |

In a JSON destination, a source value of `{"$entries": [...]}` adds its entries
to the list at that key and keeps the list's other entries. Uninstall removes
only the entries it added.

For contribution rules, see [AGENTS.md](AGENTS.md).

## Install the optional Claude router

[components/claude-router](components/claude-router/README.md) is an optional
local proxy that routes Claude Code conversations across several Claude
subscription accounts. With one Claude account, you do not need it. The
default targets and `--target all` never install it.

Linux is tested on a real Linux kernel with a systemd user manager in a
container (Debian 13, systemd 257, arm64), with fake accounts. It is not yet
tested with real accounts on a Linux machine.

A conversation stays on its account until that account's included allowance
runs out. The router then moves the conversation to another account. The next
request writes the conversation's prompt cache again on the new account.
`claude-router cache` reports the moves and the cache writes they caused.

Paid overflow is the claude.ai setting that bills usage past the plan's
included allowance, shown there as extra usage or usage credits. The router
sends requests only to accounts whose latest reading says paid overflow is
off, and `setup` refuses an account with it on.
If you turn paid overflow on at claude.ai after the last reading, while the
account's included allowance is used up, one paid response can happen before
the router sees it.

To install and activate the router:

1. Install Go 1.26 or newer from <https://go.dev/dl/>, and Claude Code.
   `go version` and `claude --version` must work in your shell. Linux
   distribution packages of Go are often older than 1.26.
2. From the checkout, download the pinned Go modules once, with network access:

   ```sh
   (cd components/claude-router && go mod download)
   ```

3. Build and install the router. The install changes no Claude Code settings
   and does not start the router:

   ```sh
   ./install.sh --component claude-router --dry-run
   ./install.sh --component claude-router
   ```

   It adds a launchd agent on macOS or a systemd user unit on Linux, both
   named `dev.astack.claude-router`.
4. The installer does not put `claude-router` on PATH. Run this line, and add
   it to `~/.zshrc` on macOS or `~/.bashrc` on Linux:

   ```sh
   export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin:$PATH"
   ```

5. On Linux, let the router run without a login session:

   ```sh
   sudo loginctl enable-linger "$USER"
   ```

   With linger, the systemd user unit starts at boot. Without it, the unit
   starts at login and stops about 15 seconds after your last session ends,
   so Claude Code in tmux over SSH loses its router when you log out.
6. Sign in each account, start the router, and point Claude Code at it. Name
   each account with your own label:

   ```sh
   claude-router setup -account work -account alumni
   ```

   Each sign-in opens a browser window. Sign in to claude.ai with that
   account. With `-no-browser`, `setup` prints the sign-in URL instead. `setup`
   prints the `check` lines, then
   `activated Claude Code routes through the router` and
   `next      claude-router status`.

   If `setup` refuses because an account has paid overflow on, turn it off on
   claude.ai, then run `claude-router stop` and `claude-router setup`. The
   running router keeps its reading for up to 10 minutes, so a rerun without
   the `stop` can refuse again.
7. Check the router at any time. It sends one short test message:

   ```sh
   claude-router check
   ```

   ```text
   ok        router running on 127.0.0.1:18787 (launchd)
   ok        work, alumni ready
   ok        paid overflow off on both accounts
   ok        Claude Code settings point at the router
   ok        code matches the accepted record (darwin/arm64)
   ok        test message answered 200 on work in 5.8s
   check     passed
   ```

   On Linux, the first line ends with `(systemd)`. The fifth line names the
   platform the record was accepted on, not this machine, so it reads
   `(darwin/arm64)` on Linux too. When the two differ, `setup` also prints a
   line such as
   `note      accepted on darwin/arm64, this is linux/arm64, not yet tested on real Linux`.

Use these commands day to day:

```sh
claude-router status
claude-router cache
claude-router account add school
claude-router account remove school
```

`status` prints a `cache` line when account moves caused more than 10% of the
last 24 hours' cache writes. `account remove` refuses while the account has
conversations from the last 5 hours, unless you pass `-force`.

To roll back to a direct connection, run `claude-router direct`. It stops the
router and restores the Claude Code settings that activation changed. In a
shell without the PATH line, run:

```sh
"${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin/claude-router" direct
```

If `direct` prints `after   claude needs its own /login`, run `/login` in
Claude Code. To route again, run `claude-router setup`. To remove the router,
run `./uninstall.sh --component claude-router`. `direct` and uninstall both
keep logins and assignments.

The component README explains configuration, the service on each system,
and every command.

## Find a skill

Ask [astack-help](skills/astack-help/SKILL.md) which skills fit the current task.
It reads the installed skills and recommends what to use next.

## Update or remove

After pulling source changes, rerun the installer with the same targets.
It updates managed files and removes skill files deleted from the source.
Conflicting local edits stop installation. To back up and replace conflicts,
preview with `--force --dry-run` before running with `--force`.

astack-help offers this update when any agent's installed commit differs from
`main` on the checkout's `origin`.

To remove an installation, use the same targets with `./uninstall.sh`.
Preview removal with `--dry-run`. Uninstall preserves unrelated files and
restores managed settings. It does not restore file backups automatically.

## Verify changes

Run the test suite, which uses temporary directories:

```sh
python3 -m unittest discover -s tests -v
```

To preview installation without using the existing agent configuration, run
`./install.sh --target all --home /tmp/astack-demo --dry-run`.

## Credits

astack is based on pstack, Matt Pocock's skills,
and gstack, linked at the top of this page.
astack is released under the [MIT License](LICENSE). Adapted skills retain
their upstream licenses, kept in each skill's directory. The
[upstream manifest](upstream/manifest.json) records sources and revisions.
