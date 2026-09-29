---
name: background-jobs
description: Run long background jobs and watchers with a deadline and a wake-up on completion. Use for slow tests, builds, or waiting on a ready line or CI result.
---

# Wake when a job finishes

Run each long command that finishes on its own through `scripts/run.py`, with a
deadline well past its normal duration. Choose an authorized, noninteractive
command. Preserve the project's test locks, worker limits, and execution
wrappers. Run the work in the command's foreground. A launcher that detaches its
worker exits before the work finishes.

`scripts/run.py` inherits the current directory and environment, closes stdin,
and runs the command in its own process group. At the deadline, it stops the
group and exits with code 124. Pass the command as separate arguments after
`--`. For shell syntax, use `sh -c '...'`.

## Claude Code

The Bash tool's `timeout` parameter does not stop a `run_in_background` command.
Run the helper as the `run_in_background` command. Claude Code wakes you when it
exits.

```sh
python3 <skill-directory>/scripts/run.py --timeout 1800 -- make test
```

To wait for a condition, such as a ready line or a CI result, run the watcher
through the helper the same way, such as
`run.py --timeout 5400 -- gh pr checks 42 --watch`. Make the watcher exit on
every outcome, including failure and error lines. Start a long-lived process
that produces the condition, such as a dev server, in its own plain
`run_in_background` call without the helper. Its exit wakes you if it crashes.

Have the watcher print its first check before it sleeps. Before you end the turn,
confirm in its output file that the check ran without errors. Stop a job or
watcher you replace or no longer need with TaskStop.

## Codex

Add `--codex` to queue completion to the current thread, following
[CODEX.md](CODEX.md).
