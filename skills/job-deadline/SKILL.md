---
name: job-deadline
description: Run a command with a deadline so it ends even if it hangs.
disable-model-invocation: true
---

# Run a command with a deadline

A background command that hangs never exits, so nothing that waits for its exit
fires. Run a long job or a watcher through the helper, with a deadline well past
its normal duration:

```sh
python3 <skill-directory>/scripts/run.py --timeout 1800 -- make test
```

The helper inherits the current directory and environment, closes stdin, and
runs the command in its own process group. It passes through the command's output
and exit code. At the deadline, it stops the group and exits with code 124.
SIGINT, SIGTERM, or SIGHUP sent to the helper also stops the group. SIGKILL does
not, because the helper cannot catch it.

Choose a command that finishes on its own. Preserve the project's test locks,
worker limits, and execution wrappers. Pass the command as separate arguments.
For shell syntax, use `sh -c '...'`. Run the work in the command's foreground. A
launcher that detaches its worker exits before the work finishes. Start a
long-lived process, such as a dev server, without the helper.

A watcher waits for one outcome, such as a ready line or a CI result. For
example, `run.py --timeout 5400 -- gh pr checks 42 --watch` waits on CI. Make
the watcher exit on every outcome, including failure and error lines. Have it
print its first check before it sleeps. Before you end the turn, read that check
in its output and confirm it ran without errors.
