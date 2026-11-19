---
name: astack-update
description: Update or reinstall astack.
disable-model-invocation: true
---

# Update astack

Treat invocation as authorization to update and reinstall astack without another confirmation.

Fetch astack's upstream, then fast-forward its `main` worktree to remote main when possible. Leave unrelated untracked files and directories in place. If local edits, unpublished commits, or conflicting paths prevent the fast-forward, install from a separate clean worktree at remote main. Preserve the existing work and task branches.

Run `./install.sh` from the updated checkout even when already current. Choose its targets this way:

- When the astack manifest, `${XDG_STATE_HOME:-$HOME/.local/state}/astack/manifest.json`, exists, pass `--target` for each target under its `targets`. Also pass `--target jobgate`, even when `targets` has no `jobgate`.
- Without a manifest, pass no `--target`, so the installer installs its default targets.
- When the user names targets, pass only those, in place of both cases above.

For a `--home` installation, read that home's `.local/state/astack/manifest.json` instead, and pass the same `--home` to every `./install.sh` call. When a conflict is on a `jobgate` file and the manifest's `targets` has no `jobgate`, rerun without `--target jobgate` and report the conflicting files. For other installer conflicts, use the installer's `--force` option to back up conflicting files and complete the upgrade. Report any backup locations, and any `APPEND`, `SKIP PATH`, or `WARN` line, with the result.

When the manifest lists `claude-router` under `components`, update the router after the harness install succeeds:

1. Run `python3 scripts/claude_router.py identity --code-only` in the updated checkout. Compare `components/claude-router/accepted.json` with it:
   - `result` is `pass`.
   - `sdk` equals the CLIProxyAPI version in `components/claude-router/go.mod`.
   - `code_sha256` and `helpers_sha256` equal the printed digests.

   When any of these differs, leave the router as it is and report the difference. Over an activated router, the install's check would fail and switch Claude Code to direct.
2. Otherwise run `./install.sh --component claude-router < /dev/null`, without `--force`, with a 600-second timeout. An activated router restarts, which ends requests other routed sessions have in flight.
3. Report any `APPEND`, `SKIP`, or `RESTART` line, the check lines after `CHECK`, or the `astack:` line when it exits 1.
