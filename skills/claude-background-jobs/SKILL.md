---
name: claude-background-jobs
description: Claude Code hook that denies background Bash commands that do not run through the run-job helper.
disable-model-invocation: true
---

# Deny unbounded Claude Code background jobs

`scripts/hook.py` is a Claude Code `PreToolUse` hook for the Bash tool. It denies
a `run_in_background` call unless the command runs the helper from the
[`run-job`](../run-job/SKILL.md) skill, written literally:

```sh
python3 /absolute/path/to/run-job/scripts/run_job.py ARG ...
```

- `python3` can also be `python3.N` or an absolute path to either name.
- The helper path must be the installed `run_job.py`, written as an absolute path.
- Each word is made of letters, digits, `_ . / : = @ % + , -`, and single-quoted
  text. Any other character, such as `$`, `~`, a double quote, or a newline,
  denies the call.

Put shell syntax and `cd` inside `sh -c '...'`, and set variables with
`env VAR=value`. The deny message gives the command forms for a finite job and a
long-lived process, with the helper's absolute path.

The hook checks only the command text. Shell functions and aliases in the user's
shell setup, including zsh global aliases that expand inside arguments, can still
change what runs.

The hook fails open. If the event is malformed, or the script or `python3` is
missing, the hook exits with code 1. Claude Code reports the hook error and runs
the command.
