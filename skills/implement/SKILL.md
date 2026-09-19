---
name: implement
description: Implement a feature, spec, or set of tickets in verifiable slices.
disable-model-invocation: true
---

# Implement

Build the work described by the user, spec, or tickets. Resolve conflicts in
favor of the user's current requirements. Carry forward designs and decisions
already made instead of reopening them by default.

## Establish the contract

Read the source request and relevant project instructions, domain glossary, and
ADRs. Identify the observable acceptance criteria, scope, dependencies, and
verification commands. Trace unfamiliar affected behavior with
[how](../how/SKILL.md).

If the module shape remains uncertain, use [architect](../architect/SKILL.md).
Return here with the chosen design. For non-trivial UI, layout, or copy changes,
follow [prototype](../prototype/SKILL.md) before editing real components. Honor
an existing selection.

## Sequence the work

Read [Sequence Verifiable Units](../principles/sequence-verifiable-units.md).
Choose the smallest slice that demonstrates useful behavior through the
affected layers. Put prerequisite decisions and setup before dependent work.
For work spanning several tickets, use the dependencies already recorded or
read [to-tickets](../to-tickets/SKILL.md) to draft a breakdown.

For substantial work, identify independent workstreams and shared writes.
When delegation is useful and available, give each worker a disjoint scope,
the chosen data model, and acceptance criteria. Keep coupled changes under one
owner. Inspect delegated diffs and verify their artifacts yourself. Work
directly when dividing the task would add coordination without useful separation.

## Build one slice

Use [tdd](../tdd/SKILL.md) for requested test-first work and behavior changes with
a practical test path. It owns test selection and the failing-before evidence.
Use the closest useful executable check when a new test would provide weak
signal or require disproportionate setup.

Run focused tests and relevant type checks as each slice changes. Finish the
current slice with its checks passing before building on it. Follow
[no-comments](../no-comments/SKILL.md) when adding or changing code comments.
If implementation repeatedly contradicts the sketch, return to architect's
revision step with the evidence.

## Verify and deliver

Read [Prove It Works](../principles/prove-it-works.md). Exercise the changed
behavior on the matching surface, including the complete communication path
for integrations. Run the repository's required final checks and inspect the
diff against the acceptance criteria. Distinguish blocked or inconclusive
verification from a pass.

Use [review-pr](../review-pr/SKILL.md) when the user requests review or the
change warrants an independent review. Use [open-pr](../open-pr/SKILL.md) for
authorized PR delivery. Preserve a request to stop at local changes.

Report the behavior delivered, material design choices, verification evidence,
and unresolved acceptance criteria. Commit and publish according to the task's
delivery scope. Merging and deployment require their own authorization.
