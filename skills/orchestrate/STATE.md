# Maintain shared execution state

Use this convention for every multi-phase plan, whether implement runs its units
sequentially or orchestrate coordinates several owners. Keep one store when
switching execution skills. The current execution owner writes its shared files.
With several agents, that owner is the coordinator; workers return reports
instead of editing the shared tables. A track coordinator may own a separate
track store, but sends rollups instead of writing the parent's files.

## Locate the store

Use the plan's directory, `<project repo>/.scratch/<program>/` by default. Resolve
it from the project checkout hosting the plan, not from each worker's directory. If an existing effort
already has a store elsewhere, reuse it. Record the absolute store and plan paths
in the plan and every session handoff. Keep the store outside disposable worker
worktrees and preserve it through worktree cleanup. For another machine or a
remote session, explicitly transfer the current store or publish it to an
authorized shared location. Local files alone do not synchronize across machines.

## Initialize

When execution starts, create any missing `overview.md`, `units.tsv`, `ledger.tsv`,
and `status.md` files alongside the plan. Preserve existing rows and history.
Record the owner's session identity, repository,
plan path, execution authorization, merge authorization, and done predicate in
`overview.md`. Seed one unit row per planned PR, using the plan's stable IDs.
Keep dependencies and acceptance criteria in the plan; link them from state.

A sequential run uses track `main`, the current session as `agent`, and an empty
`brief` field unless a separate brief is needed. Leave unknown branch, PR, and
SHA fields empty until assigned. Create `reports/` for verification receipts.
Before a PR exists, keep receipts keyed by unit and head SHA there. Once the PR
exists, append a ledger row only if its current head matches the receipt.

Add `preferences.md` for standing orders, `decisions.tsv` for new decisions,
and `gates.md` when a human decision blocks an action. With delegated workers,
also create `briefs/`, `inbox/`, and `processed/`. With coordinated merges or
stack mutations, maintain `frontier.json`. Adding these files changes no core
schema or unit IDs.

Use these header rows when creating UTF-8 TSV files. Fields contain no literal tabs
or newlines. Use report paths for multiline evidence.

```text
units.tsv
id	track	state	agent	branch	pr	head_sha	brief

ledger.tsv
pr	head_sha	role	verdict	evidence

decisions.tsv
question	evidence	decision	units
```

Unit states are `planned`, `running`, `blocked`, `needs-verify`, `merge-ready`, `done`,
`failed`, `abandoned`, and `zombie-reconciled`. Use `done` only when the unit's
acceptance criteria and authorized delivery step are complete. A PR awaiting
merge remains `merge-ready`. Abandonment records its reason and the replacement
or remaining gap in `overview.md`; it does not satisfy the original predicate.

Ledger roles are `worker` and `verifier`. Verdicts are `live-ui-verified`,
`unit-test-verified`, `type-check-only`, `verifier-blocked`, and `verifier-failed`.
Blocked and failed verdicts never satisfy acceptance. Behavioral work requires
more than `type-check-only`. Append verdicts in receipt order.
For a PR and head SHA, use the latest verifier row when one exists, otherwise
the latest worker row. Check that its verdict meets the unit's recorded
acceptance criteria. A new SHA needs a new row. Preserve failed and blocked
receipts when a later verdict supersedes them.

## Checkpoint progress

Update state after a unit changes state, verification completes, a PR head or
base changes, or a blocking decision arrives. Save partial progress before
ending a session. Sequential owners record their own results directly;
coordinators drain worker completions first using the procedure below.

1. Save the receipt under `reports/`, including the unit, head SHA, commands,
   results, and artifact paths. Update the unit row and append any ledger verdict.
2. Check plan boxes only when their evidence exists. Link that evidence from the
   box. The unit table owns execution state; a stale checkbox cannot override
   current Git state or a missing verdict at the current SHA.
3. Append a dated handoff to `overview.md` with each active unit, its worktree,
   branch and PR, head SHA, any uncommitted work, blockers, and the exact next
   action or command. Include remaining approvals and active worker identities.
   Use explicit `none` entries when these do not apply. The latest handoff is the
   resume entry point; earlier entries retain the history.
4. Generate `status.md` from the current tables and latest handoff. Include counts
   by state, current heads and effective verdicts, blockers, and the next action.
   Keep `status.md` derived; do not maintain a separate `progress.md` or chat-only
   task list as another source of truth.

The last human-facing message links the plan and store so a new session can find
them. A session that stops mid-unit leaves that unit unfinished and records its
partial result. Session termination alone never marks work done.

## Drain completions

1. Save each completion to a uniquely named file in `inbox/`, with agent, unit,
   reported status, and a durable report path. Reports include the head SHA,
   commands, results, and artifacts. Retain the source event id when available.
2. Snapshot the pending filenames. Reconcile those events against current PRs,
   heads, and existing rows before accepting their claims. Replayed events must
   not create duplicate units or replace newer evidence.
3. Update the unit rows, append verdicts and decisions, and recompute the frontier
   after a merge or stack mutation. Write replacement tables to temporary files
   in the store, then rename them into place. After interruption, reconcile
   pending events against the tables before dispatching more work.
4. Generate `status.md` by reading the saved tables with a TSV reader, counting
   units by state and track, and joining each PR's current SHA to its effective
   ledger verdict. Include unmatched heads as unverified. Report changes since
   the previous drain and unresolved entries in `gates.md`.
5. Move reconciled pointers to `processed/`. Leave arrivals outside the snapshot
   for the next drain. Recompute ready work from dependencies and current state.

## Recompute the merge frontier

Fetch current Git refs and query the forge for each tracked PR's state, base,
head branch, and head SHA. Compare these with the plan's dependencies and the
stack owner's confirmed branch order. For GitHub, use `gh pr view <number>
--json number,state,baseRefName,headRefName,headRefOid,url` for current PR data.

Write `frontier.json` with a monotonically increasing `generation`, the
observation time, and one entry per stack. Each stack records its owner, its
ordered PRs with branches and SHAs, and its lowest unmerged PR. Only admit a
merge when all dependencies are satisfied, its current SHA has the required
verdict, and its human gates and merge authorization are satisfied.

If refs disagree during a restack, record the frontier as blocked until the
stack owner confirms the complete graph. Re-read the candidate PR's head before
merging and use the exact-head protection required by ship-pr.

## Resume

Read the plan, latest overview handoff, unit rows, ledger, and any standing
orders or pending completions. Confirm the previous execution owner is inactive
or has explicitly transferred ownership before writing shared state.

Check the recorded worktrees, uncommitted changes, branch heads, PR states, and
verification receipts. Preserve partial work. Probe surviving workers through
read-only harness status and durable artifacts before reassigning their scope.
Reconcile stale rows and plan checkboxes with these facts; a changed SHA requires
fresh verification. An interrupted checkpoint is evidence to reconcile, not
permission to repeat an already completed action.

Regenerate the frontier when present and the status summary. Select the next
unfinished unit whose dependencies and approvals are satisfied. Continue from
its recorded next action, either directly with implement or through orchestrate's
drain cycle. Reuse the same store when execution changes between these modes.
