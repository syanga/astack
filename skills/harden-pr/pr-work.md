# Follow the PR

Use `python3 "<harden-pr directory>/scripts/pr.py" status --pr <n>` for a GitHub snapshot and `history --pr <n>` for earlier reviews and thread replies. Add `--repo owner/name` when needed. `reply` and `resolve` take no `--pr` or `--repo`, because thread IDs are global. A status-only request ends with a report. Read the current head, checks, approvals, unresolved threads, and review evidence before changing anything. The helper reports state, not a verdict about correctness or merge readiness.

Reuse applicable reviews. Follow [test results](../open-pr/test-results.md) to reuse verification and publish any new test results on the PR. Use targeted review for missing evidence or changes that invalidate earlier reviews. Run the full [review-pr](../review-pr/SKILL.md) or [harden-pr](SKILL.md) workflow only when the user requests it or the record check in [Finish or merge](#finish-or-merge) calls for review-pr.

## Test a batch

When several PRs will merge together, test them once as a batch after their heads are final, instead of once per PR. Merge their heads onto the latest base in a scratch branch and run the suites that cover the batch there. One run costs less, and it tests the combined tree that will land. For a stack, include only its highest PR if that PR contains every lower head. When the batch fails and the base passes, bisect the batch to the failing PR or combination. Publish the result on each PR under [test results](../open-pr/test-results.md), listing the base SHA and every head SHA. Before each merge, confirm that since the tested base, the target branch has gained only merged batch members and the remaining heads have changed only by conflict-free rebases. Otherwise, merge the remaining heads onto the current base in a new scratch branch. When the batch's tested commit cannot be fetched, rebuild it by merging the listed head SHAs onto the listed base SHA. Compare the new scratch branch with the tested commit under [Reuse a published result](../open-pr/test-results.md#reuse-a-published-result), and rerun the covering suites that do not keep their result.

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

Finalizing ends when every in-scope PR has its repairs committed and pushed, current descriptions and review threads, and passing required checks. Required reviews must be complete and apply to each remote head, with no unresolved in-scope defects. Use the procedures above, routing stack changes through the designated owner.

Honor explicit report-only, read-only, or local-only limits. If publication, verification, or a needed decision is blocked, report the unfinished PRs and the blocker.

## Finish or merge

When hardening was requested, confirm its assessment covers the final head, refreshing affected coverage through [harden-pr](SKILL.md) as needed. For a narrower follow-through request, finish when its findings and checks are addressed. Report each PR's URL and final head SHA, what changed, remaining findings, CI, required approvals, and unavailable coverage.

Before an authorized merge, run the record check, which passes when the head has a signed review record. When the user explicitly declined review or limited publication, skip the record check. Otherwise, run `pr.py status` before the scratch merge. When `our_reviews.on_head` is 0, take the first case that applies:

1. If a review-pr or harden-pr run in this session reviewed the head, post its record through [posting.md](posting.md).
2. If the current head is a conflict-free rebase or replay, with an unchanged patch, of an earlier head that has a signed review record, repost that record through posting.md. Drop the helper's prefix line from the old body, and use an empty `comments` list.
3. Otherwise, run [review-pr](../review-pr/SKILL.md) on the head. When the PR's latest complete signed record reviewed a commit the branch still contains, pass that commit as the fixed point. When the task authorizes repairs, use fix-and-review mode. Otherwise, use report-only mode.

After the case you took, run `pr.py status` again. If the head changed, repeat this record check on the new head. If the head is unchanged and `our_reviews.on_head` is still 0, leave the PR open. Report that the head has no signed review record.

After the record check passes or you skip it, fetch the base, merge the head onto it in a scratch branch, and record that base SHA. Apply [Reuse a published result](../open-pr/test-results.md#reuse-a-published-result) with the scratch branch as HEAD and the recorded SHA in place of `origin/<base branch>`, without fetching again. Run the covering suites that do not keep their result on the scratch branch. The PR head stays unchanged, and a squash merge lands the scratch branch's tree while the base stays at that SHA. When the scratch merge conflicts or a suite fails, stop and follow [Address findings and CI](#address-findings-and-ci). For a batch result, follow [Test a batch](#test-a-batch) instead. Then take a fresh snapshot. If the head changed, run the record check on the new head, then repeat this paragraph. Confirm that findings and user decisions are addressed, and required checks and approvals pass. Fetch the base again. If it no longer points at the recorded SHA, build a new scratch branch on the new base and repeat this paragraph. Otherwise, merge with `gh pr merge <n> --squash --match-head-commit <sha>`. Respect the user's merge disposition. If it is absent, stop with the finalized PRs open. For stacked PRs, retarget children to the parent's base before deleting the parent branch.

For merge-when-ready, wait, run the record check, and merge the verified head explicitly. Use auto-merge only when requested and repository controls prevent unverified head changes while pending. Matching the head at scheduling time alone is insufficient. Confirm the merged state before reporting the PR as landed.
