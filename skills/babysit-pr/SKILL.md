---
name: babysit-pr
description: One PR, three modes. Report its status, answer its review threads, or drive it to merge-ready through review, fix, and CI.
disable-model-invocation: true
---

# Babysit a PR

The policy is the Pull Requests section of the global rules. The reviewer in the loop is [`../review-pr/SKILL.md`](../review-pr/SKILL.md), which posts its findings to the PR as threads.

The loop's state lives on the PR, not in your memory. Every snapshot ends with a `next` verdict the script computes from GitHub alone: `fix`, `wait`, `review`, `merge-ready`, `hand off`, or `stop`. Follow it. It already applies the review-round cap, the CI-not-converging stop, and the stuck-check stop.

## 1. Declare the mode

The request picks one of three modes.

- `status`. Take one snapshot and report. "Check on #12", "anything outstanding on X". Also the mode for a docs-only PR.
- `threads`. Answer the review threads only. "Address the review comments".
- `drive`. Follow `next` until it says `merge-ready`, `hand off`, or `stop`. "Babysit this", "get it green". The mode when the request names none of the others.

Done when you have stated the mode and the words that picked it, or that the request named none and defaulted to `drive`.

## 2. Take a snapshot

From the repository root, run `python3 <this skill's directory>/scripts/pr.py status`. Pass `--pr <number>` for a PR other than the current branch's PR, and `--repo owner/name` for another repository. Read `next.action`, `next.blockers`, `next.handoff`, and `next.stop` first, then the fields behind them: `mergeable`, `draft`, `checks`, `recent_commits`, `threads.unresolved`, `our_reviews`, `bots`, `new_comments`, `new_reviews`. Exit code 2 means `gh` or the API failed, which is not a verdict: wait one minute, retry once, then report the failure. In `drive` mode, if `head.sha` changed since your previous snapshot and you did not push in between, another actor is on the branch: report and stop.

In `status` mode, report now and stop. In `threads` mode, go to the Threads part of step 3, then report and stop. In `drive` mode, do what `next.action` says:

| `next.action` | Do |
| --- | --- |
| `fix` | step 3 |
| `wait` | step 4 |
| `review` | step 5 |
| `merge-ready` | step 6 |
| `hand off` | report `next.handoff` to the user and stop |
| `stop` | report `next.stop` and what you tried, and stop |

Done when, in `drive` mode, you have acted on `next.action`, and in the other two modes you have reported.

## 3. Fix the blockers

Work `next.blockers` in this order: the draft state, the conflict, the threads, a folded review, then the failed checks. Clear a draft with `gh pr ready <n>`. Batch the conflict and thread fixes into one push. CI fixes follow in their own push. Take a fresh snapshot after every push.

**Conflict.** Rebase onto the base branch and resolve it following [`conflicts.md`](conflicts.md). If a hunk needs a product decision, stop and report the branch and the hunk. After the rebase, search the base for callers of every symbol the PR moves or deletes. The rebase restarts every check and outdates threads, so include it in the same push as every other fix.

**Threads.** Work every unresolved thread whose `awaiting_user` is false, following [`triage.md`](triage.md). Reply with `python3 <this skill's directory>/scripts/pr.py reply --thread <id> --body-file <file> --model <your model id>`. Resolve a fixed or dismissed thread with `pr.py resolve --thread <id>`, which exits 2 when the thread did not resolve. Push before replying, so the reply cites a commit that exists.

**A folded review.** When `our_reviews.folded_act_on` is above zero, the head's act-on findings sit in a review body in `new_reviews` and opened no threads. Fix them. The push moves the head and clears the fold. A folded act-on finding that does not hold cannot be cleared from the PR: report it to the user with your evidence and stop.

**Failed checks.** Classify before any retry. Fix a failure in the diff's own code in a commit. A failure in code the diff never touched means a stale base: run `git fetch origin <base>`, then `git merge-base --is-ancestor origin/<base> HEAD`, and rebase when that command exits non-zero. Rerun a workflow only when `gh run view <run id> --json attempt` shows attempt 1. A failure on a later attempt is not flake, so read the job log. A failure that a fix in the diff, a rebase, and one rerun do not clear needs the user: report the check, the attempt, and the log lines, and stop.

Done when a fresh snapshot's `next.blockers` is empty, or you stopped on a hunk, a finding, or a check that needs the user. Return to step 2.

## 4. Wait

Wait with the harness's timer, sized to the repository's usual check duration. In Claude Code that is `/loop` with no interval. Use one timer. When it fires, return to step 2. The snapshot turns `next.action` to `stop` when a check stays pending past the limit, or when GitHub has still not computed mergeability by then, so the wait is bounded. When a bot that reviewed an earlier commit has `since_head` false after the checks finish, name the missing bot pass in the report and go on. Answer a user question mid-loop and continue. Done when the timer has fired.

## 5. Review the head

Run [`../review-pr/SKILL.md`](../review-pr/SKILL.md) on the PR. If review-pr reports that it could not post the review, report that and stop, since an unposted review is not counted and would be run again. Otherwise return to step 2. Done when `our_reviews.on_head` is at least 1, or the failed post is reported.

## 6. Merge or hand off

Take one more snapshot and confirm `next.action` is still `merge-ready` on the `head.sha` you intend to merge. Then carry out the disposition from the request as the global rules describe, merging with `gh pr merge <n> --squash --match-head-commit <head.sha>` when it says merge, so a push that lands after the snapshot fails the merge. If the merge command fails, report its message and stop. For a child PR in a stack, retarget the child to the parent's base before the parent branch is deleted, or GitHub closes the child. Done when the verdict was still `merge-ready` on that SHA and you have carried out the disposition or reported it.

## Report

End every mode with a report to the user that carries:

- the mode, the head SHA, and the last `next` verdict
- the CI state, the review state, and the merge state from the last snapshot
- `our_reviews.total`, and what each review round found
- what you fixed, and what you dismissed with the reason for each
- what is pending, including a bot pass that never arrived
- what needs the user: the asks with their thread links, and any `stop` or `hand off` reason
- any dismissal that repeated during the run, as a pattern proposed for `triage.md` in its own PR
