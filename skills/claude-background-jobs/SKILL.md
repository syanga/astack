---
name: claude-background-jobs
description: Run long Claude Code jobs and watchers that always wake you. Use for slow tests, builds, or waiting on a ready line or CI result.
---

# Wake when a job finishes

Run a long finite job, such as a slow test suite or build, through the helper as
the Bash `run_in_background` command:

```sh
python3 <skill-directory>/scripts/run.py --timeout 1800 -- make test
```

Claude Code wakes you when the helper exits, so the job needs no watcher. The
helper closes stdin and stops the job's process group at the deadline with exit
code 124. Choose a deadline well past the job's normal duration. Pass the command
as separate arguments. For shell syntax, use `sh -c '...'`.

To wait for a condition, such as a ready line or a CI result, start any local
process with plain `run_in_background` and have a watcher only read its output.
Make the watcher exit on every outcome, not only success, and give it a deadline.
The Bash tool's `timeout` parameter does not stop a `run_in_background` command.
Use Monitor with `timeout_ms` for deadlines up to 30 minutes, or a Bash
`run_in_background` loop that exits when `$SECONDS` passes the deadline.

Have the watcher print its first check to stderr before it sleeps, and confirm in
its output file that the check ran without errors before you end the turn. Stop a
job or watcher you replace or no longer need with TaskStop.
