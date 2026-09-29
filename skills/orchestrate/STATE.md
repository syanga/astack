# Maintain shared execution state

Use this convention for every multi-phase plan, whether implement runs its units
sequentially or orchestrate coordinates several owners. Keep one store when
switching execution skills. The current execution owner writes its shared files.
With several agents, that owner is the coordinator; workers return reports
instead of editing the shared tables. A track coordinator may own a separate
track store, but sends rollups instead of writing the parent's files.

## Locate the store

Use the plan's directory, `<project repo>/.scratch/<program>/` by default. Resolve
it from the project checkout hosting the plan. Workers use that same path.
If an existing effort already has a store elsewhere, reuse it. Record the absolute
store and plan paths in the plan and every session handoff. Keep the store outside disposable worker
worktrees and preserve it through worker cleanup. Whole-program teardown follows
[program closeout](../worktree-cleanup/PROGRAM.md). For another machine or a
remote session, explicitly transfer the current store or publish it to an
authorized shared location. Local files alone do not synchronize across machines.

## Initialize

When execution starts, read [CLI.md](CLI.md) and run `orch init` against this
store. Here `orch` means `bun "$ORCH_CLI"` with `ORCH_STORE` set as documented
there. Init creates missing unit, ledger, inbox, gate, standing-order, and
frontier files while preserving existing data. Create `overview.md` and record
the owner's session identity, repository, plan path, execution authorization,
merge authorization, and done predicate. Seed one unit per planned PR with
`orch unit add`, using the plan's stable IDs. Keep dependencies and acceptance
criteria in the plan; link them from state.

A sequential run uses track `main`, the current session as `agent`, and an empty
`brief` field unless a separate brief is needed. Leave unknown branch, PR, and
SHA fields empty until assigned. Create `reports/` for verification receipts.
Before a PR exists, keep receipts keyed by unit and head SHA there. Once the PR
exists, append a ledger row only if its current head matches the receipt.

Use `orch standing add` for standing orders and `orch gate park` for human
decisions that block an action. Open the decision trail through
[show-me-your-work](../show-me-your-work/SKILL.md), which owns its format, helper,
history, and audit procedure. Use `<store>/decisions.tsv`; the execution owner
writes it. Follow that skill's transition procedure for an older log. Create
`briefs/` when workers need separate briefs. The CLI maintains the completion
queue, archived drain batches, and merge frontier; use its commands instead of rewriting tables.

The CLI owns the unit and ledger formats. Use `--json` for complete records.
[CLI.md](CLI.md#compatibility-and-boundaries) describes older table compatibility.

New units start `pending` (`planned` in older manual stores). Execution states
are `running`, `blocked`, `needs-verify`, `merge-ready`, `done`,
`failed`, `abandoned`, and `zombie-reconciled`. Use `done` only when the unit's
acceptance criteria and authorized delivery step are complete. Required reviews
are part of acceptance. If a hold prevents a required review, keep the unit
`needs-verify` and record the pending review. Delivery permission alone does not
waive that review. A PR awaiting merge remains `merge-ready`.
Abandonment records its reason and the replacement
or remaining gap in `overview.md`; it does not satisfy the original predicate.

## Accept verification

Record each receipt with `orch ledger record` in receipt order. Omit `--verifier`
for worker claims; name the verifier for independent receipts. Use
`orch --json ledger check <pr> <sha>` to read the effective verdict. The CLI
retains every row and selects the latest verifier receipt for that PR and SHA,
or the latest worker receipt when no verifier exists. A new SHA needs a new receipt.

Verdicts are `live-ui-verified`, `unit-test-verified`, `type-check-only`,
`verifier-blocked`, and `verifier-failed`. Inspect the evidence and compare its SHA
with the current PR head before accepting the result. Treat a receipt as
evidence only when it links saved files that hold the raw output and exit
status of every verification command it reports. CI success contributes
evidence but does not establish a verdict. Behavioral work needs more than
`type-check-only`. A blocked check resumes when its environment is available;
a failed check needs a fix before new verification. Neither satisfies acceptance.

The CLI records claims. It does not run verification, validate evidence files,
or enforce unit transitions. Check the recorded acceptance criteria yourself.

## Checkpoint progress

Use the CLI to update state after a unit changes state, verification completes,
a PR head or base changes, or a blocking decision arrives. Save partial progress
before ending a session. Sequential owners record their own results directly;
coordinators drain worker completions first using the procedure below.

1. Save the receipt under `reports/`, including the unit, head SHA, commands,
   results, and artifact paths. Link the file that holds each verification
   command's raw output and exit status, as
   [Accept verification](#accept-verification) requires. Save output that exists
   only in the session to a file first.
   Update the unit row and append any ledger verdict.
   Log decisions and checkpoints through show-me-your-work.
2. Check plan boxes only when their evidence exists and meets
   [Accept verification](#accept-verification). Link that evidence from the box. The unit table owns execution state; a stale checkbox cannot override
   current Git state or a missing verdict at the current SHA.
3. Append a dated handoff to `overview.md` with each active unit, its worktree,
   branch and PR, head SHA, any uncommitted work, blockers, and the exact next
   action or command. Include remaining approvals and active worker identities.
   Use `## Handoff <date/time>` headings and explicit `none` entries when these
   do not apply. The latest handoff is the resume entry point. Keep earlier entries.
4. Run `orch status` to generate `status.md` from the saved tables and latest
   handoff. It includes state counts, recorded heads, effective verdicts, and gates;
   the handoff supplies blockers and the next action.
   Keep `status.md` derived; do not maintain a separate `progress.md` or chat-only
   task list as another source of truth.

The last human-facing message links the plan and store so a new session can find
them. A session that stops mid-unit leaves that unit unfinished and records its
partial result. Session termination alone never marks work done.

## Drain completions

1. Save each completion with `orch inbox push`, including agent, unit, reported
   status, and a durable report path. Reports include the head SHA, commands,
   results, artifacts, and links to each verification command's saved raw output
   and exit status, as [Accept verification](#accept-verification) requires.
   Retain the source event ID in the report when available.
2. Read `orch --json inbox drain`. It rotates the pending queue and retains the
   drained batch under `processed/` before returning its pointers. Reconcile those
   reports against current PRs, heads, and existing rows before accepting claims.
   Replayed events must not create duplicate units or replace newer evidence.
3. Update units with `orch unit set`, append verdicts with `orch ledger record`,
   and record decisions. Recompute the frontier after a merge or stack mutation.
   Each CLI mutation holds the store lock and atomically replaces its file; a
   checkpoint spanning commands still needs recovery after interruption.
4. Recompute ready work from dependencies and current state. Record acceptance
   and the next action in the overview handoff, then run `orch status`.

After interruption, run `orch init`, inspect `orch --json inbox history`, and
reconcile any unrecorded receipts before dispatching more work. History includes
interrupted and archived batches. A drained pointer is not proof of acceptance.
Arrivals after queue rotation remain in `inbox/` for the next drain.

## Recompute the merge frontier

Fetch current Git refs and reconcile them with the forge and the stack owner's
confirmed order. Use `orch frontier set --repo <repo>` for Graphite, or
`orch frontier set --source github --repo <repo> --prs <ordered-pr-list>` for
GitHub. [CLI.md](CLI.md) defines each adapter's checks and limits. Each store holds
one ordered frontier with a generation, PRs, branches, SHAs, states, and the
lowest unmerged PR. Separate track stores send rollups to their
parent coordinator.

Only admit a merge when all plan dependencies are satisfied, the current SHA
meets the verification criteria above, and its human gates and merge authorization
are satisfied. A frontier refresh does not enforce these conditions. If refresh
fails or refs disagree during a restack, treat the saved frontier as stale until the stack owner
confirms the graph and refresh succeeds. Re-read the candidate PR's head before
merging and use the exact-head protection required by ship-pr.

## Resume

Read the plan, latest overview handoff, unit rows, ledger, and any standing
orders, the decision trail, and pending completions. Confirm that the previous
execution owner is inactive or has transferred ownership before writing shared state.

Apply [the CLI's pause and resume orders](CLI.md#completions-and-recovery).
Honor the hold's scope during verification, checkpoint writes, and execution.

Check the recorded worktrees, uncommitted changes, branch heads, PR states, and
verification receipts. Preserve partial work. Probe surviving workers through
read-only harness status and durable artifacts before reassigning their scope.
Reconcile stale rows and plan checkboxes with these facts; a changed SHA requires
fresh verification. Evaluate receipts under [Accept verification](#accept-verification).
Reuse them only when they identify the current artifact and remain applicable to
its environment and acceptance criteria. When those inputs changed or supporting
evidence is missing, run the necessary checks if the hold permits them. Otherwise
report them as pending. Reconcile these results before selecting work. An
interrupted checkpoint is evidence to reconcile, not permission to repeat an
already completed action.

Refresh the frontier only when the store tracks PRs. The empty frontier created
by `orch init` needs no forge lookup. Regenerate the status summary within any
restrictions on checkpoint writes. Select the next unfinished unit whose
dependencies and approvals are satisfied and whose work the current hold permits.
If the hold prevents all execution, report the saved
resume point and its release condition. Otherwise continue from the recorded next
action, either directly with implement or through orchestrate's drain cycle.
Reuse the same store when execution changes between these modes.

## Close

Audit the decision trail through show-me-your-work before handing back. Include
its review findings with the plan and store links. When cleanup is authorized,
follow [program closeout](../worktree-cleanup/PROGRAM.md) to archive final records
and remove disposable artifacts. Otherwise preserve the store and its receipts.
