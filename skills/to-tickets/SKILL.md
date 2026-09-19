---
name: to-tickets
description: Break a spec, plan, or conversation into verifiable implementation tickets with explicit dependencies.
disable-model-invocation: true
---

# To tickets

Read the source spec or conversation and any referenced issue discussion.
Inspect unfamiliar code and use the project's domain vocabulary and ADRs.
Separate unresolved design decisions from implementation work. A ticket blocked
on a decision remains blocked even when it has no code dependency.

## Draft the slices

Read [Sequence Verifiable Units](../principles/sequence-verifiable-units.md).
Each ticket delivers a narrow but complete behavior through the necessary
layers and includes its verification. Size it for one focused agent session.
State only dependencies that genuinely prevent the ticket from starting.

Put necessary preparatory refactoring first, with its own passing checks.
Avoid splitting a feature into separate schema, backend, frontend, and test
tickets when none delivers verifiable behavior alone.

For a wide mechanical refactor, read
[Migrate Callers Then Delete Legacy APIs](../principles/migrate-callers-then-delete-legacy-apis.md).
Use a coordinated migration when all callers can change together. If external
compatibility or independent delivery requires expand-contract, plan the new
form, migration batches, and removal as explicit dependent tickets. Keep the
temporary compatibility path bounded by the removal ticket. If no batch can
pass independently, keep the coupled work in one integration unit and identify
its final verification requirement.

## Check the breakdown

Present titles, delivered behavior, and blockers in dependency order. Check that
every acceptance criterion has an owner, every blocker resolves to a ticket or
named external decision, and the dependency graph has no cycles.

Use prior agreement on scope and granularity. Ask about unresolved product or
sequencing choices when they affect the breakdown. Complete the drafts before
seeking any required publication approval.

## Deliver

Use the destination requested by the user or established by the project.
For local tickets, write one Markdown file per ticket in the chosen task
directory, numbered in dependency order. When no destination is established,
return the draft breakdown in the conversation.

Each ticket contains:

- A parent spec reference, if one exists.
- The behavior to build and its acceptance criteria.
- Verification that establishes those criteria.
- The tickets or decisions blocking it, or an explicit statement that none do.

Publish externally only when authorized. Create blockers first so later tickets
can reference real identifiers. Use native dependency links when available,
otherwise record blockers in the body. Use the project's actual labels and
leave the parent issue's state unchanged unless updating it was requested.

Return the ticket links and identify which tickets can start. The deliverable
is the breakdown; begin implementation only when it is also requested.
