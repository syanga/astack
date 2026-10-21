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

A status question does not pause authorized work.

Codex does not start a turn when a native subagent finishes. astack's Stop hook
wakes an idle top-level thread when one of its subagents finishes, so a top-level
thread may end its turn while its subagents run. The hook does not wake a
subagent thread. As a subagent, wait with `wait_agent` for the subagents you
started before giving your final answer.
