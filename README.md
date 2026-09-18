# astack

Personal coding instructions and agent skills, deployable across macOS and Linux.
The instruction and skill content is a skeleton to fill in later; the installer
is ready to use. Installation also disables Claude Code's
automatic memory through its user settings; Codex settings are unchanged by default.

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

## Optional secret-scanning hook

Install a Gitleaks pre-push guard in a repository you choose:

```sh
python3 skills/open-pr/scripts/git_hooks.py install --repo /path/to/project --dry-run
python3 skills/open-pr/scripts/git_hooks.py install --repo /path/to/project
```

This is separate from `install.sh` and agent configuration. It runs on pushes
from any terminal or agent, across the selected repository's linked worktrees.
The installer downloads a pinned, checksum-verified Gitleaks release for macOS
or Linux (ARM64 or x64). It requires Python 3.10+ and Git 2.31+.

To replace an existing gstack-managed guard, use `--replace-gstack`. To install
offline, pass `--gitleaks /path/to/gitleaks` with a trusted executable of the
Gitleaks version that `VERSION` in `skills/open-pr/scripts/gitleaks_pre_push.py` pins. The
installer checks its version and copies it without a download.

The installer keeps an existing `pre-push` hook. If that hook is executable, the
guard runs it before the scan, with the original arguments and stdin, and a
rejection from it still blocks the push. A gstack-managed hook is the exception.
With `--replace-gstack`, the installer saves the gstack wrapper and the guard
does not run it. The guard runs only the wrapper's `pre-push.local`, if that
file exists and is executable. The installer refuses a custom `core.hooksPath`,
such as Husky's, and a symlinked hooks directory.

`uninstall.sh` does not remove the guard. Remove it with `skills/open-pr/scripts/git_hooks.py`,
which restores the earlier hook, or deletes `pre-push` when no earlier hook
existed:

```sh
python3 skills/open-pr/scripts/git_hooks.py uninstall --repo /path/to/project
```

Each push scans the commit patches it sends:

- For an existing branch, the guard scans every commit the remote branch lacks.
  A secret that a later commit deletes still blocks the push.
- For a new branch or tag, the guard reads the destination's current branch tips
  and scans commits those branches lack. It never trusts local remote-tracking
  refs. Unknown destination tips are not excluded, so the scan may include
  already-published history until you fetch. If the destination cannot be read,
  the push blocks.
- A shallow clone blocks the push until you run `git fetch --unshallow`.
- A missing remote tip blocks the push with a message that says to fetch the
  remote. The block clears once a fetch brings in that tip, which a
  single-branch clone needs `git fetch origin <branch>` for. A fetch may not
  bring it in for a forced tag push,
  when no fetched ref reaches the commit the remote tag points at. A tag that
  points at a blob or a tree gets the same message, but no fetch helps, because
  the guard cannot scan such a tag.

The guard uses the default Gitleaks rules. A `.gitleaks.toml`, a
`.gitleaksignore` file in the working tree, a `gitleaks:allow` comment, or a
`GITLEAKS_*` variable does not change them. A `-diff` or `binary` attribute does
not hide a path from the scan. The default rules skip some paths, including
extensions such as `.bin` and `.pdf`, lock files such as `package-lock.json`,
and anything under `node_modules`. The guard does not report a secret in a
skipped path.

A finding blocks the push, and its message gives the rule, path, line,
and commit without the secret. A scanner failure also blocks the push.
`git push --no-verify` skips the guard along with every other pre-push check.

To upgrade Gitleaks, update `VERSION` in `skills/open-pr/scripts/gitleaks_pre_push.py`, the
version, checksums, and `checksums_source` URL in `skills/open-pr/scripts/gitleaks/releases.json`,
and the version that the fake scanner in `tests/test_git_hooks.py` prints. Then
run the hook tests against the real scanner:

```sh
ASTACK_TEST_GITLEAKS=/path/to/gitleaks \
  python3 -m unittest discover -s tests -p test_git_hooks.py -v
```

Without `ASTACK_TEST_GITLEAKS`, the suite uses a fake scanner and skips the
real pushes. Set the variable to run the real-scanner tests locally.

An installed guard keeps its own copy of the scanner. Running `install` again
updates the Python runner to match the invoked bundle while preserving the
pinned scanner and chained hooks. Changes that fail the installation's integrity
checks are refused. To change the Gitleaks version or hook options, run
`uninstall`, then `install`.

## What to edit

```text
instructions/
  AGENTS.md           Shared global preferences for every harness
  CLAUDE.md           Claude Code additions
  CODEX.md            Codex additions
  GEMINI.md           Gemini CLI additions
  OPENCODE.md         OpenCode additions
skills/               Installable skills: <skill-name>/SKILL.md
settings/             Selected harness settings to merge (JSON sources)
templates/skill/      Starter to copy when creating a skill; never installed
upstream/manifest.json  Source repository, path, and commit of each adapted skill
harnesses.json        Destination paths and instruction overlays
scripts/manage.py     Install/update/uninstall implementation
scripts/astack_settings.py  Per-key JSON/TOML settings editing and restoration
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
specific `description` of at most 200 characters, since every description is
loaded into every session. Relative links in a skill's Markdown must resolve.
A skill with `disable-model-invocation: true` gets a Codex `agents/openai.yaml`
with `allow_implicit_invocation: false` generated at install time unless it
ships its own, which must agree with the flag. The installer performs basic checks; each harness handles
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

Installed instructions and skills are copies, so moving or deleting the checkout
does not break them. Settings are merged into the harness configuration. Edit
sources in the repo, commit/push your changes, then on each computer:

```sh
git pull --ff-only
./install.sh --target all          # Or the targets you use on this machine
```

Rerunning updates managed files and removes previously installed skill files
that have been deleted from the repo. It leaves other skills and configuration
alone. Restart the harness or reload its skills/context after installation.

For instructions and skills, the installer refuses to overwrite existing unmanaged
files or locally modified installed files, even if an unmanaged file has identical contents. Resolve the
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

Individual file writes are atomic, but an entire install/uninstall is not a
transaction. A crash or state-write failure after changing a file can leave its
ownership record out of sync. Rerun to identify conflicts and reconcile them;
use `--force` only after reviewing its backup preview.

```sh
./uninstall.sh --dry-run           # Claude Code + Codex by default
./uninstall.sh --target all
```

Uninstall removes only recorded, unmodified instruction/skill files. It preserves unrelated files,
empty directories, state, and backups. Local edits cause a conflict;
`./uninstall.sh --force` backs them up before removal. Uninstall does not
automatically restore old backups. If changing a configuration directory
override, uninstall that target first, then install at the new location.

## Managed harness settings

`settings/claude.json` supplies selected keys for Claude's user `settings.json`:

```json
{
  "autoMemoryEnabled": false
}
```

This disables automatic memory while retaining `CLAUDE.md` instructions and
existing memory files. It sets a user-level default; higher-priority project,
launch, or managed settings can override it. See
[Claude memory](https://code.claude.com/docs/en/memory) and
[settings precedence](https://code.claude.com/docs/en/settings).

`settings/codex.json` is initially `{}`: astack neither reads nor writes Codex's
`config.toml` until you add a preference. It uses the same JSON source format,
translated into native TOML. For example, to manage reasoning effort later:

```json
{
  "model_reasoning_effort": "high"
}
```

The destinations are `~/.claude/settings.json` and `~/.codex/config.toml`, honoring
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, and `--home`. Codex's configuration precedence
still applies; see [Codex configuration](https://learn.chatgpt.com/docs/config-file/config-basic).
An empty source stops managing its previously owned keys and restores them.

The installer owns individual leaf keys. Nested source objects select nested keys;
arrays are replaced as a whole. It records each key's original presence/value and
last installed value. On initial install, your selected preferences replace those
keys while retaining the rest of the configuration. Reinstall updates managed keys;
removing a source key or uninstalling restores its original value, or removes it
if it was originally absent. Unrelated settings and local edits are preserved.

Changes to or deletion of a managed key cause a conflict before installation starts.
`--force` backs up the current settings file and applies the selected preferences
or restoration. The first pre-install value remains the restoration baseline.
`--dry-run` names affected keys without printing their values or changing files.
Settings are never followed through destination symlinks, even with `--force`.
Malformed configuration and unsupported structures fail before any payload writes.

JSON may be reformatted when merged. TOML edits preserve unrelated text, comments,
and formatting, and retain original literals for restoration. Supported managed
TOML values are scalars and arrays of scalars (including nested arrays), under
ordinary or dotted tables. Managing a table itself, children of inline tables,
or children of arrays of tables is unsupported; unrelated instances are preserved.
Codex values cannot be JSON `null`. Change an owned scalar/object structure by
removing its managed keys and reinstalling before adding the new structure.

When nothing unrelated has changed, uninstall restores the original file exactly,
or removes a settings file astack created. Otherwise it restores only owned keys
and retains the file. Existing file permissions are preserved; newly created
settings files and the local ownership manifest use mode `0600`. The manifest
contains original configuration snapshots for restoration; keep it local.

Installer state version 1 migrates automatically to version 2. Use this installer
or a newer one to uninstall a version 2 installation; older installers reject it.
Settings writes are atomic per file, and a detected state-write failure rolls back
that settings file. An abrupt process/machine failure can still require manual
reconciliation. Run one installer at a time and avoid editing settings during it;
the installer checks for intervening edits before writing, but does not lock the
harness out of its configuration.

A bundled, licensed copy of the Python standard-library TOML 1.0 reader keeps
settings management compatible with Python 3.10+. No package installation or
network access is required.

## Test without touching your configuration

```sh
./install.sh --target all --home /tmp/astack-demo --dry-run
./install.sh --target all --home /tmp/astack-demo
./uninstall.sh --target all --home /tmp/astack-demo
python3 -m unittest discover -s tests -v
```

`--home` redirects installation and state, ignoring configuration environment
overrides. Tests use temporary homes and exercise install, repeat install,
updates, and uninstall for all four harnesses. They check local edits, unrelated
files, backup restoration, custom environment paths, moved checkouts, executable
helpers, binary assets, Unicode paths, symlinks, blocked state directories, and
retry after an injected file-write failure. This repository has no GitHub Actions
workflow. Run the suite locally.

Settings tests temporarily configure both Claude and Codex, reinstall updates,
then uninstall and check exact restoration. They also cover unrelated edits,
conflicts and backups, nested keys, TOML comments and multiline values, custom
roots, state migration, and rollback after a settings-state write failure. These
validate configuration files and installer behavior; they do not launch agent
sessions or change your real harness settings.

To run just the deployment tests:

```sh
python3 -m unittest discover -s tests -p 'test_install*.py' -v
```

These tests verify files and installer behavior. Before relying on a new harness
adapter or imported skill, also test discovery and invocation in that harness
using an isolated profile: confirm the global instructions take effect, invoke a
skill and its helpers, reinstall an update, then uninstall and restart the harness
to confirm removal. Skills with external runtime dependencies need their own
smoke tests; copying their files does not establish that those dependencies work.

## Add another harness

For harnesses that read a global Markdown file and `skills/<name>/SKILL.md`, add an
entry to `harnesses.json` and an overlay to `instructions/`. A destination combines
`default` (relative to home) with `suffix`, or uses the absolute directory from
`env` when set. Then use `./install.sh --target <name>`. Add a destination assertion
to the tests and document the official discovery paths here. Give each target
distinct destination paths so updates and removal have unambiguous ownership;
overlapping targets, including symlinked directory aliases, are rejected before
installation, even with `--force`.

Harnesses requiring different rule formats, plugin packages, or settings formats
need a dedicated adapter. This repo distributes instructions, skills, and selected
harness preferences; credentials and harness binaries stay machine-specific.

## Adapted skills

Skills adapted from other repositories record their source repository, path, and
commit in `upstream/manifest.json` and keep the upstream LICENSE in the skill
directory. To see what changed upstream since, clone the source and diff from
the pinned commit to its head.

For imports with a `files` mapping, its keys are paths relative to the upstream
`path`; values are current astack paths relative to this repository. A `null`
value records an intentional omission, explained in the entry's `note`. Entries
follow the source skill even when its files move into multiple astack directories.

Compare upstream changes from the pinned commit separately from our adaptations:
use `git diff <pinned-commit>..<upstream-head> -- <upstream-path>` in the source
clone, then compare the pinned source files with their mapped astack files.
Check the whole upstream directory for new files as well as the mapped files.
Advance the pin only after reviewing and reconciling the upstream changes.
