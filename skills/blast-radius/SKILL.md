---
name: blast-radius
description: Find what a change breaks beyond its diff.
disable-model-invocation: true
---

# Blast radius

Report the breakage a symbol search cannot find, before the change ships, and prove the safety fact by running code. The safety fact is the one fact the change is safe because of. The deliverable is the proof, not the write-up.

## Steps

1. **Read the change.** Take the diff, the symbols it adds, changes, and deletes, and what the code now does differently, including what the diff does not spell out. For a PR, run `gh pr view <n> --json title,body,commits,comments,reviews` and `gh pr diff <n>`. For history, run `git log --follow -p` on the files and `git blame -L <start>,<end> <file>` on the changed lines. Done when you can state what changed in one paragraph without the diff in front of you.
2. **Find the safety fact.** Most risky-looking changes are safe because of one fact, such as "this call only drops cache entries that are already dead". Name it. If it holds, most of the risky cases clear at once. Spend the time here. Done when the safety fact is one sentence.
3. **Look where grep stops.** Check each channel and record a `file:line`, or state that the channel does not apply:
   - the source of each library you call, at its pinned version, and any local patch
   - when things run: microtasks, unmount and teardown, framework lifecycle
   - the JSON an API returns, a database column, a wire format
   - another language reading the same bytes
   - feature flags
   - code three hops downstream

   Done when every channel has a `file:line` or a stated reason it does not apply.
4. **Weigh each risk.** Keep a risk when it has a real chance of happening and a real cost if it does. Cite a real `file:line` for each. Label each claim as cited, inferred, or unknown. Report the searches that found nothing; a search that finds nothing is an answer. Never invent a caller or an API. Done when every kept risk carries a `file:line`, a likelihood, a cost, and a label, and every cleared risk says why.
5. **Prove the safety fact.** Write a script or test that calls the real code, run it, and paste what happened. Push the fact down the ladder in [`evidence.md`](evidence.md) and say where it stopped. A safety fact that cannot reach rung 4 is unproven; say so instead of writing it up as settled. For a wide change, run steps 2 to 5 in parallel reviewers on different models and merge their answers, the way [`../review-pr/SKILL.md`](../review-pr/SKILL.md) does; different models catch different real bugs. Done when the proof is pasted, or the fact is marked unproven.

## Hand back

Write it with [`../technical-writing/SKILL.md`](../technical-writing/SKILL.md). Strip anything private before it goes anywhere public.

- **What it does.** What changed, including the part that is not obvious.
- **The safety fact.** The fact, the rung it reached, the proof. Unproven when you could not prove it.
- **Risks.** Only the real ones, each with how it breaks, the `file:line`, how likely, how bad, and how to check. Paste the proof for the ones that matter.
- **Cleared.** What you checked and why it is fine.
- **Before you merge.** The cheapest test or reproduction that catches the worst risk you kept, including the script you wrote.

**Reply.** The hand-back above, with the safety fact either proven or marked unproven.
