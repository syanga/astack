---
name: babysit-pr
description: One PR, three modes. Report its status, answer its review threads, or drive it to merge-ready through review, fix, and CI.
disable-model-invocation: true
---

# Babysit a PR

The policy is the Pull Requests section of the global rules. The loop's state lives on the PR, not in your memory. Every snapshot ends with a `next` verdict the script computes from GitHub alone: `fix`, `wait`, `review`, `merge-ready`, `hand off`, or `stop`. Follow it. The script applies every cap and stop.

## 1. Declare the mode

The request picks one of three modes.

- `status`. Take one snapshot and report. "Check on #12", "anything outstanding on X". Also the mode when the request names none and the PR changes only human-facing documentation, such as a README, `docs/`, or a changelog. `gh pr view <n> --json files -q '.files[].path'` lists what the PR changes. A Markdown file an agent loads, such as a skill or an instruction file, is not documentation.
- `threads`. Answer the review threads only. "Address the review comments".
- `drive`. Follow `next` until it says `merge-ready`, `hand off`, or `stop`. "Babysit this", "get it green", "run the full loop". The mode when the request names none of the others and the PR changes more than documentation. Words in the request that name a mode always win.

Done when you have stated the mode and the words that picked it, or that the request named none and which default applied.

## 2. Take a snapshot

From the repository root, run `python3 <this skill's directory>/scripts/pr.py status`. Pass `--pr <number>` for a PR other than the current branch's PR, and `--repo owner/name` for another repository. Read `next.action`, `next.blockers`, `next.handoff`, and `next.stop` first. When the first snapshot already lists a `next.handoff` reason, the run can end no better than `hand off` until the user answers, so say that at the top of the report. Read the fields behind them next: `mergeable`, `draft`, `checks`, `recent_commits`, `threads.unresolved`, `our_reviews`, `bots`, `new_comments`, `new_reviews`. Exit code 2 means `gh` or the API failed, which is not a verdict: run it once more, then report the failure. In `drive` mode, if `head.sha` changed since your previous snapshot and you did not push in between, another actor is on the branch: report and stop.

In `status` mode, report now and stop. In `threads` mode, report `next.stop` and stop when it is set. Otherwise go to step 3's Pre-push check and Threads blocks, then report and stop. In `drive` mode, do what `next.action` says:

| `next.action` | Do |
| --- | --- |
| `fix` | step 3 |
| `wait` | step 4 |
| `review` | step 5 |
| `merge-ready` | step 6 |
| `hand off` | report `next.handoff` to the user and stop |
| `stop` | report `next.stop` and what you tried, and stop |

Done when you have named the verdict, its blockers, any `next.handoff` reason, and, in `drive` mode, the step the table sends you to.

## 3. Fix the blockers

Work `next.blockers` in this order: the draft state, the conflict, the threads, a folded review, then the failed checks. Clear a draft with `gh pr ready <n>`. Put the fixes in new commits, grouped by what they change. Amend or rebase only to resolve a conflict or to refresh a stale base as [`checks.md`](checks.md) directs, because rewriting pushed commits outdates the open threads and hides what the last review covered. Batch the conflict and thread fixes into one push. CI fixes follow in their own push. Take a fresh snapshot after every push.

**Pre-push check.** A review round costs far more than this check, and a fix that needs fixing spends one. Before every push:

1. Read the fix's diff against what it answers, the thread's finding or the failed check, and confirm it answers the whole of it.
2. Apply the Clean the diff step of [`../open-pr/SKILL.md`](../open-pr/SKILL.md) to that diff, with one difference: when blast-radius runs, put its safety fact in the thread reply, since there is no PR-body section here.
3. Apply [`../technical-writing/SKILL.md`](../technical-writing/SKILL.md) to new prose.
4. Run `git fetch origin <head ref>` and confirm `git rev-parse FETCH_HEAD` prints the snapshot's `head.sha`.
5. Correct with `gh pr edit` any sentence in the PR's title or body that this fix made false.

**Conflict.** Rebase onto the base branch and resolve it following [`conflicts.md`](conflicts.md). If a hunk needs a product decision, stop and report the branch and the hunk. After the rebase, search the base for callers of every symbol the PR moves or deletes. The rebase restarts every check and outdates threads, so include it in the conflict-and-thread push.

**Threads.** Work every unresolved thread whose `awaiting_user` is false, following [`triage.md`](triage.md). Reply with `python3 <this skill's directory>/scripts/pr.py reply --thread <id> --body-file <file> --model <your model id>`. Resolve a fixed, dismissed, or deferred thread with `pr.py resolve --thread <id>`, which exits 2 when the thread did not resolve. Push before replying, so the reply cites a commit that exists. A noted finding, a bucket [`../review-pr/judgment.md`](../review-pr/judgment.md) defines, sits in the latest review body in `new_reviews` and opens no thread. Fix it in the same push when you are already changing that file and `our_reviews.last_round` is false. Otherwise list it in the report. Fix a finding about the PR's title or body with `gh pr edit`.

**A folded review.** When `our_reviews.folded_act_on` is above zero, the head's act-on findings sit in a review body in `new_reviews` and opened no threads. Fix them. The push moves the head and clears the fold. A folded act-on finding that does not hold cannot be cleared from the PR: report it to the user with your evidence and stop.

**Failed checks.** Classify each one following [`checks.md`](checks.md) before any retry.

Done when every pushed fix passed the Pre-push check, and, in `drive` mode, either a fresh snapshot's `next.blockers` is empty or you stopped on a hunk, a finding, or a check that needs the user. Return to step 2.

## 4. Wait

Run `python3 <this skill's directory>/scripts/pr.py wait`, with the same `--pr` and `--repo` as the snapshot, and a shell timeout of ten minutes. Where the shell allows less, pass `--max-minutes` one minute under its limit. If it returns with `next.action` still `wait`, run it again. If it exits 2, run it once more, then report the failure and stop. When a bot that reviewed an earlier commit has `since_head` false after the checks finish, name the missing bot pass in the report and go on. Done when the snapshot's `next.action` is no longer `wait`. Act on it as step 2 says.

## 5. Review the head

Run [`../review-pr/SKILL.md`](../review-pr/SKILL.md) on the PR. If review-pr reports that it could not post the review, report that and stop, since an unposted review is not counted and would be run again. Otherwise return to step 2. Done when `our_reviews.on_head` is at least 1, or the failed post is reported.

## 6. Merge or hand off

Take one more snapshot and confirm `next.action` is still `merge-ready` on the `head.sha` you intend to merge. If it changed, return to step 2. Then carry out the disposition from the request as the global rules describe. With no disposition in the request, report and ask. Merge with `gh pr merge <n> --squash --match-head-commit <head.sha>` when the disposition says merge, so a push that lands after the snapshot fails the merge. If the merge command fails, report its message and stop. For a child PR in a stack, retarget the child to the parent's base before the parent branch is deleted, or GitHub closes the child. Done when you have carried out the disposition or reported it on a SHA whose verdict was still `merge-ready`, or you returned to step 2 because the verdict changed.

## Report

End every mode with a report to the user that carries:

- the mode, the head SHA, and the last `next` verdict
- the CI state, the review state, and the merge state from the last snapshot
- `our_reviews.total`, and what each review round found, which `pr.py history` prints
- what you fixed, and what you dismissed or deferred with the reason for each
- what is pending, including a bot pass that never arrived
- what needs the user: the asks with their thread links, and any `stop` or `hand off` reason
- any dismissal that repeated during the run, as a pattern proposed for `triage.md` in its own PR
