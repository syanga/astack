## Codex preferences

Run long noninteractive jobs, watchers, and long-lived processes such as dev
servers through the `codex-background-jobs` skill. It queues completion so you
can end the turn in a supported, loaded thread. Preserve the process session and
result paths. Stop a job or watcher you replace or no longer need as the skill
describes. Follow the skill's recovery steps when completion delivery or
resumption is unverified.

When the completion queue is unavailable, read `run-job/SKILL.md` from the
installed skills and still run the command through its helper. Keep interactive
commands in attended native sessions. In both cases, keep the process session
and use the longest permitted native wait. Cap waits at 60 seconds when periodic
progress updates are required. Keep input prompts visible.

Never spend model turns on repeated sleep-and-tail, process-list, or file-status
checks when completion notifications or native waits are available. Short session
polls or delegating the same loop do not satisfy this rule. Read logs for a result,
failure, input request, or suspected stall, not for routine progress updates.

If neither mechanism exists, use one bounded watcher outside the model that
returns on completion or an actionable change. Model-driven polling is a last
resort: explain the limitation and back off between checks.
