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
default targets and `--target all` never install it. The component README
explains [what an account move costs](components/claude-router/README.md#how-the-service-routes),
how the router refuses paid overflow, and
[the service on each system](components/claude-router/README.md#operate-the-service).

Linux is tested on a real kernel in a Debian 13 arm64 container with systemd
257 and fake accounts, not yet with real accounts on a Linux machine.

To install and activate the router:

1. Install Python 3.10 or newer, Go 1.26 or newer from <https://go.dev/dl/>,
   and Claude Code. `python3 --version`, `go version`, and `claude --version`
   must work in your shell. The `python3` that ships with macOS is 3.9, and
   the installer fails with a `TypeError` under it.
2. From the checkout, download the pinned Go modules once, with network access:

   ```sh
   (cd components/claude-router && go mod download)
   ```

3. Build and install the router. The install does not start the router or
   change Claude Code settings:

   ```sh
   ./install.sh --component claude-router --dry-run
   ./install.sh --component claude-router
   ```

4. The installer does not put `claude-router` on PATH. Run this line:

   ```sh
   export PATH="${XDG_DATA_HOME:-$HOME/.local/share}/astack/claude-router/bin:$PATH"
   ```

   To keep it in new shells, add the same line to `~/.zshrc` on macOS or
   `~/.bashrc` on Linux.
5. On Linux, turn on linger so the router runs without a login session
   ([why](components/claude-router/README.md#operate-the-service)):

   ```sh
   sudo loginctl enable-linger "$USER"
   ```

6. Sign in each account and activate the router, with your own labels for
   the accounts:

   ```sh
   claude-router setup -account work -account alumni
   ```

   Each sign-in opens a browser window. Sign in to claude.ai with that
   account. With `-no-browser`, `setup` prints the sign-in URL instead of
   opening a browser. Activation points Claude Code's settings at the router.
   `setup` ends with `activated Claude Code routes through the router`. If it
   refuses because an account has paid overflow on, turn paid overflow off on
   claude.ai. Then run `claude-router stop` before you run `setup` again. For
   any other refusal, follow its `next` line and
   [Enroll accounts and start](components/claude-router/README.md#enroll-accounts-and-start).
7. Start a new Claude Code session. A new session reads the changed settings
   when it starts.
8. Check the router. It sends one short test message and ends with
   `check     passed`:

   ```sh
   claude-router check
   ```

Use these commands day to day:

```sh
claude-router status
claude-router cache
claude-router account add school
claude-router account remove school
```

`cache` reports the account moves of the last 24 hours and the prompt cache
writes they caused.

`account add`, `account remove`, `stop`, `direct`, and an install over an
activated router stop or restart the router. Each one cuts off routed Claude
Code sessions in the middle of a turn. Run them while no session is working.

To roll back to a direct connection, run `claude-router direct`. Then start a
new Claude Code session. If `direct` prints
`after   claude needs its own /login`, run `/login` in that session. To
activate the router again, run `claude-router setup`. To remove the router,
run `./uninstall.sh --component claude-router`. `direct` and uninstall both
keep logins and assignments.

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
