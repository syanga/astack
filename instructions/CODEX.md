## Codex preferences

For long noninteractive tests, builds, and finite jobs, use the
`codex-background-jobs` skill to queue completion and end the turn in a supported,
loaded thread. Preserve the process session and result paths. Follow the skill's
recovery steps when completion delivery or resumption is unverified.

For interactive commands or unavailable completion queues, keep the process
session and use the longest permitted native wait. Cap waits at 60 seconds when
periodic progress updates are required. Keep input prompts visible.

Never spend model turns on repeated sleep-and-tail, process-list, or file-status
checks when completion notifications or native waits are available. Short session
polls or delegating the same loop do not satisfy this rule. Read logs for a result,
failure, input request, or suspected stall, not for routine progress updates.

If neither mechanism exists, use one bounded watcher outside the model that
returns on completion or an actionable change. Model-driven polling is a last
resort: explain the limitation and back off between checks.
