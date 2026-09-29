# Resume a Codex thread when a job finishes

Use this workflow in a loaded Codex thread with `CODEX_THREAD_ID` and a CLI that
supports `codex queue`. Keep the hosting session open until the job finishes.
Interactive commands use their native process sessions.

1. Start the helper through the shell tool, with a short initial yield:

   ```sh
   python3 <skill-directory>/scripts/run.py --codex --label 'unit tests' \
     --timeout 1800 -- make test
   ```

   With `--codex`, the helper saves combined output in a private temporary
   directory and queues one completion message to the thread.
2. Confirm the initial receipt says `running`. Retain the process session and
   the receipt's result and log paths. If startup fails, handle the error before
   ending the turn. If the helper has already finished, read its result now.
3. Continue independent work. When only the job remains, tell the user what is
   running and end the turn. The queue requests automatic continuation in the
   loaded thread. Acceptance alone does not establish that execution resumed.
   Do not schedule status checks or ask the user to reply to resume.
4. On notification, read the result and relevant output once. Continue the
   original task. A timeout or nonzero exit is a failure to investigate.
   Answer status questions briefly, then continue the remaining authorized work.
   Apply later cancellation or scope changes before acting on delayed messages.

The helper attempts one notification after success, failure, or timeout. When it
receives SIGINT, SIGTERM, or SIGHUP while the job runs, it stops the job and
skips notification. Verify the cancellation receipt. A host can terminate the
process session without running this cleanup, leaving the job alive and its
result stale.
A handled interrupt during delivery records `notification: unknown`; the helper
cannot recall a message already accepted by Codex.

## Recover a failed completion

If the CLI lacks `queue`, the helper refuses to start the job. Use a native wait.
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
