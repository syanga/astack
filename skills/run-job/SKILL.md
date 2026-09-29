---
name: run-job
description: Run a command in its own process group and stop the whole group at a deadline, on a signal, or when the command exits.
disable-model-invocation: true
---

# Run a bounded job

A background command that hangs never exits, so the notification that waits for
its exit never arrives. `scripts/run_job.py` runs a command in its own process
group. It stops the whole group:

- at the deadline,
- when the helper receives SIGINT, SIGTERM, or SIGHUP,
- when the command exits and leaves other processes running in its group.

Run a finite job with a deadline well past its normal duration:

```sh
python3 <skill-directory>/scripts/run_job.py --timeout 1800 -- make test
```

Run a long-lived process, such as a dev server, with `--no-timeout`. The helper
still exits when the process exits or crashes:

```sh
python3 <skill-directory>/scripts/run_job.py --no-timeout -- npm run dev
```

Pass the command as separate arguments. For pipes, redirections, or several
commands, use `sh -c '...'`. Run the work in the command's foreground. If a
launcher detaches its worker and exits, the helper stops the worker as a leftover
process. Keep the project's test locks, worker limits, and execution wrappers.

The helper needs Python 3.10 or later. It inherits the current directory and
environment, reads stdin from `/dev/null`, and passes stdout and stderr through.
Its own status lines go to stderr and start with `run-job:`.

| Exit code | Meaning |
| --- | --- |
| The command's code | The command exited. A command killed by signal N gives 128+N. |
| 2 | The arguments are invalid. Nothing started. |
| 124 | The deadline passed, and the helper stopped the process group. |
| 125 | The helper could not confirm that the process group stopped. This replaces any other code. |
| 126 | The command is not executable. |
| 127 | The command was not found. |
| 128+N | The helper received signal N and stopped the process group. |

A command can exit with one of these codes itself. The helper prints a
`run-job:` status line with codes 124 through 127 and a `usage: run_job.py` line
with code 2.

## Bound a watcher and confirm its first check

A watcher waits for one outcome, such as a ready line or a CI result. Give it a
deadline:

```sh
python3 <skill-directory>/scripts/run_job.py --timeout 5400 -- gh pr checks 42 --watch
```

- Make the watcher exit on every outcome: success, failure, an error from the
  check itself, and the watched process ending. If the watched process runs
  through its own `run_job.py` call, that call's exit notification reports a
  crash. Stop the watcher when it arrives.
- Make it print its first check before its first sleep.
- Before you end the turn, read that first check in the output and confirm it ran
  without errors.

## Processes the helper cannot stop

A process that calls `setsid` leaves the group, and the helper does not stop it.
The helper cannot handle SIGKILL, so killing the helper that way leaves the group
running.
