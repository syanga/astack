---
name: investigation
description: Investigate a technical question and return a cited explanation or recommendation.
disable-model-invocation: true
---

# Investigation

**You own the answer. Plan, route, write.**

Investigation requests are read-only. They produce a cited explanation or a recommendation. State the question and the evidence needed to answer it. Reuse the scope already established with the user.

1. Choose the method that answers the question. For code behavior, read [how](../how/SKILL.md). For motivation or regression history, also read [why](../why/SKILL.md). For a suspected failure, read [diagnosing-bugs](../diagnosing-bugs/SKILL.md) in diagnosis-only scope. For an existing profile, trace, or heap snapshot, follow [trace forensics](../diagnosing-bugs/TRACE-FORENSICS.md).
2. Keep the inquiry read-only, including delegated work. Use existing code, captures, history, and checks that fit that scope. If an experiment needs instrumentation or other writes, report the missing evidence and the proposed experiment. A question alone does not authorize it.
3. Produce the `how`-shaped explanation, or a recommendation with a tradeoffs table for a decision between alternatives. Cite the evidence, separate observations from hypotheses, and name any gaps that limit the conclusion. For "are we sure?", give your judgment and reasons, including evidence against the premise.
4. Apply [unslop](../unslop/SKILL.md) to the reply.

Stop with the answer. When the user requests implementation, route to [bug-fix](../bug-fix/SKILL.md), [perf-issue](../perf-issue/SKILL.md), or [implement](../implement/SKILL.md). Carry forward the findings rather than repeating the inquiry. For a session transfer, read [handoff](../handoff/SKILL.md) and preserve the current scope and evidence pointers.

**Reply:** the answer or recommendation, cited evidence, confidence, and unresolved questions.
