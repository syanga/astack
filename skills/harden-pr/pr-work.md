# Follow the PR

Use `python3 "<harden-pr directory>/scripts/pr.py" status --pr <n>` for a GitHub snapshot and `history --pr <n>` for earlier reviews and thread replies. Add `--repo owner/name` when needed. A status-only request ends with a report. Read the current head, checks, unresolved threads, and review bodies before changing anything. The helper reports state, not a verdict about correctness or merge readiness.

## Address findings and CI

Verify review claims against current code using [the review checklist](checklist.md). Fix real defects within scope, explain supported dismissals, and report deferred work. Earlier dismissals are context; new evidence can reopen them. Review bodies can contain actionable findings that GitHub could not place inline, so read them as well as threads.

Use `pr.py reply --thread <id> --body-file <file> --model <id>` for replies and `pr.py resolve --thread <id>` after addressing a thread. The helper adds attribution.

Push a code fix before replying with its commit and evidence. Resolve a dismissed or deferred thread with its rationale. Leave questions requiring the user unresolved and continue independent work. A previous agent reply alone does not mean a thread awaits the user; read the conversation.

Correct a title or description finding with `gh pr edit`.

Read failing job logs before choosing a fix, rebase, or retry. A failure outside the diff can be an interaction, an existing base failure, or infrastructure trouble. Determine which. Retry a suspected transient failure when the evidence supports it; repeated failure calls for investigation. Report unavailable checks and external blockers accurately.

For conflicts, read the intent on both sides and preserve it through the resolution. Ask only when the intents require a product decision. After a rebase, examine changed callers and rerun the affected checks.

## Push and wait

Apply [open-pr's diff cleanup and pre-push protection](../open-pr/SKILL.md) to fixes. Update any PR description made inaccurate by the changes. Prefer new commits for review fixes; use a rebase when the base or conflicts require it. Before pushing, fetch the remote head and compare it with the snapshot. If another actor advanced the branch, inspect their work before proceeding. Use an explicit lease for a necessary rewritten push.

Take a fresh snapshot after each push. Use `pr.py wait --pr <n> --max-minutes 0.5` to wait in short intervals while checks or mergeability settle. It returns when they settle, the head changes, the PR closes, checks fail or stall, or the interval expires. Inspect the returned snapshot; returning does not imply success. An API failure is missing state, never a clean result. Retry a transient failure, then report it if it persists. Stay quiet during routine waiting.

## Finish or merge

For requested hardening, assess the final head with the review and verification described in [SKILL.md](SKILL.md). For a narrower follow-through request, finish when its findings and checks are addressed. Report what changed, remaining findings, CI, required approvals, and unavailable coverage. A bot that reviewed an earlier commit may not have reviewed the final head.

Before an authorized merge, take a fresh snapshot, confirm that findings and user decisions are addressed and required checks and approvals pass on the intended head, and use `gh pr merge <n> --squash --match-head-commit <sha>`. Respect the user's merge disposition. If it is absent, report readiness and ask. For stacked PRs, retarget children to the parent's base before deleting the parent branch.
