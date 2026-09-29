---
name: claude-background-jobs
description: Claude Code hook that denies background Bash commands that do not run through the run-job helper.
disable-model-invocation: true
---

# Deny unbounded Claude Code background jobs

`scripts/hook.py` is a Claude Code `PreToolUse` hook for the Bash tool. It allows
every call except a `run_in_background` call whose command does not run through
the helper in the [`run-job`](../run-job/SKILL.md) skill. The hook accepts only
this shape:

```sh
[cd DIR &&] [NAME=VALUE ...] [exec] python3 CORE [ARG ...]
```

- `python3` can also be `python3.N` or an absolute path to either name.
- `CORE` must resolve to the installed `run-job/scripts/run_job.py`. Write it as
  an absolute path, or start it with `~`, `$HOME`, `${HOME}`, or
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`.
- Outside quotes, any `;`, `|`, `&`, `(`, `)`, `<`, `>`, newline, `$(...)`, or
  backtick denies the call. Inside quotes, these characters are text.

Put pipes, redirections, and other shell syntax inside `sh -c '...'`. The hook
also denies a redirection of the helper itself, because opening a FIFO or a
device can block before the helper starts. A denied call gets a message with the
exact commands to run instead.

The hook fails open. If the event is malformed, or the script or `python3` is
missing, the hook exits with code 1. Claude Code reports the hook error and runs
the command.
