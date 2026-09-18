---
name: worktree-cleanup
description: Worktree cleanup. Use for cleanup, resource audits, close this lane, or close out.
---

# Worktree cleanup

First, check the lane's conversation, notes, and linked PRs for unfinished work or unrecorded follow-ups. If anything needs handling or recording before closeout, report it and stop before auditing resources or making changes. Stop on missing completion evidence too.

"Close this lane" means the current T3 thread unless specified otherwise. Resource questions are read-only.

Inventory worktrees and associated resources, including leftovers outside deleted worktrees. Establish ownership from thread and launch records. Retain unknown, shared, active, pinned, or data-bearing targets whose disposal is undecided. Current-thread tools cannot establish other threads' inactivity.

Stop disposable resources before removing their worktrees:

- Processes: match PID, start time, and command. Account for supervisors and children.
- Previews: use the [cleanup record](../t3-preview/cleanup.md) or original launch evidence. HTML-only previews have no processes. Stop preview quick tunnels before their servers. Preserve T3's access tunnel and retained deliverables.
- Docker: verify the daemon and container ownership. Compose names can span worktrees. Inspect volume consumers, including stopped containers. Avoid global pruning.
- Test databases: derive the exact target from test setup. Stop clients before scoped teardown. Preserve shared servers and data.

Remove only merged or explicitly abandoned worktrees. Check ignored and untracked files and post-squash commits. Retain detached commits through a branch. Keep main, current, locked, active, and open-PR worktrees. Preserve branch refs unless deletion was requested.

Recheck before `git worktree remove`. Force removal requires explicit disposal authorization for the affected files. Verify teardown before deleting recovery records. Report removals, retired links, holds, and failures.

Expand to simulators or caches only when requested. Editor backups may contain otherwise unrecorded work.
