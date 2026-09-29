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
- Every word is plain text made of letters, digits, and `_ . / : = @ % + , -`, or
  text in single quotes. Anything the shell would expand or interpret, such as
  `$`, a backtick, a double quote, a backslash, `~`, a glob, a brace, a newline,
  or an operator, denies the call.

Shell syntax, `cd`, and `VAR=value` go inside the command, as `sh -c '...'` or
`env VAR=value`, where they are single-quoted text to the hook. A denied call
gets a message with the helper's absolute path in the command forms for a
finite job and a long-lived process. It allows every other call.

The hook checks the command text, not the shell it runs in. A shell function or
alias named `python3` in the user's own shell setup is outside what it checks.

The hook fails open. If the event is malformed, or the script or `python3` is
missing, the hook exits with code 1. Claude Code reports the hook error and runs
the command.
