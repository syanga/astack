# Pause safely

**You own a clean stop. Leave a checkpoint a fresh agent can resume from.** Pause only when requested. A request to prepare context for another agent does not by itself stop the current work.

1. Stop at a safe boundary. Finish the current atomic step if the request permits it. Honor an immediate stop-writes request immediately. Start nothing new. Ask this task's workers to stop and return their partial results through the available agent controls. Check their status before reporting them inactive; record any worker whose stop remains unconfirmed.
2. Take no irreversible action just to pause. Existing delivery authorization still applies; a pause request alone does not authorize a PR or push.
3. Make the work durable. Preserve uncommitted edits on disk and record their paths. Commit task-owned edits when committing is within the agreed delivery scope. If the tree is broken, record the failure and the first repair step rather than discarding partial work to make it clean.
4. Write the resume note using [handoff's task-record convention](SKILL.md#use-the-existing-task-record). If a show-me-your-work trail exists, point at it instead of duplicating it. For Wayfinder, preserve findings and release unfinished ticket claims through the tracker's procedure only after their owners stop. With an execution store, checkpoint through its owner and record whether ownership is available for transfer or still pending.

**Reply:** where you are in the loop, what's on disk versus still in your head (paths, no diff dumps), the commits you made and whether the tree is clean, and the first action on resume. This is a pause, not a final report.
