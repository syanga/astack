# Follow the PR

Use `python3 "<harden-pr directory>/scripts/pr.py" status --pr <n>` for a GitHub snapshot and `history --pr <n>` for earlier reviews and thread replies. Add `--repo owner/name` when needed. `reply` and `resolve` take no `--pr` or `--repo`, because thread IDs are global. A status-only request ends with a report. Read the current head, checks, approvals, unresolved threads, and review evidence before changing anything. `status` omits the PR description, so read it with `gh pr view <n> --json body`. The helper reports state, not a verdict about correctness or merge readiness.

Reuse applicable reviews. Follow [test results](../open-pr/test-results.md) to reuse verification and publish any new test results on the PR. Use targeted review for missing evidence or changes that invalidate earlier reviews. Run the full [review-pr](../review-pr/SKILL.md) or [harden-pr](SKILL.md) workflow only when the user requests it or the record check in [Finish or merge](#finish-or-merge) calls for review-pr.

## Test a batch

When several PRs will merge together, test them once as a batch after their heads are final, instead of once per PR. Merge their heads onto the latest base in a scratch branch and run the suites that cover the batch there. One run costs less, and it tests the combined tree that will land. For a stack, include only its highest PR if that PR contains every lower head. When the batch fails and the base passes, bisect the batch to the failing PR or combination. Publish the result on each PR under [test results](../open-pr/test-results.md). Before each merge, confirm that since the tested base, the target branch has gained only merged batch members and the remaining heads have changed only by conflict-free rebases. Otherwise, merge the remaining heads onto the current base in a new scratch branch. Compare the new scratch branch with the tested commit under [Reuse a published result](../open-pr/test-results.md#reuse-a-published-result), and rerun the covering suites that do not keep their result.

## Address findings and CI

Verify review claims against current code using [the review checklist](checklist.md). Fix real defects within scope, explain supported dismissals, and report deferred work. Earlier dismissals are context; new evidence can reopen them. Review bodies can contain actionable findings that GitHub could not place inline, so read them as well as threads.

Use `pr.py reply --thread <id> --body-file <file> --model <id>` for replies and `pr.py resolve --thread <id>` after addressing a thread. The helper adds attribution.

Push a code fix before replying with its commit and evidence. Resolve a dismissed or deferred thread with its rationale. Leave questions requiring the user unresolved and continue independent work. A previous agent reply alone does not mean a thread awaits the user; read the conversation.

Correct a title or description finding with `gh pr edit`.

Read failing job logs before choosing a fix, rebase, or retry. A failure outside the diff can be an interaction, an existing base failure, or infrastructure trouble. Determine which. Retry a suspected transient failure when the evidence supports it; repeated failure calls for investigation. Report unavailable checks and external blockers accurately.

For conflicts, read the intent on both sides and preserve it through the resolution. Ask only when the intents require a product decision. After a push, rebase, or retarget, compare the patch and integration assumptions with the reviewed version and refresh affected verification. After squash-merging a parent, replay only the child's commits from the previous parent tip onto the new base.

## Push and wait

Apply [open-pr's diff cleanup and pre-push protection](../open-pr/SKILL.md) to fixes. Update any PR description made inaccurate by the changes, but keep any pass-limit alert from [posting.md](posting.md). Prefer new commits for review fixes; use a rebase when the base or conflicts require it. Before pushing, fetch the remote head and compare it with the snapshot. If another actor advanced the branch, inspect their work before proceeding. Use an explicit lease for a necessary rewritten push.

Take a fresh snapshot after each push. Use `pr.py wait --pr <n> --max-minutes 0.5` to wait in short intervals while checks or mergeability settle. It returns when they settle, the head changes, the PR closes, checks fail or stall, or the interval expires. Inspect the returned snapshot; returning does not imply success. An API failure is missing state, never a clean result. Retry a transient failure, then report it if it persists. Stay quiet during routine waiting.

## Finalize reviewed PRs

Finalizing ends when every in-scope PR has its repairs committed and pushed, current descriptions and review threads, and passing required checks. Required reviews must be complete and apply to each remote head, with no unresolved in-scope defects. Use the procedures above, routing stack changes through the designated owner.

Honor explicit report-only, read-only, or local-only limits. If publication, verification, or a needed decision is blocked, report the unfinished PRs and the blocker. When a review-pr run stops at its pass limit, push its repairs and stop. Leave the remaining findings open.

## Finish or merge

When hardening was requested, confirm its assessment covers the final head, refreshing affected coverage through [harden-pr](SKILL.md) as needed. For a narrower follow-through request, finish when its findings and checks are addressed. Report each PR's URL and final head SHA, what changed, remaining findings, CI, required approvals, and unavailable coverage.

A pass-limit alert from [posting.md](posting.md) in the PR description blocks the merge until the user confirms merging the head SHA. A confirmation for an earlier SHA does not count. Before you ask for that confirmation, show the user the remaining findings in the pass-limited review record and on the PR. For that head, the user's confirmation counts as addressing those findings. Leave them unrepaired.

Before an authorized merge, run the record check, which passes when the head has a signed review record. When the user explicitly declined review or limited publication, skip the record check. Otherwise, run `pr.py status` before the scratch merge. When `our_reviews.on_head` is 0, take the first case that applies:

1. If a review-pr or harden-pr run in this session reviewed the head, post its record through [posting.md](posting.md).
2. If the current head is a conflict-free rebase or replay, with an unchanged patch, of an earlier head that has a signed review record, repost that record through posting.md. Drop the helper's prefix line from the old body, and use an empty `comments` list.
3. Otherwise, run [review-pr](../review-pr/SKILL.md) on the head, with the fixed point that [open-pr](../open-pr/SKILL.md) picks. When the task authorizes repairs, use fix-and-review mode. Otherwise, use report-only mode.

After the case you took, run `pr.py status` again. If the head changed, repeat this record check on the new head. If the head is unchanged and `our_reviews.on_head` is still 0, leave the PR open. Report that the head has no signed review record.

After the record check passes or you skip it, fetch the base, merge the head onto it in a scratch branch, and record that base SHA. Apply [Reuse a published result](../open-pr/test-results.md#reuse-a-published-result) with the scratch branch as HEAD and the recorded SHA in place of `origin/<base branch>`, without fetching the base again. The `<tested commit> HEAD` diff then also lists the base's changes. On the scratch branch, run the covering suites that do not keep their result, without [the rebase step](../open-pr/test-results.md#rebase-before-the-published-run). Publish any such run under [Publish the result](../open-pr/test-results.md#publish-the-result). The PR head stays unchanged, and a squash merge lands the scratch branch's tree while the base stays at that SHA. When the scratch merge conflicts or a covering suite's result is not `pass`, stop and follow [Address findings and CI](#address-findings-and-ci). For a batch result, follow [Test a batch](#test-a-batch) instead. Then take a fresh snapshot. If the head changed, run the record check on the new head, then repeat this paragraph. Confirm that findings and user decisions are addressed, and required checks and approvals pass. Fetch the base again. If it no longer points at the recorded SHA, build a new scratch branch on the new base and repeat this paragraph. Respect the user's merge disposition. If it is absent or does not allow a merge, stop with the finalized PRs open. Otherwise, take these steps:

1. If the PR description has a pass-limit alert, continue only when the user confirmed merging this head SHA under the gate above. Append `> Merge confirmed by the user for <sha>.` to the alert. Resolve each thread that an agent or review bot opened for a finding that the confirmation addressed. In each resolution, cite the confirmation.
2. Read the target branch's effective rules to determine whether it requires a merge queue. Check whether the proposed command explicitly enables auto-merge. If either applies, the command can schedule a merge. If neither applies and the CLI command merges immediately or refuses, use the immediate-merge path in step 3. If scheduling behavior is unknown, stop and report the blocker. Before a scheduling command, verify the effective repository controls on the target branch. Record evidence that they prevent an unreviewed head from merging while the request is pending, including applicable bypasses. A required approval or green check alone does not establish that it enforces this workflow's review requirement. Neither `--match-head-commit` nor the local merge hook protects later head changes. If the controls are absent, unreadable, or insufficient, stop before issuing the command and report the blocker. Before either path, read the existing auto-merge and queue state and request ownership. If another actor owns a pending request, or ownership is unknown, report it and stop before issuing a merge command. Record our request identity so a later cancellation can be limited to our request.
3. Merge with `gh pr merge <n> --squash --match-head-commit <sha>`. Run it alone, on one line, with literal unquoted arguments and no redirects or pipes. When the working directory is outside the PR's repository, add `--repo <owner>/<repo>`. Pass any commit body with `--body-file <path>`, using a path without spaces or `~`. [claude-merge-gate](../claude-merge-gate/SKILL.md) lists the exact form the merge gate allows.
4. Follow [Confirm the merge result](#confirm-the-merge-result) after the command, even if it returns no output or fails.

For stacked PRs, retarget children to the parent's base before deleting the parent branch.

For merge-when-ready, wait, run the record check, and merge the verified head through the steps above. Use explicit auto-merge only when requested, with the same preflight and result confirmation.

## Confirm the merge result

Read fresh remote state with `gh pr view <n> --json state,mergedAt,mergeCommit,headRefOid`, adding `--repo <owner>/<repo>` outside the repository. Inspect auto-merge and queue state through the GitHub API when the PR is still open. Command success, silence, and queue admission are not evidence of a merge.

- When `state` is `MERGED` and merge metadata confirms it, report the merge commit and time. If the head differs from the verified head, report that discrepancy and the missing review coverage. Do not describe the changed head as verified.
- When the PR is open with auto-merge enabled or a queue entry, report it as pending. Recheck that the repository controls still prevent an unreviewed head from merging. If those controls cannot be established, cancel only the pending request this run created, using the operation appropriate to its auto-merge or queue state. Read fresh state to verify that our request is no longer pending. An unsuccessful cancellation, unknown ownership, or inability to verify cancellation is a blocker. Report it immediately. If the PR merged during cancellation, report the actual merge and any review discrepancy instead of claiming cancellation succeeded.
- While safely pending, follow the harness's PR waiting mechanism and recheck this procedure on resumption. If the head changes, invalidate the old head's merge readiness and any SHA-specific user confirmation. Repeat the record check and refresh affected review, tests, approvals, and findings under [Finish or merge](#finish-or-merge) before treating the new head as ready. Repeat preflight before any new merge command.
- When the PR is open without a pending request, report that it has not merged. Reassess readiness under [Finish or merge](#finish-or-merge) before retrying. A closed, unmerged PR is not landed. Report it without reopening it.
- When the API fails or the state or merge metadata is unknown or inconsistent, retry a transient read failure, then report unresolved state as a blocker. Do not schedule another merge or report the PR as landed without confirmation.

After verified cancellation, leave the PR unmerged and report the missing controls. Resume merge attempts only after the preflight passes. Perform post-merge work, including stack cleanup, only after confirming the merged state.
