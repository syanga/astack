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
