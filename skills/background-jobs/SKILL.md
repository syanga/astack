---
name: background-jobs
description: Run long background jobs and watchers with a deadline and a wake-up on completion. Use for slow tests, builds, or waiting on a ready line or CI result.
---

# Wake when a job finishes

Give every long background command a deadline. Choose an authorized,
noninteractive command and a deadline well past its normal duration. Preserve
the project's test locks, worker limits, and execution wrappers. Run the work in
the command's foreground. A launcher that detaches its worker exits before the
work finishes.

The helper runs the command in its own process group with stdin closed and the
current directory and environment. At the deadline, it stops the group and exits
with code 124. Pass the command as separate arguments after `--`. For shell
syntax, use `sh -c '...'`.

## Claude Code

The Bash tool's `timeout` parameter does not stop a `run_in_background` command.
Run the helper as the `run_in_background` command. Claude Code wakes you when it
exits, and TaskStop stops the job.

```sh
python3 <skill-directory>/scripts/run.py --timeout 1800 -- make test
```

To wait for a condition, such as a ready line or a CI result, make the watcher
exit on every outcome, including failure and error lines. If a local process
produces the condition, start it in its own plain `run_in_background` call. Its
exit wakes you, and Monitor's expiry kills processes started inside the watcher.
For up to 30 minutes, use Monitor with `timeout_ms` as the deadline. For longer
waits, run the watcher through the helper, such as
`run.py --timeout 5400 -- gh pr checks 42 --watch`.

Have the watcher print its first check before it sleeps. Before you end the turn,
confirm in its output file that the check ran without errors. Monitor sends only
stderr to that file. Stop a job or watcher you replace or no longer need with
TaskStop.

## Codex

Add `--codex` to queue completion to the current thread, following
[CODEX.md](CODEX.md).
