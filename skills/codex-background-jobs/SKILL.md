---
name: codex-background-jobs
description: Run Codex background commands and queue completion to their thread. Use in Codex for tests, builds, watchers, dev servers, or other commands that would otherwise need repeated waits.
---

# Resume when a job finishes

Use this workflow in a loaded Codex thread with `CODEX_THREAD_ID` and a CLI that
supports `codex queue`. Keep the hosting session open until the job finishes.
Other harnesses and interactive commands use their native process sessions.

Run finite jobs, watchers, and long-lived processes through this helper. It runs
the command through the [`run-job`](../run-job/SKILL.md) helper.

1. Before your first job and before starting a watcher, read
   [`run-job/SKILL.md`](../run-job/SKILL.md). Its rules for the command and its watcher contract
   apply to this helper. Choose an authorized, noninteractive command and a
   deadline well past its normal duration. For a long-lived process, such as a
   dev server, use `--no-timeout` instead. Its exit or crash still queues a
   notification.
2. Start the helper through the shell tool with `tty: true` and a short initial
   yield. The TTY lets you stop the job with Ctrl-C.

   ```sh
   python3 <skill-directory>/scripts/run.py --label 'unit tests' --timeout 1800 -- make test
   ```

   The helper saves combined output in a private temporary directory.
3. Confirm the initial receipt says `running`. Retain the process session and
   the receipt's result and log paths. If startup fails, handle the error before
   ending the turn. If the helper has already finished, read its result now.
   Before you end the turn with a watcher running, read its first check in the
   log and confirm it ran without errors.
4. Continue independent work. When only the job remains, tell the user what is
   running and end the turn. The queue requests automatic continuation in the
   loaded thread. Acceptance alone does not establish that execution resumed.
   Do not schedule status checks or ask the user to reply to resume.
5. On notification, read the result and relevant output once. Continue the
   original task. A timeout, a nonzero exit, or `cleanup: failed` is a failure
   to investigate. Answer status questions briefly, then continue the remaining
   authorized work. Apply later cancellation or scope changes before acting on
   delayed messages.

The helper attempts one notification after success, failure, or timeout. When it
receives SIGINT, SIGTERM, or SIGHUP while the job runs, it stops the job and
skips notification. A host can kill the helper without running this cleanup,
leaving the job alive and its result stale. A handled interrupt during delivery
records `notification: unknown`; the helper cannot recall a message already
accepted by Codex.

## Stop a replaced job

To stop a job or watcher you replace or no longer need, write Ctrl-C (`\u0003`)
to its retained process session. The helper stops the job's process group, which
can take up to 10 seconds. Wait for the session to exit, then read the result. It
says `cancelled` if the job was running. If the job had already finished, it keeps
the job's final status with `notification: skipped`, or `notification: unknown`
if the interrupt arrived during delivery. Before starting a replacement that needs the
same locks or ports, confirm the result says `cleanup: ok`.

If the session is gone or does not accept input, use the result's process IDs.
Codex's sandbox blocks `ps` and signals to processes started by another command,
so run these commands with escalated permissions outside the sandbox:

1. Run `ps -ww -o command= -p <runner_pid>`. If it shows this helper with the
   job's label, run `kill -TERM <runner_pid>`, wait for it to exit, and read the
   result as above.
2. If the helper is gone but the result still says `running`, the host killed the
   helper and the job's process group may still be alive. Run
   `pgrep -l -g <command_pid>`. If it lists only this job's processes, run
   `kill -TERM -- -<command_pid>`.

## Recover a failed completion

If the CLI lacks `queue`, the helper refuses to start the job and names the
`run-job` helper to use instead. Run the command through it and attend it with a
native wait.
If delivery fails or its outcome is unknown, inspect the retained process session
and result on the next turn. Reuse completed work instead of rerunning the job.
An unknown delivery may already have been accepted. Reconcile delayed or duplicate
messages with the current task before continuing.

If writing the result fails after startup, use the final process receipt and
notification's job status and exit code. The saved result can be stale, including
still saying `running`. Read the log for the job's output and preserve the receipt
before the process session is lost. If no final receipt exists, report the
outcome as unverified and inspect the retained session and job evidence.

Distinguish job completion, queue acceptance, and resumed execution in reports.
To verify wake-up, compare the job and queue timestamps with thread events and
the first resumed action. A notification visible after a user message does not
prove that it started a turn. Report missing evidence instead of inferring which
message caused execution.

The helper cannot report its own forced termination or a stopped host. Losing the
launching tool connection can kill the helper even while the host and job continue.
A stale result is not evidence that either process is still alive. Automatic recovery
requires supervision outside that connection with a durable cancellation mechanism.

An unloaded thread can accept a notification without starting a turn. Resuming the
whole thread can execute other pending messages too. Leave automatic thread loading
to a host that checks authorization and cancellation for every pending task. Keep
these host failures separate from the handled job failures and delivery errors.

Codex can check its queue internally on a timer. This workflow removes model
polling and does not provide a T3 Monitoring badge.
