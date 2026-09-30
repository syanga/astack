---
name: arena
description: Compare independent solutions to the same task, select a base, and combine the strongest ideas into a verified result.
disable-model-invocation: true
---

# Arena

Fan out N parallel attempts at the same task. Read every candidate end to end. Pick the strongest as the base. Graft the best ideas from the others into it. Verify the synthesized result.

When called by another workflow, return the synthesized artifact and evidence to it. The caller owns implementation and delivery beyond the requested artifact. A design request produces sketches, not production edits.

## Start

Open a todolist with one entry per phase before launching anything.

1. Frame
2. Fan out
3. Cross-judge
4. Pick
5. Graft
6. Verify

## Phase A: Frame

The N candidates will receive the same prompt, so the prompt is the contract.

1. State the artifact each candidate is producing.
2. Derive the rubric. State what success looks like for *this* task, then turn it into 3-6 concrete gradeable criteria. The rubric is the picker's tool in Phase D. Candidates see the task, constraints, and acceptance requirements. Keep the scoring rubric for the comparison.
3. Pick the runners and a time or attempt budget. Start with one fresh Codex candidate and one fresh Claude candidate when available and permitted. Follow [provider execution](PROVIDERS.md). If a provider is unavailable, use separate native contexts and disclose the reduced diversity. Same-model attempts are valid, but are not cross-provider coverage. Honor the caller's candidate count and design criteria.
4. Pin the source revision and give every candidate the same grounding snapshot, including relevant uncommitted work. Assign separate output paths. Use isolated worktrees for code candidates, or a temporary per-candidate directory for sketches, per [separate-before-serializing-shared-state](../principles/separate-before-serializing-shared-state.md). Read-only runners return text for the parent to save.

## Phase B: Fan out

Dispatch candidates with the available agent tools or provider CLIs, within the concurrency limit. Queue remaining candidates when necessary. Give each the task, shared grounding, its own output path, and instructions to produce both the artifact and a short rationale. Keep candidates independent until their outputs are complete.

Each rationale names the alternatives the candidate considered and what it rejected.

If a candidate fails to produce output, note the dropout and replace it within the budget. Comparison needs at least two usable candidates. If that is impossible, return the available work as incomplete comparison. Without delegation, develop distinct alternatives directly and disclose that they were not independently produced.

## Phase C: Cross-judge

After all candidates have finished or been reconciled as failed, run one fresh read-only judge using [provider execution](PROVIDERS.md). Prefer a different provider from the parent when available. The judge sees the rubric and candidate artifacts under neutral labels, without provider identities or the parent's preference. It scores each criterion and recommends a base with reasons.

The judge runs alongside the parent's reading in Phase D, after candidate outputs are frozen. If independent judging is unavailable, perform the rubric comparison directly and report that limitation.

## Phase D: Pick a base

Read every candidate end to end before picking.

Score each candidate against the rubric criterion by criterion, not on holistic feel. Compare against the cross-judge. Agreement supports the pick but does not establish correctness. For disagreement, inspect the evidence, tradeoffs, and rubric before deciding.

Pick the base on which candidate a future maintainer can extend most easily without breaking invariants. Prefer the cleaner boundary or smaller API when two feel tied, per [Laziness Protocol](../principles/laziness-protocol.md).

Record the pick and the reason in a short synthesis note alongside the base artifact, including the cross-judge's verdict.

## Phase E: Graft

Walk each losing candidate once more and identify what is worth porting into the base. The signal is usually one or two things per candidate, not most of it.

Fold each graft in by hand, per [redesign-from-first-principles](../principles/redesign-from-first-principles.md). Don't paste mechanically. The result has to remain coherent under one mental model.

Record what was grafted, from which candidate, and what was rejected and why.

When candidates converge on the same shape, record the agreement and verify that shape. No graft is required. Divergence can expose valid alternatives or missing constraints. Compare the tradeoffs before reframing. Re-run only when unresolved requirements prevent a sound choice, within the remaining budget.

## Phase F: Verify

The synthesized artifact has to hold up under the same scrutiny as any other output, per [prove-it-works](../principles/prove-it-works.md).

Verify the combined artifact, even when its source candidates passed. For a defect, revisit the relevant requirement, candidate, or graft and repeat affected checks. If the budget ends first, return the unresolved failure. For UI choices, follow [prototype](../prototype/SKILL.md) and preserve the user's selection before production implementation.

## Outputs

One synthesized artifact. One short synthesis note alongside, naming the base, the source of each graft, rejections, provider, model, and reasoning effort when known, dropouts, coverage limits, and verification results. Reuse the caller's rationale or task record. For an existing decision trail, reference the synthesis through [show-me-your-work](../show-me-your-work/SKILL.md).

Before ending, reconcile every runner to completed, stopped, or still owned with an explicit handoff. For session transfer, follow [handoff](../handoff/SKILL.md), preserving candidate paths, the source snapshot, pending judgment, and workspace cleanup information.
