---
name: blast-radius
description: Find what a change breaks beyond its diff.
disable-model-invocation: true
---

# Blast radius

Find what a change breaks somewhere else before it ships, and prove the one fact its safety rests on by running code. Listing callers is not the job; grep does that in a second. The job is the breakage grep will not show.

A write-up that sounds right is worth nothing, because it reads as convincing whether or not it is true. Find the one or two facts the whole thing depends on and push each down the ladder in [`evidence.md`](evidence.md). Say where each stopped.

## Steps

1. **Read the change.** The diff, the symbols it adds, changes, and deletes, and what it now does differently, including the part the diff does not spell out. For a PR, `gh pr view <n> --json title,body,commits` and `gh pr diff <n>`.
2. **Find the one fact it is safe because of.** Most risky-looking changes are safe because of a single fact, such as "this call only drops cache entries that are already dead". Name it. If it holds, most of the risky cases clear at once. Spend the time here, not on a long list of maybes.
3. **Look where grep stops.** Read the source of the library you call at its pinned version, and any local patch. Work out when things run: microtasks, unmount and teardown, framework lifecycle. Follow what a symbol search misses: the JSON an API returns, a database column, a wire format, another language reading the same bytes, a feature flag, code three hops downstream.
4. **Weigh each risk honestly.** A real chance of happening and a real cost if it does. Keep the risks you confirmed. List the ones you checked and cleared separately. Cite a real `file:line`. A search that finds nothing is still an answer. Never invent a caller or an API.
5. **Prove the one fact.** Write a script or test that calls the real code, run it, and paste what happened. If it cannot be proven cheaply, mark it unproven rather than overstating.

## Hand back

- **What it does.** What changed, including the part that is not obvious.
- **The one fact it is safe because of.** The fact, the rung it reached, the proof. Unproven when you could not prove it.
- **Risks.** Only the real ones, each with how it breaks, the `file:line`, how likely, how bad, and how to check.
- **Cleared.** What you checked and why it is fine.
- **Before you merge.** The cheapest test or repro that catches the real bug, including the script you wrote.

Cite real code and strip anything private before the write-up goes anywhere public.
