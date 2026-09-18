---
name: update-astack
description: Update or reinstall astack.
disable-model-invocation: true
---

# Update astack

Fetch astack's upstream, then fast-forward its clean `main` worktree to remote main. Stop on local changes or unpublished commits. Preserve task branches.

Run `./install.sh` from main even when already current. Preserve installed harness targets unless the user specifies others. Stop on installer conflicts instead of forcing replacement.
