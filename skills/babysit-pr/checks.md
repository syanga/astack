# Classify a failed check

- **A failure in the diff's own code.** Fix it in a commit.
- **A failure in code the diff never touched.** The base is stale. Run `git fetch origin <base>`, then `git merge-base --is-ancestor origin/<base> HEAD`, and rebase when that command exits non-zero.
- **A suspected flake.** Rerun the workflow only when `gh run view <run id> --json attempt` shows attempt 1. A failure on a later attempt is not flake, so read the job log.
- **A failure that a fix in the diff, a rebase, and one rerun do not clear.** It needs the user. Report the check, the attempt, and the log lines, and stop.
