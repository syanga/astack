## Claude Code preferences

* Before your first Bash `run_in_background` command, read `run-job/SKILL.md` from the installed skills. Run every background command through that skill's `run_job.py`, as the entire Bash command. Put `cd`, redirects, and pipes inside `sh -c '...'`.
* Before you end the turn with a watcher running, read its first check and confirm it ran without errors.
* Stop a background job with TaskStop when you replace it or no longer need it. Before starting a replacement that needs the same locks or ports, wait for the stopped job's exit notification.
