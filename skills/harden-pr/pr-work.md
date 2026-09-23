# Follow the PR

Use `python3 "<harden-pr directory>/scripts/pr.py" status --pr <n>` for a GitHub snapshot and `history --pr <n>` for earlier reviews and thread replies. Add `--repo owner/name` when needed. A status-only request ends with a report. Read the current head, checks, approvals, unresolved threads, and review evidence before changing anything. The helper reports state, not a verdict about correctness or merge readiness.

Reuse applicable reviews. Follow [test results](../open-pr/test-results.md) to reuse verification and publish any new test results on the PR. Use targeted review for missing evidence or changes that invalidate earlier reviews. Run the full [review-pr](../review-pr/SKILL.md) or [harden-pr](SKILL.md) workflow only when requested.

## Address findings and CI

Verify review claims against current code using [the review checklist](checklist.md). Fix real defects within scope, explain supported dismissals, and report deferred work. Earlier dismissals are context; new evidence can reopen them. Review bodies can contain actionable findings that GitHub could not place inline, so read them as well as threads.

Use `pr.py reply --thread <id> --body-file <file> --model <id>` for replies and `pr.py resolve --thread <id>` after addressing a thread. The helper adds attribution.

Push a code fix before replying with its commit and evidence. Resolve a dismissed or deferred thread with its rationale. Leave questions requiring the user unresolved and continue independent work. A previous agent reply alone does not mean a thread awaits the user; read the conversation.

Correct a title or description finding with `gh pr edit`.

Read failing job logs before choosing a fix, rebase, or retry. A failure outside the diff can be an interaction, an existing base failure, or infrastructure trouble. Determine which. Retry a suspected transient failure when the evidence supports it; repeated failure calls for investigation. Report unavailable checks and external blockers accurately.

For conflicts, read the intent on both sides and preserve it through the resolution. Ask only when the intents require a product decision. After a push, rebase, or retarget, compare the patch and integration assumptions with the reviewed version and refresh affected verification. After squash-merging a parent, replay only the child's commits from the previous parent tip onto the new base.

## Push and wait

Apply [open-pr's diff cleanup and pre-push protection](../open-pr/SKILL.md) to fixes. Update any PR description made inaccurate by the changes. Prefer new commits for review fixes; use a rebase when the base or conflicts require it. Before pushing, fetch the remote head and compare it with the snapshot. If another actor advanced the branch, inspect their work before proceeding. Use an explicit lease for a necessary rewritten push.

Take a fresh snapshot after each push. Use `pr.py wait --pr <n> --max-minutes 0.5` to wait in short intervals while checks or mergeability settle. It returns when they settle, the head changes, the PR closes, checks fail or stall, or the interval expires. Inspect the returned snapshot; returning does not imply success. An API failure is missing state, never a clean result. Retry a transient failure, then report it if it persists. Stay quiet during routine waiting.

## Finalize reviewed PRs

Hardening and fix-and-review finish when every in-scope PR has its repairs committed and pushed, current descriptions and review threads, and passing required checks. Required reviews must be complete and apply to each remote head, with no unresolved in-scope defects. Use the procedures above, routing stack changes through the designated owner.

Honor explicit report-only, read-only, or local-only limits. If publication, verification, or a needed decision is blocked, report the unfinished PRs and the blocker.

## Finish or merge

When hardening was requested, confirm its assessment covers the final head, refreshing affected coverage through [harden-pr](SKILL.md) as needed. For a narrower follow-through request, finish when its findings and checks are addressed. Report each PR's URL and final head SHA, what changed, remaining findings, CI, required approvals, and unavailable coverage.

Before an authorized merge, take a fresh snapshot, confirm that findings and user decisions are addressed and required checks and approvals pass on the intended head, and use `gh pr merge <n> --squash --match-head-commit <sha>`. Respect the user's merge disposition. If it is absent, stop with the finalized PRs open. For stacked PRs, retarget children to the parent's base before deleting the parent branch.

For merge-when-ready, wait and merge the verified head explicitly. Use auto-merge only when requested and repository controls prevent unverified head changes while pending. Matching the head at scheduling time alone is insufficient. Confirm the merged state before reporting the PR as landed.
