---
name: full-send
description: Take a settled task through verified PRs to merge and closeout without further input. Say "no merge" to stop at merge-ready.
disable-model-invocation: true
---

# Full send

**You own the task from prompt to landed PRs. The user is not watching.** Invoking this skill authorizes execution, PR delivery, merging, an [admin merge](#5-merge), follow-up issues, and lane cleanup, and delegates every pick a later PR can undo. It also lifts the ask-first step from any test method that verification needs, such as running the app, browser automation, or screenshots. [Gate](#4-gates) actions and harness approval prompts still stop. With "no merge" in the request, stop each PR at merge-ready. When a linked skill ends with its report, or stops for one of these authorizations, continue to the next step. Every other stop in a linked skill still holds.

## 1. Start

1. For a simple task, or one already settled, for example by [grilling](../grilling/SKILL.md), skip the questions. Otherwise ask one round of questions, each with a recommended answer. Cover open questions that block the work. Also cover production deploys, live systems, destructive actions, and secrets, when the task touches one and the request leaves it open. When a plan's live checks need an environment that a later unit builds, ask whether the plan's PRs may merge before those checks run. After this step, reach the user only through gates.
2. Create `assumed-decisions.md` in the task's store, `.scratch/<task>/` per [Locate the store](../orchestrate/STATE.md#locate-the-store). Start it with the request and whether merging is authorized. Post its absolute path in the thread.

## 2. Route

- When [multi-phase-plan](../multi-phase-plan/SKILL.md) step 1 would skip the plan, follow [implement](../implement/SKILL.md), then [ship-pr](../ship-pr/SKILL.md).
- Otherwise follow multi-phase-plan with the plan in this store, then [orchestrate](../orchestrate/SKILL.md). Record this skill's authorizations, with the full [admin merge](#5-merge) rule, and the start-round answers in the plan and standing orders. Mark each plan gate that an assumed decision settles as decided by the coordinator. Record a repository-required approval as an admin merge case, not a review gate.

On either route, the agent that implements work that is already built starts [implement](../implement/SKILL.md) at step 5.

## 3. Assumed decisions

An **assumed decision** is a delegated pick, such as an architect candidate, a scope call, a [prototype](../prototype/SKILL.md) UI variant, or a choice a reviewer or bot asks for. Take the recommended option and append it to `assumed-decisions.md`. On the orchestrate route, workers report their picks and the coordinator appends them. A defect that a reviewer reports, including a visual one, is a review finding, not a pick.

```markdown
## D<n>. <question>
- Options: <A>; <B>
- Picked: <option>, because <reason>
- Undo: <what reversing it takes, with the PR or commit>
```

In each PR body, list that PR's entries under "Assumed decisions", one line each.

When the user sends a message that steers the work, apply it at the next checkpoint on the orchestrate route. On the implement route, apply it before the next commit. When it overrides an assumed decision, update the entry. When the affected work has merged, deliver the change as a new PR under this skill.

## 4. Gates

Each of these conditions is a gate:

- a deploy, live-system, destructive, or secrets action the start round left open
- missing access
- a change you cannot verify on the matching surface of [implement](../implement/SKILL.md) step 5, unless the start round let it merge before that check
- a human change request
- a missing approval that the [admin merge](#5-merge) does not clear
- a head whose record check in [Finish or merge](../harden-pr/pr-work.md#finish-or-merge) ended with `our_reviews.on_head` at 0
- a [review-pr](../review-pr/SKILL.md) run that stopped at its pass limit
- in-scope harden-pr defects after three fix-and-review rounds
- a review bot silent through both timers in [Merge](#5-merge) step 2

On the orchestrate route, park it through [orchestrate's escalation](../orchestrate/SKILL.md#escalation). Otherwise write its [decision request](../orchestrate/SKILL.md#messages-to-the-user) to the store's `open-decisions.md`, and record the answer there when it arrives. Continue unblocked work. When nothing can move, ask with the question tool if one exists, otherwise end the turn with the request. Send a push notification if the harness has one.

Before ending a turn, check that a running worker, watcher, timer, or promised message from another thread will wake you. Otherwise, check that each unfinished unit waits on a gate. If neither holds, dispatch ready work first, including the review that [Merge](#5-merge) step 1 requires.

In T3 Code, a PR watch ends without waking you when its PR merges. It also skips comments and reviews from your GitHub account, which your other threads share. To wait on another thread's merge or review, send that thread your thread ID. Ask it to message you with `t3_thread_send` after it merges or reviews the PR. T3 Code forbids polling loops for PR waits, so that promised message is the only wake for that merge or review.

## 5. Merge

Before merging, or before reporting merge-ready under "no merge":

1. Get a converged [review-pr](../review-pr/SKILL.md) fix-and-review run on the final head. After a run has stopped at its pass limit, a merge confirmation for that head under [Finish or merge](../harden-pr/pr-work.md#finish-or-merge) also qualifies. A commit after either needs a new run. Treat a consequential PR as a hardening request for [harden-pr](../harden-pr/SKILL.md). Then run the record check in [Finish or merge](../harden-pr/pr-work.md#finish-or-merge), also under "no merge".
2. Wait for a review of the final head from each review bot seen on this PR or recent merged PRs. In T3 Code, call `watch_pull_request` once per PR and end the turn while checks are pending. When checks pass and a bot has not yet reviewed the final head, start a 30-minute timer as a background job ([run-job](../run-job/SKILL.md) or [codex-background-jobs](../codex-background-jobs/SKILL.md)). Check [`pr.py status`](../harden-pr/pr-work.md) when the timer fires or a review arrives. When the timer fires first, request a re-review where the bot supports one and start a second timer.
3. File each actionable follow-up, including `followups.md` entries, as a GitHub issue linked from the PR. List other notes under `Deferred follow-ups` in the PR body.

**Admin merge.** When the base branch's protection and rulesets show repository-required approvals as the only unmet requirement, and every check on the head is green, follow [Finish or merge](../harden-pr/pr-work.md#finish-or-merge) without its required-approvals check and add `--admin` to its merge command. A required check that has not run is unmet. A merge queue or a human change request rules out the admin merge. Leave human reviews for humans to dismiss.

## 6. Close

1. When the default branch runs CI after merges, wait for the run on each merge commit as a background job. If the PR broke it, ship a fix or a revert under this skill. On the orchestrate route, orchestrate's retro watcher does the waiting.
2. Follow [worktree-cleanup](../worktree-cleanup/SKILL.md). If it stops with work remaining, or a gate is still open, send a push notification naming what remains.
