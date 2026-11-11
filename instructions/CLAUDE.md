## Claude Code preferences

* Before your first Bash `run_in_background` command, read `run-job/SKILL.md` from the installed skills. Run every background command through that skill's `run_job.py`, as the entire Bash command. Put `cd`, redirects, and pipes inside `sh -c '...'`.
* Before you end the turn with a watcher running, read its first check and confirm it ran without errors.
* Stop a background job with TaskStop when you replace it or no longer need it. Before starting a replacement that needs the same locks or ports, wait for the stopped job's exit notification.
* After any rechecks, run a PR merge as the entire Bash command, such as `gh pr merge <n> [--repo <owner>/<repo>] --squash --match-head-commit <sha> [flags]`. Keep it on one line with literal values and no quotes or redirects. The merge gate hook denies every other Bash command that contains `pr merge` or a merge API name such as `mergePullRequest`. Put prose that mentions one, such as a commit message or PR body, in a file with the Write tool, and pass the file with `-F` or `--body-file`. Search for one with a pattern that breaks it up, such as `pr.merge`.
