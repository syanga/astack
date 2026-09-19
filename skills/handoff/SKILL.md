---
name: handoff
description: Prepare a handoff for another session, or resume from an existing handoff.
argument-hint: "What will the next session be used for?"
disable-model-invocation: true
---

Write a handoff document summarising the current conversation so a fresh agent can continue the work. If the request includes pausing work, first read [pause safely](PAUSE.md). To pick up an existing handoff, follow [session pickup](RESUME.md) instead.

## Use the existing task record

- **Execution store.** Follow [the shared execution-state convention](../orchestrate/STATE.md) to checkpoint the plan's existing store. The latest dated entry in `overview.md` is the handoff; regenerate `status.md`. A delegated worker returns its handoff to the coordinator, which owns shared writes.
- **Decision map without an execution store.** Keep [Wayfinder's](../wayfinder/SKILL.md) map and tickets authoritative. Append the handoff to the current ticket, or the map's Notes if no ticket is selected, and link it from the map. Use the chosen tracker's operations and existing publication authorization. If a tracker update is unavailable or unauthorized, save a local handoff and name the pending update.
- **Standalone task.** Save a uniquely named handoff file in the temporary directory of the user's OS, outside the current workspace. Use a user-specified destination when provided. A local temporary file does not transfer to another machine; arrange an authorized transfer when the receiving session is remote.

Keep planning-only handoffs in the map when present, otherwise in a standalone note. With both a decision map and an execution store, write the handoff in the store and link the map.

## Capture the resume point

Include the goal and current scope, decisions made, completed and pending work, verification evidence and its limits, blockers, and the exact next action. Record execution, merge, and publication authorization already given and any approvals still needed.

For coding work, identify the repository, worktree, branch, head SHA, and uncommitted changes. Identify active workers and who still owns their work. Link resource and preview cleanup records so the next session can preserve or tear down what this session started. Writing a handoff alone does not stop workers or transfer ownership.

Include a "suggested skills" section in the document, naming which skills the next agent should read and why. Give paths or links that the receiving session can resolve.

Do not duplicate content already captured in other artifacts (specs, plans, ADRs, issues, commits, diffs). Reference them by path or URL instead.

Redact any sensitive information, such as API keys, passwords, or personally identifiable information.

If the user passed arguments, treat them as a description of what the next session will focus on and tailor the doc accordingly.

## Report

When no decision trail exists, use the existing task record. Handoff alone does not start a decision log.

When the task has a decision trail, follow [show-me-your-work](../show-me-your-work/SKILL.md) for its audit, review, and Attention findings. If the user's stop instruction prevents that review, report the review as pending in Attention and explain why.

Return the handoff's absolute path or URL, the first action on resume, and any ownership or transfer still pending. The next session starts from that pointer; this skill does not automatically discover active work.
