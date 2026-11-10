---
name: implement
description: Implement a feature, spec, or set of tickets with design ownership, independent review, and verified slices.
disable-model-invocation: true
---

# Implement

Implement the work described by the user in the spec or tickets. Carry forward designs and decisions already made. For non-trivial UI changes, follow [prototype](../prototype/SKILL.md) before editing real components.

For a substantial behavior-preserving phase, follow [refactoring](../refactoring/SKILL.md), reusing the design and evidence already established. Keep incidental cleanup within this workflow. Apply [TypeScript guidance](../typescript-best-practices/SKILL.md) when writing or reviewing TypeScript.

When executing a multi-phase plan, read [the shared execution-state convention](../orchestrate/STATE.md). A sequential execution owner initializes or resumes the plan's existing store, checkpoints progress, and leaves its next action there before ending a session. At completion, follow the store's close procedure, including the show-me-your-work audit. A delegated worker returns receipts to its coordinator, which owns the shared state updates and final audit.

For a requested pause or transfer to another session, read [handoff](../handoff/SKILL.md). Reuse the execution store when present; standalone implementation needs only a handoff note.

**You own the design. Plan, review, verify.** Delegate implementation.

1. [how](../how/SKILL.md) over the affected subsystem.
2. [architect](../architect/SKILL.md) for parallel design exploration. Skipping stays as `architect skipped: <reason>`. Do not fold the design decision silently into implementation. If the user pushes back on a design's shape because of an unstated constraint, follow [architect's pushback rule](../architect/SKILL.md#phase-c-agree-opt-in).
3. Write the throughput checkpoint as four todo items. A dimension that genuinely does not apply (single file, no fan-out) keeps its item with `n/a: <reason>` rather than being dropped:
   - **Blocking first steps.** Gates run before fan-out.
   - **Independent workstreams.** Disjoint files, services, or layers parallelize. Shared writes serialize.
   - **Shared mutable state.** Default to splitting the target (the [separate-before-serializing-shared-state](../principles/separate-before-serializing-shared-state.md) principle skill). Serialize only for real invariants.
   - **Smallest safe decomposition.** If one worker is best, name why.
4. Delegate code-writing to a subagent using the active environment's available tools and model configuration, with a specific scope (file paths, named data shape and its organizing structure per [model-the-domain](../principles/model-the-domain.md), a state machine over scattered booleans, a table/registry over branching, a typed model over repeated shape assumptions, chosen before the delegate writes logic, and success criteria). Review its diff yourself. When the implementation admits multiple valid shapes, use architect's candidate exploration before assigning the implementation. If delegation is unavailable, own the diff directly and make the separate review pass explicit. Comments follow [no-comments](../no-comments/SKILL.md). Make surgical edits and re-ground against the source for upstream-derived files. Port shared-primitive improvements to all in-scope consumers and verify each.
   Use [tdd](../tdd/SKILL.md) where possible, at the testing boundaries established for the task. Run typechecking and single test files regularly. Leave the covering suites from [test results](../open-pr/test-results.md) for delivery, where they run once.
5. Verify on the matching surface. "Inconclusive" or wrong-surface is not a pass. Flag it.
   A **visual change** alters a web page's layout, styling, or components. Its matching surface is phone and desktop, light and dark, following t3-preview's [viewport and theme checks](../t3-preview/SKILL.md#viewport-and-theme-checks). List the measured width and the theme of each screenshot in the PR. In a site with shipped pages, also screenshot the page's shipped version from a local build of the default branch at the same widths and themes. For a new page, use the closest shipped page. List each difference in the PR. An element the shipped page lacks needs a recorded [prototype](../prototype/SKILL.md) pick. Browser emulation is the wrong surface for engine-specific controls, such as an iOS date input. Check each one in its own engine, for example Safari in the iOS Simulator. When that engine is unavailable, report the control as unverified. If the site deploys PR previews, add the PR's preview URL, once it deploys, to that report and to any decision request about the control, so the user can open it on the device.
6. Rebase into small, ordered commits. Stack follow-ups.
   Use the [sequence-verifiable-units](../principles/sequence-verifiable-units.md) principle skill, building, verifying, and committing each small unit before the next.
7. Commit the work and use [open-pr](../open-pr/SKILL.md) for PR delivery within the task's authorized scope. Merging and deployment are separate actions. When that scope stops short of a PR, such as a request to stop at local edits, do this instead of open-pr:
   1. Run the covering suites picked under [test results](../open-pr/test-results.md#pick-the-suites).
   2. Run [review-pr](../review-pr/SKILL.md) in fix-and-review mode. For explicitly requested adversarial hardening, run [harden-pr](../harden-pr/SKILL.md) instead.

Code-coupled work (one feature, one migration) goes to a single owner with the checkpoint inline. That owner fans out internally after the blocking phase. Parent-level fan-out is for slices that produce independent artifacts (audits, cross-subsystem investigations, competing experiments). Rewrite the checkpoint at phase boundaries. Spawn a fresh owner rather than chaining interrupts.

**Reply:** what you built, what you chose and why, the throughput checkpoint, open decisions. Tables for design alternatives.
