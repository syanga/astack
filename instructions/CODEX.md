## Codex preferences

For long-running commands, keep the process session and wait for completion or new
output. Prefer completion notifications when available. Use the longest permitted
wait. When periodic progress updates are required, cap each wait at 60 seconds.
Keep interactive prompts visible in the process session so requests for input can
interrupt the wait.

Never spend model turns on repeated sleep-and-tail, process-list, or file-status
checks when a process session, notification, or native wait is available. Short
session polls or delegating the same polling loop do not satisfy this rule.
Read logs for a result, failure, input request, or suspected stall.
A routine progress update does not require another log read.

If no completion mechanism exists, use one bounded watcher outside the model that
returns on completion or an actionable change. Use model-driven polling only when
neither a completion mechanism nor a bounded watcher is available.
Explain that limitation and back off between checks.
