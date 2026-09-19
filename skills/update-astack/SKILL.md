---
name: update-astack
description: Update or reinstall astack.
disable-model-invocation: true
---

# Update astack

Treat invocation as authorization to update and reinstall astack without another confirmation.

Fetch astack's upstream, then fast-forward its `main` worktree to remote main when possible. Leave unrelated untracked files and directories in place. If local edits, unpublished commits, or conflicting paths prevent the fast-forward, install from a separate clean worktree at remote main. Preserve the existing work and task branches.

Run `./install.sh` from the updated checkout even when already current. Preserve installed harness targets unless the user specifies others. For installer conflicts, use the installer's `--force` option to back up conflicting files and complete the upgrade. Report any backup locations with the result.
