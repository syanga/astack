---
name: blast-radius
description: Find what a change breaks beyond its diff, and prove the safety fact by running code.
disable-model-invocation: true
---

# Blast radius

Report the breakage a symbol search cannot find, before the change ships. Prove the safety fact by running code. The safety fact is the one fact the change is safe because of.

## Steps

For a wide change, hand steps 2 to 4 to parallel reviewers on different models and merge their answers, using the independent review approach in [harden-pr](../harden-pr/sections/specialists.md).

1. **Read the change.** Take the diff, the symbols it adds, changes, and deletes, and what the code now does differently, including what the diff does not spell out. For a PR, run `gh pr view <n> --json title,body,commits,comments,reviews` and `gh pr diff <n>`. For history, run `git log --follow -p` on the files and `git blame -L <start>,<end> <file>` on the changed lines. Done when you have written one paragraph stating what changed.
2. **Find the safety fact.** Most risky-looking changes are safe because of one fact, such as "this call only drops cache entries that are already dead". Name it. If it holds, most of the risky cases clear at once. Spend the time here. Done when the safety fact is one sentence that names the `file:line` making it true.
3. **Look where grep stops.** Verify each channel and record a `file:line`, or state that the channel does not apply.
   - The source of each library you call, at its pinned version, and any local patch.
   - When things run: microtasks, unmount and teardown, framework lifecycle.
   - The JSON an API returns.
   - A database column.
   - A wire format.
   - Another language reading the same bytes.
   - Feature flags.
   - Downstream callers, past the first hop.

   Done when every channel has a `file:line` or a stated reason it does not apply.
4. **Weigh each risk.** Keep a risk when it has a real chance of happening and a real cost if it does. Cite a real `file:line` for each and state the rung it reached on [`evidence.md`](evidence.md). Report the searches that found nothing. Never invent a caller or an API. Done when every kept risk carries a `file:line`, a likelihood, a cost, and a rung, and every cleared risk says why.
5. **Prove the safety fact.** Write a script or test that calls the real code, run it, and paste what happened. That is rung 4. A safety fact below rung 4 is unproven. Mark it unproven only after you attempted the script and can say why it could not run. Done when the proof is pasted, or the fact is marked unproven with the reason.

## Hand back

Write it with [`../technical-writing/SKILL.md`](../technical-writing/SKILL.md). Strip anything private before it goes anywhere public.

- **What it does.** State what changed, including the part that is not obvious.
- **The safety fact.** State the fact, the rung it reached, and the proof. Mark it unproven when you could not prove it.
- **Risks.** List only the real ones, each with how it breaks, the `file:line`, how likely, how bad, and how to verify. Paste the proof for the ones that matter.
- **Cleared.** List what you verified and why it is fine.
- **Before you merge.** Give the cheapest test or reproduction that catches the worst risk you kept, including the script you wrote.

**Reply.** The hand-back above.
