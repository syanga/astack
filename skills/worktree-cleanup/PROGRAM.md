# Close out program artifacts

Apply this policy when cleanup includes an orchestrate or multi-phase-plan program. Use the plan and [execution store](../orchestrate/STATE.md) to find its artifacts, including paths outside worker worktrees. Planning alone does not create a store; handle an existing plan without initializing one.

For cleanup limited to a worker lane, preserve the shared store and evidence still needed by the program. Return to [lane teardown](SKILL.md) for that worker's disposable resources. The remaining steps require whole-program cleanup scope.

## Establish what can close

Confirm that cleanup covers the whole program. Reconcile its units, workers, pending completions, PRs, gates, and follow-ups against current evidence. Require completion of the program's done predicate or explicit abandonment with remaining requirements accounted for. A merged worker PR does not establish program completion. Preserve state for active, paused, blocked, or merge-ready programs.

Identify every path's owner and consumers from the plan, store, handoffs, and launch records. Keep resources shared outside the cleanup scope, retained deliverables, and artifacts of unknown ownership. Stop owned disposable processes through [worktree-cleanup](SKILL.md) before removing their files. Confirm that no worker or coordinator can still write to a store being archived.

## Preserve one final archive

Use an existing retention location or a durable local directory outside every removal target. Record its absolute path in the closeout report. Archiving locally does not authorize publishing program records.

On repeated cleanup, reuse an existing verified archive when its records still match the program's final state.

Copy the final plan, overview and handoffs, unit and verification records, decision trail, and required evidence into one program archive. Include supporting briefs and completion records needed to understand those records. Follow evidence pointers outside the store too. Keep append-only records unchanged and add an index mapping original paths to archive paths.

Verify retained files against their originals and check that evidence references resolve in the archive or through its relocation index. Update editable local plan and handoff links to the archive. Preserve any old path still required by a live consumer. If a copy, evidence check, or required link update fails, keep the original and report the hold.

## Remove disposable artifacts

After verifying the archive, remove owned snapshots, duplicate reports, temporary verification workspaces, and intermediate output that have no remaining consumer or retention requirement. Use the worktree procedure for Git worktrees. Remove the original store only after its retained records and evidence are verified in the archive and required link updates are complete.

Select targets from the ownership inventory, not a blanket deletion of `.scratch/` or the system temporary directory. Keep teardown and recovery records until resource removal is verified. Report the archive path, removals, relocated or retained links, holds, and failures.
