---
name: codex-background-jobs
description: Run long noninteractive Codex jobs with automatic completion wake-up. Use for tests, builds, or other finite commands that would otherwise need repeated waits.
---

# Resume when a job finishes

Use this workflow in a loaded Codex thread with `CODEX_THREAD_ID` and a CLI that
supports `codex queue`. Keep the hosting session open until the job finishes.
Other harnesses and interactive commands use their native process sessions.

1. Choose an authorized, noninteractive command and a realistic maximum duration.
   Preserve the project's test locks, worker limits, and execution wrappers.
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
   running and end the turn. The queued completion starts a new turn automatically.
   Do not schedule status checks or ask the user to reply to resume.
5. On notification, read the result and relevant output once. Continue the
   original task. A timeout or nonzero exit is a failure to investigate.

The helper queues one message after success, failure, or timeout. Interrupting
its process session while the job runs stops the job and skips notification.
Use that session to cancel before ending the turn if the job is no longer wanted.
An interrupt during delivery records `notification: unknown`; a message already
accepted by Codex cannot be recalled.

If the CLI lacks `queue`, the helper refuses to start the job. Use a native wait.
If queue delivery fails later, the result records `notification: failed` and the
helper exits 75. On returning to the session, read that result and continue
manually. Do not rerun the job. A closed host or interrupted thread may not wake;
queue acceptance alone does not prove that a new turn started.

Codex can check its queue internally on a timer. This workflow removes model
polling and does not provide a T3 Monitoring badge.
