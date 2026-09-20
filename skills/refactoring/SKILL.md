---
name: refactoring
description: Change code structure while preserving observable behavior, with characterization tests and equivalence evidence.
disable-model-invocation: true
---

# Refactoring

**You own the contract. The structure changes. The behavior does not.** Distinct from Feature, which adds behavior, and Bug fix, which corrects it.

If the cleanup reveals a missing feature or a real bug, record it as a follow-up. Changing that behavior needs its own authorized scope under [implement](../implement/SKILL.md) or [bug-fix](../bug-fix/SKILL.md). Preserve the current contract during the refactor. If a defect prevents safe delivery, report the blocker instead of shipping it.

For work spanning several PRs, use [multi-phase-plan](../multi-phase-plan/SKILL.md) when a plan is needed. Keep an existing execution owner and [shared store](../orchestrate/STATE.md). For a pause or transfer, follow [handoff](../handoff/SKILL.md).

1. Establish the behavior contract first. Use [how](../how/SKILL.md) over the affected subsystem, reusing prior grounding. Run existing characterization tests, snapshots, or an equivalence harness against the unchanged code. Add missing coverage before moving structure, following [test behavior](../principles/test-behavior-not-implementation.md). Include relevant outputs, errors and their timing, side effects, and cleanup order. Type check and lint alone do not establish the contract.
2. Name the structure the code is missing per [model the domain](../principles/model-the-domain.md). Keep code whose structure is already clear and local. The reshape must remove complexity, not add indirection.
3. Name the target shape. State the intended module layout, types, and call graph using [foundational thinking](../principles/foundational-thinking.md) and [redesign from first principles](../principles/redesign-from-first-principles.md). For an unsettled design across function boundaries, use [architect](../architect/SKILL.md) before the move. Reuse a settled design. Mechanical renames need no design competition.
4. [Subtract before you add](../principles/subtract-before-you-add.md). Delete dead code, collapse one-caller wrappers, drop redundant validators, and remove orphan references within the agreed scope. Each deletion must preserve the recorded contract. Ship the smallest change that reaches the target shape under [laziness protocol](../principles/laziness-protocol.md). Revert speculative cleanup that does not help.
5. Move in small steps, keeping characterization checks passing. For internal APIs whose callers are all in scope, [migrate callers and delete the old API](../principles/migrate-callers-then-delete-legacy-apis.md) in the same wave. Preserve compatibility required by external consumers or a staged migration. Spot-check renames in code, strings, prose, and back-references.

   Delegate mechanical edits when available with specific paths, names, and behavior to preserve. Review the diff yourself. When delegation is unavailable, make a separate review pass.

   Apply [no-comments](../no-comments/SKILL.md) to changed code and [TypeScript guidance](../typescript-best-practices/SKILL.md) when relevant.
6. [Prove behavior is unchanged](../principles/prove-it-works.md) on the real artifact. For larger reshapes, run an equivalence check over old and new outputs or replay a recorded baseline. Include the observable effects named in step 1. Use the project's verification skill or available tools on the interface the change affects. Inspect the evidence yourself. Missing or inconclusive verification is not a pass.
7. Confirm the change is worth keeping. The success measure is [reduced reader load](../principles/minimize-reader-load.md). If the diff does not lower reader load somewhere, revert only this attempt's changes, preserving unrelated work.
8. Keep small ordered commits under [sequence verifiable units](../principles/sequence-verifiable-units.md), with each behavior-preserving slice green before the next. Preserve commits already under review. For standalone work, use [review-pr](../review-pr/SKILL.md), then [open-pr](../open-pr/SKILL.md) within the authorized delivery scope. Honor local-only requests. A delegated unit returns evidence to its execution owner, which handles review and delivery once. Hardening, merging, and deployment require their own authorization.

**Reply:** the structure that changed, the preserved behavior, the equivalence evidence, what became easier to understand, and what shipped or was reverted.
