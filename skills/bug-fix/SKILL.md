---
name: bug-fix
description: Bug fixes. Use when the user asks to fix a bug, such as wrong behavior, a crash, a hang, or a failing test.
---

# Bug fix

**You own this task. Plan, review, verify.** Read [scale the workflow](../principles/scale-the-workflow.md) before step 1. Review the findings and diff yourself.

Be scientific. Tie each fix to runtime evidence. When evidence refutes a hypothesis, remove the experimental changes it motivated while preserving unrelated work. Ship the smallest change the evidence justifies.

1. Reproduce the user's failure yourself through the affected UI, CLI, or API in an authorized environment. Use [diagnosing-bugs](../diagnosing-bugs/SKILL.md) phases 1 through 4 for the feedback loop, minimization, and cause. This workflow owns implementation and delivery below. Use the available control tools. If access prevents reproduction, state what you tried and the specific missing access or artifact. Continue useful investigation with unverified hypotheses clearly labeled.
2. Narrow the cause with the diagnosis loop. Use [how](../how/SKILL.md) for the affected subsystem, unless you already hold a traced model of it. For a regression, use [why](../why/SKILL.md) for its history. When how and why both run, run them in parallel. Each experiment should distinguish the remaining hypotheses. Confirm the mechanism with runtime evidence before designing a fix.
3. Plan the fix. If it crosses a function boundary, read [architect](../architect/SKILL.md) first. Before implementation, follow [tdd's bug-fix workflow](../tdd/BUG-FIX.md) to establish failing-before evidence at a practical test boundary, or explain the alternative check. When you delegate the fix, give the worker the scope, confirmed mechanism, reproduction command, and acceptance criteria. Review the diff.
4. Verify on the same UI, CLI, or API. Rerun the original reproduction, the regression check, and relevant adjacent checks. "Inconclusive" or a check of a different interface is not a pass. Apply diagnosing-bugs' cleanup and intermittent-failure criteria.
5. Keep the failing-before and passing-after evidence. Follow [sequence-verifiable-units](../principles/sequence-verifiable-units.md) for ordered commits when the work has several units.
6. Use [open-pr](../open-pr/SKILL.md) within the task's delivery scope. When that scope stops short of a PR, such as a request for local edits only, run [review-pr](../review-pr/SKILL.md) in fix-and-review mode instead. Merging is a separate action.

For a unit in a multi-phase plan, follow [the shared execution-state convention](../orchestrate/STATE.md). Reuse its store. For a session transfer, read [handoff](../handoff/SKILL.md). Include the reproduction command, hypotheses ruled out, current instrumentation, and evidence paths.

**Reply:** what was broken, root cause, fix, and verification. Include redacted failing-before and passing-after output, or state which evidence is unavailable.
