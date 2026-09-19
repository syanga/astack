# Maintain the program store

Use one coordinator as the writer of the shared tables and frontier. Workers
return reports through the harness. The coordinator saves those reports and
their completion pointers. A track coordinator may own a separate track store,
but sends rollups to the parent instead of writing the parent's files.

## Initialize

Create the store directory named by the skill. Record the coordinator's session
identity, repository, plan path, execution authorization, merge authorization,
and done predicate in `overview.md`. Keep numbered standing orders in
`preferences.md`. Create `briefs/`, `reports/`, `inbox/`, and `processed/`.

Create UTF-8 TSV files with these header rows. Fields contain no literal tabs
or newlines. Use report paths for multiline evidence.

```text
units.tsv
id	track	state	agent	branch	pr	head_sha	brief

ledger.tsv
pr	head_sha	role	verdict	evidence

decisions.tsv
question	evidence	decision	units
```

Unit states are `planned`, `running`, `needs-verify`, `merge-ready`, `done`,
`failed`, `abandoned`, and `zombie-reconciled`. Use `done` only when the unit's
acceptance criteria and authorized delivery step are complete. A PR awaiting
merge remains `merge-ready`. Abandonment records its reason and the replacement
or remaining gap in `overview.md`; it does not satisfy the original predicate.

Ledger roles are `worker` and `verifier`. Append verdicts in receipt order.
For a PR and head SHA, use the latest verifier row when one exists, otherwise
the latest worker row. Check that its verdict meets the unit's recorded
acceptance criteria. A new SHA needs a new row. Preserve failed and blocked
receipts when a later verdict supersedes them.

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

Read the standing orders, overview, unit rows, pending completions, and ledger.
Confirm the previous coordinator is inactive before taking ownership. Probe
workers through read-only harness status and durable artifacts. Reconcile
surviving work before assigning its scope again. Regenerate the frontier and
status from current facts, then resume the drain cycle.
