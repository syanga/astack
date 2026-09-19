# Pause safely

Pause only when requested. A request to prepare context for another agent does not by itself stop the current work.

Honor restrictions on checkpoint writes throughout this procedure, including standing orders and saved findings. When those writes are forbidden, leave shared files unchanged and report the resume context and unsaved updates in chat.

1. Apply the requested pause scope. If only new dispatch is paused, let in-flight workers continue and retain their ownership. For a full stop, tell this task's workers to stop through the available agent controls. Finish an atomic step only if the request permits it. An immediate stop-writes order takes effect at once. Check worker status before reporting anyone inactive. Name any unconfirmed stop.
2. For an execution store, have its owner record the hold through [the CLI's pause and resume orders](../orchestrate/CLI.md#completions-and-recovery). Record its scope and release condition. Existing delivery authorization still applies within the hold. Pausing alone does not authorize a PR or push.
3. Preserve uncommitted edits on disk. Commit task-owned edits only when the delivery scope and hold permit it. Record resulting commits and the paths of remaining uncommitted changes. If the tree is broken, record the failure and first repair step. For Wayfinder, preserve findings and release unfinished ticket claims through the tracker's procedure only after their owners stop.
4. Complete [handoff](SKILL.md), including its task-record and reporting procedures. Record the pause scope, active owners, and whether ownership is available for transfer. The execution owner checkpoints its store.
