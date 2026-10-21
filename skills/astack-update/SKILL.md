---
name: astack-update
description: Update or reinstall astack.
disable-model-invocation: true
---

# Update astack

Treat invocation as authorization to update and reinstall astack without another confirmation.

Fetch astack's upstream, then fast-forward its `main` worktree to remote main when possible. Leave unrelated untracked files and directories in place. If local edits, unpublished commits, or conflicting paths prevent the fast-forward, install from a separate clean worktree at remote main. Preserve the existing work and task branches.

Run `./install.sh` from the updated checkout even when already current. Preserve installed harness targets unless the user specifies others. For installer conflicts, use the installer's `--force` option to back up conflicting files and complete the upgrade. Report any backup locations with the result.

When the astack manifest, `${XDG_STATE_HOME:-$HOME/.local/state}/astack/manifest.json`, lists `claude-router` under `components`, update the router after the harness install succeeds:

1. Run `python3 scripts/claude_router.py identity --code-only` in the updated checkout. Compare `components/claude-router/accepted.json` with it:
   - `result` is `pass`.
   - `sdk` equals the CLIProxyAPI version in `components/claude-router/go.mod`.
   - `code_sha256` and `helpers_sha256` equal the printed digests.

   When any of these differs, leave the router as it is and report the difference. Over an activated router, the install's check would fail and switch Claude Code to direct.
2. Otherwise run `./install.sh --component claude-router < /dev/null`, without `--force`, with a 600-second timeout. An activated router restarts, which ends requests other routed sessions have in flight.
3. Report any `APPEND`, `SKIP`, or `RESTART` line, the check lines after `CHECK`, or the `astack:` line when it exits 1.
