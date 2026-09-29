---
name: claude-background-jobs
description: Claude Code hook that denies background Bash commands that do not run through the run-job helper.
disable-model-invocation: true
---

# Deny unbounded Claude Code background jobs

`scripts/hook.py` is a Claude Code `PreToolUse` hook for the Bash tool. It denies
a `run_in_background` call unless the command runs the helper from the
[`run-job`](../run-job/SKILL.md) skill. It allows every other call. A background
command must have this shape:

```sh
[cd DIR &&] [NAME=VALUE ...] [exec] python3 CORE [ARG ...]
```

- `python3` can also be `python3.N` or an absolute path to either name.
- `CORE` must resolve to the installed `run-job/scripts/run_job.py`. Write it as
  an absolute path, or start it with `~/`, `$HOME`, `${HOME}`, or
  `${CLAUDE_CONFIG_DIR:-$HOME/.claude}`.
- An unquoted, unescaped `;`, `|`, `&`, `(`, `)`, `<`, `>`, newline, `$(...)`, or
  backtick denies the call, and so do `$(...)` and backticks inside double quotes.
  Inside single quotes, all of these are text.

Shell syntax inside `sh -c '...'` is a quoted argument, so the hook allows it.
Redirections are denied because opening a FIFO or a device can block before the
helper starts. A denied call gets a message with the helper's absolute path in
the command forms for a finite job and a long-lived process.

The hook fails open. If the event is malformed, or the script or `python3` is
missing, the hook exits with code 1. Claude Code reports the hook error and runs
the command.
