---
name: codex-background-jobs
description: Run long noninteractive Codex jobs and queue completion to their thread. Use for tests, builds, or other finite commands that would otherwise need repeated waits.
---

# Resume when a job finishes

Use this workflow in a loaded Codex thread with `CODEX_THREAD_ID` and a CLI that
supports `codex queue`. Keep the hosting session open until the job finishes.
Other harnesses and interactive commands use their native process sessions.

1. Choose an authorized, noninteractive command and a realistic maximum duration.
   Preserve the project's test locks, worker limits, and execution wrappers.
   Run the work in the foreground of that command. A launcher that detaches its
   worker can exit before the work finishes.
2. Start the helper through the shell tool, with a short initial yield:

   ```sh
   python3 <skill-directory>/scripts/run.py --label 'unit tests' --timeout 1800 -- make test
   ```

   Pass the command as separate arguments. For shell syntax, explicitly use
   `sh -c '...'`. The helper inherits the current directory and environment.
   It closes stdin and saves combined output in a private temporary directory.
3. Confirm the initial receipt says `running`. Retain the process session and
   the receipt's result and log paths. If startup fails, handle the error before
   ending the turn. If the helper has already finished, read its result now.
4. Continue independent work. When only the job remains, tell the user what is
   running and end the turn. The queue requests automatic continuation in the
   loaded thread. Acceptance alone does not establish that execution resumed.
   Do not schedule status checks or ask the user to reply to resume.
5. On notification, read the result and relevant output once. Continue the
   original task. A timeout or nonzero exit is a failure to investigate.
   Answer status questions briefly, then continue the remaining authorized work.
   Apply later cancellation or scope changes before acting on delayed messages.

The helper attempts one notification after success, failure, or timeout. Interrupting
its process session while the job runs stops the job and skips notification.
Use that session to cancel before ending the turn if the job is no longer wanted.
An interrupt during delivery records `notification: unknown`; a message already
accepted by Codex cannot be recalled.

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

The helper cannot report its own forced termination or a stopped host. A stale
result is not evidence that a job is still alive. Recovery from helper death or
host shutdown requires supervision outside the helper. Keep those cases separate
from the handled job failures and delivery errors.

Codex can check its queue internally on a timer. This workflow removes model
polling and does not provide a T3 Monitoring badge.
