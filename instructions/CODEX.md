## Codex preferences

Run long noninteractive jobs, including watchers and long-lived processes such as
dev servers, through the `codex-background-jobs` skill. Stop a job you replace or
no longer need as the skill describes. Follow the skill's recovery steps when
completion delivery or resumption is unverified.

When the completion queue is unavailable, read `run-job/SKILL.md` from the
installed skills and run the command through that skill's `run_job.py`, started
with `tty: true`. Run interactive commands in attended native sessions with their
input prompts visible. In both cases, keep the process session and use the
longest permitted native wait. Cap waits at 60 seconds when periodic progress
updates are required.

Never spend model turns on repeated sleep-and-tail, process-list, or file-status
checks when completion notifications or native waits are available. Short session
polls or delegating the same loop do not satisfy this rule. Read logs for a result,
failure, input request, or suspected stall, not for routine progress updates.

If neither mechanism exists, use one bounded watcher outside the model that
returns on completion or an actionable change. Model-driven polling is a last
resort: explain the limitation and back off between checks.
