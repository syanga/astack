# astack

Personal coding instructions and agent skills, deployable across macOS and Linux.
The content is a skeleton to fill in later; the installer is ready to use.

## Quick start

Requirements: Git, a POSIX shell, and Python 3.10 or newer. No Python packages,
Node dependencies, or administrator privileges are needed. Install and sign in
to your agent harnesses separately.

```sh
git clone https://github.com/syanga/astack.git
cd astack
./install.sh --dry-run
./install.sh                      # Claude Code + Codex
```

Select individual harnesses, repeat `--target`, or install all four:

```sh
./install.sh --target claude
./install.sh --target codex --target gemini
./install.sh --target all
```

The scripts work from any working directory, including paths containing spaces.
They install configuration files even if a harness is not installed yet.

## What to edit

```text
instructions/
  AGENTS.md           Shared global preferences for every harness
  CLAUDE.md           Claude Code additions
  CODEX.md            Codex additions
  GEMINI.md           Gemini CLI additions
  OPENCODE.md         OpenCode additions
skills/               Installable skills: <skill-name>/SKILL.md
templates/skill/      Starter to copy when creating a skill; never installed
harnesses.json        Destination paths and instruction overlays
scripts/manage.py     Install/update/uninstall implementation
install.sh            Installation entry point
uninstall.sh          Removal entry point
AGENTS.md             Instructions for working on this repository only
CLAUDE.md             Claude entry point for this repository only
```

The installer concatenates `instructions/AGENTS.md` and the selected harness's
addition into its global instruction file. Use the shared file for preferences
you want everywhere. Root-level `AGENTS.md` and `CLAUDE.md` are separate repo
instructions and are not deployed. These uppercase filenames are intentional:
the harnesses use them for discovery on case-sensitive Linux filesystems.

Create a skill:

```sh
cp -R templates/skill skills/my-skill
# Edit skills/my-skill/SKILL.md: name must be my-skill; replace the description/body.
./install.sh
```

Use lowercase letters, digits, and single hyphens for names (at most 64 characters).
Each `SKILL.md` needs YAML frontmatter with `name` matching its directory and a
specific `description`. The installer performs basic checks; each harness handles
full YAML validation and skill activation. Use simple frontmatter as in the
template for portability.

Add `scripts/`, `references/`, or `assets/` inside a skill when needed. Supporting
files and executable modes are copied too. Hidden files, Python bytecode, and
`__pycache__` are excluded; source symlinks are rejected. Keep skills self-contained
and avoid harness-specific tool names when sharing them across agents.

## Installed locations

| Target | Global instructions | Skills |
| --- | --- | --- |
| `claude` | `~/.claude/CLAUDE.md` | `~/.claude/skills/<name>/` |
| `codex` | `~/.codex/AGENTS.md` | `~/.agents/skills/<name>/` |
| `gemini` | `~/.gemini/GEMINI.md` | `~/.gemini/skills/<name>/` |
| `opencode` | `~/.config/opencode/AGENTS.md` | `~/.config/opencode/skills/<name>/` |

`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `XDG_CONFIG_HOME` override their respective
configuration roots. Codex's shared user skill location stays `~/.agents/skills`
when `CODEX_HOME` changes. Overrides must be absolute paths. Gemini uses its
default home location. Custom harness discovery settings may change which files
are loaded; for example, Codex's `AGENTS.override.md` takes precedence over
`AGENTS.md`.

Gemini and OpenCode can also discover shared or Claude skill locations. Installing
multiple targets places the same skill content at their native paths; installing
a skill for one target can therefore expose it to other compatible harnesses.
Per-target installation is not an isolation boundary.

Paths follow the official docs:
[Claude configuration](https://code.claude.com/docs/en/claude-directory),
[Codex instructions](https://developers.openai.com/codex/guides/agents-md/) and
[skills](https://developers.openai.com/codex/skills/),
[Gemini instructions](https://geminicli.com/docs/cli/gemini-md/) and
[skills](https://geminicli.com/docs/cli/skills/),
[OpenCode instructions](https://opencode.ai/docs/rules/) and
[skills](https://opencode.ai/docs/skills/).

## Update and remove

Installed files are copies, so moving or deleting the checkout does not break
them. Edit sources in the repo, commit/push your changes, then on each computer:

```sh
git pull --ff-only
./install.sh --target all          # Or the targets you use on this machine
```

Rerunning updates managed files and removes previously installed skill files
that have been deleted from the repo. It leaves other skills and configuration
alone. Restart the harness or reload its skills/context after installation.

The installer refuses to overwrite existing unmanaged files or locally modified
installed files, even if an unmanaged file has identical contents. Resolve the
conflict manually, or explicitly back up and replace conflicting files:

```sh
./install.sh --force --dry-run
./install.sh --force
```

State is stored at `${XDG_STATE_HOME:-~/.local/state}/astack/manifest.json`.
Backups live in `astack/backups/<unique-id>/` under the same state directory,
mirroring the original absolute path. The script prints each backup location.
Backups are retained; restore them manually after uninstalling if desired.
Do not delete the manifest while using the installer to manage those files.
Run only one installer/uninstaller at a time.

```sh
./uninstall.sh --dry-run           # Claude Code + Codex by default
./uninstall.sh --target all
```

Uninstall removes only recorded, unmodified files. It preserves unrelated files,
empty directories, state, and backups. Local edits cause a conflict;
`./uninstall.sh --force` backs them up before removal. Uninstall does not
automatically restore old backups. If changing a configuration directory
override, uninstall that target first, then install at the new location.

## Test without touching your configuration

```sh
./install.sh --target all --home /tmp/astack-demo --dry-run
./install.sh --target all --home /tmp/astack-demo
./uninstall.sh --target all --home /tmp/astack-demo
python3 -m unittest discover -s tests -v
```

`--home` redirects installation and state, ignoring configuration environment
overrides. Tests use temporary homes and check conflicts, backups, updates,
uninstall, executable skills, symlinks, and paths containing spaces. GitHub Actions
runs the tests on macOS and Ubuntu.

## Add another harness

For harnesses that read a global Markdown file and `skills/<name>/SKILL.md`, add an
entry to `harnesses.json` and an overlay to `instructions/`. A destination combines
`default` (relative to home) with `suffix`, or uses the absolute directory from
`env` when set. Then use `./install.sh --target <name>`. Add a destination assertion
to the tests and document the official discovery paths here. Give each target
distinct destination paths so updates and removal have unambiguous ownership;
overlapping targets are rejected before installation.

Harnesses requiring different rule formats, plugin packages, or settings changes
need a dedicated adapter. This repo currently distributes instructions and
skills; credentials, model settings, MCP servers, and harness binaries stay
machine-specific.
