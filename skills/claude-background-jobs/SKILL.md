---
name: claude-background-jobs
description: Run Claude Code background jobs and watchers with a deadline. Use before Bash run_in_background or Monitor, such as for slow tests, builds, or waiting on a ready line or CI.
---

# Wake when a job finishes

The Bash tool's `timeout` parameter does not stop a `run_in_background` command.
Give every finite background command a deadline by running it through the helper
as the `run_in_background` command:

```sh
python3 <skill-directory>/scripts/run.py --timeout 1800 -- make test
```

Claude Code wakes you when the helper exits. The helper closes the command's
stdin. At the deadline, it stops the command's process group and exits with code
124. Choose a deadline well past the normal duration. Pass the command as
separate arguments. For shell syntax, use `sh -c '...'`. Run the work in the
command's foreground. A launcher that detaches its worker exits before the work
finishes.

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
