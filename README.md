# astack

astack is Alan's agent setup. I use it in [T3 Code](https://t3.codes) with
Claude Code and Codex. It is based on
[pstack](https://github.com/cursor/plugins/tree/main/pstack),
[Matt Pocock's skills](https://github.com/mattpocock/skills),
[gstack](https://github.com/garrytan/gstack), and
[tstack](https://github.com/t3dotgg/tstack). See [Credits](#credits).

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
Installation needs network access to install and refresh the plugins listed in
[settings/plugins.json](settings/plugins.json) through each agent's plugin CLI.
It skips agents whose CLI is not on `PATH`.

## Customize

Edit the source files, then rerun `./install.sh` with the chosen targets.
Installed instructions and skills are copies, so moving the checkout does not
break them. Restart the agent or reload its instructions and skills after installation.

The repository defines what gets installed:

| Source | Purpose |
| --- | --- |
| [instructions/](instructions/) | Shared preferences and additions for each agent |
| [skills/](skills/) | Available skills and their usage instructions |
| [settings/](settings/) | Settings and plugins to manage |
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
explains [how routing works](components/claude-router/README.md#how-the-service-routes)
and [the service on each system](components/claude-router/README.md#operate-the-service).

To install and activate the router:

1. Install Python 3.10 or newer, Go 1.26 or newer from <https://go.dev/dl/>,
   and Claude Code. `python3 --version`, `go version`, and `claude --version`
   must work in your shell. The installer fails with a `TypeError` under the
   Python 3.9 that ships with macOS.
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

4. When the install asks `Add claude-router to PATH in FILE? [Y/n]`, press
   Enter. When it prints `SKIP PATH` instead, add the line it prints to your
   shell's startup file. Then open a new shell.

5. On Linux, turn on linger so the router runs without a login session
   ([why](components/claude-router/README.md#operate-the-service)):

   ```sh
   sudo loginctl enable-linger "$USER"
   ```

6. Sign in each account and activate the router, with your own labels for
   the accounts. Each sign-in opens a browser window, or prints a URL with
   `-no-browser`:

   ```sh
   claude-router setup -account work -account alumni
   ```

   Activation points Claude Code's settings at the router. `setup` ends with
   `activated Claude Code routes through the router`. If it refuses because
   an account has paid overflow on, turn paid overflow off on claude.ai, run
   `claude-router stop`, and run `setup` again. For any other refusal, follow
   its `next` line.
7. Start a new Claude Code session, because a session reads its settings when
   it starts.
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
activated router stop or restart the router. That cuts off routed Claude Code
sessions in the middle of a turn, so run them while no session is working.

To roll back to a direct connection, run `claude-router direct` and start a
new Claude Code session. If `direct` prints
`after   claude needs its own /login`, run `/login` in that session.
`claude-router setup` activates the router again.
`./uninstall.sh --component claude-router` removes it. `direct` and uninstall
keep logins and assignments.

## Keep a Linux machine responsive when memory runs out

When agents and test suites fill a Linux machine's RAM, the kernel evicts the
code of running programs. The desktop then freezes until the OOM killer ends a
process. [scripts/memory_guard.py](scripts/memory_guard.py) turns on the
kernel's
[thrashing prevention](https://docs.kernel.org/admin-guide/mm/multigen_lru.html#thrashing-prevention)
by setting `/sys/kernel/mm/lru_gen/min_ttl_ms` to 1000. The kernel then ends a
process instead of evicting pages used in the last second. At each boot,
`systemd-tmpfiles` sets the value again from
`/etc/tmpfiles.d/astack-memory-guard.conf`. The script needs the kernel's
multi-gen LRU and `systemd-tmpfiles`. It changes nothing on macOS.

When thrashing prevention acts, the kernel ends the process with the highest
OOM score. That is usually the largest process, which can be an agent session.

To check the machine, preview the change, and make it:

```sh
python3 scripts/memory_guard.py status
python3 scripts/memory_guard.py install --dry-run
sudo python3 scripts/memory_guard.py install
```

To preview and revert the change:

```sh
python3 scripts/memory_guard.py uninstall --dry-run
sudo python3 scripts/memory_guard.py uninstall
```

`uninstall` restores the value that `min_ttl_ms` had before the first `install`.

### Move /tmp from RAM to disk

A tmpfs `/tmp` keeps its files in RAM, and the kernel can free that memory only
by writing the files to swap. On disk, files in `/tmp` live in the page cache,
which the kernel can free.
`findmnt /tmp` shows `tmpfs` in the `FSTYPE` column when `/tmp` is in RAM.

To move `/tmp` to disk at the next reboot:

1. If `/etc/fstab` has a line that mounts a tmpfs on `/tmp`, comment out that
   line with `sudoedit /etc/fstab`.
2. Run `sudo systemctl daemon-reload`.
3. Run `systemctl cat tmp.mount`. Its first line names a unit file.
   - If the unit file is `/run/systemd/generator/tmp.mount`, `/etc/fstab`
     still mounts `/tmp`. Do not mask `tmp.mount` in this state, because the
     boot can then stop in emergency mode. Go back to step 1.
   - If the unit file is another file with `Type=tmpfs`, run
     `sudo systemctl mask tmp.mount`. Ubuntu 26.04 ships such a unit file.
   - If the command prints `No files found for tmp.mount`, go to step 4.
4. Reboot. `findmnt /tmp` then prints nothing.

On disk, `/tmp` keeps files until `systemd-tmpfiles` deletes them.
`systemd-tmpfiles-clean.timer` starts `systemd-tmpfiles` once a day, and
`/usr/lib/tmpfiles.d/tmp.conf` sets the age limit, 10 days on Ubuntu 26.04.

To move `/tmp` back to RAM:

1. Run `sudo systemctl unmask tmp.mount`.
2. Restore any `/etc/fstab` line that you commented out.
3. Reboot.

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
gstack, and tstack, linked at the top of this page.
astack is released under the [MIT License](LICENSE). Adapted skills retain
their upstream licenses, kept in each skill's directory. The
[upstream manifest](upstream/manifest.json) records sources and revisions.
