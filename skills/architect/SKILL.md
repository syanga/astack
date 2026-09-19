---
name: architect
description: Sketch caller usage, types, and module boundaries before implementing a non-trivial change.
disable-model-invocation: true
---

# Architect

Design the shape before filling in the code. A design-only request ends with the
sketch and recommendation. When implementation is authorized, continue through
[implement](../implement/SKILL.md) unless the user requested a checkpoint.

## Ground the problem

Trace the affected behavior through the current system using
[how](../how/SKILL.md). Read the project's domain glossary and relevant ADRs when
available. If ownership or layering will change, use [why](../why/SKILL.md) to
recover the constraints behind the existing shape. Greenfield work still needs
its external contracts and integration constraints identified.

Grounding is complete when you can name the callers, data flow, ownership,
invariants, and behavior the change must preserve.

## Sketch alternatives

Read [Deep Modules](../principles/deep-modules.md),
[Model the Domain](../principles/model-the-domain.md), and
[Exhaust the Design Space](../principles/exhaust-the-design-space.md).
Produce at least two structurally different candidates. For independent design
exploration when subagents are available, use the briefs and comparison process
in [Design It Twice](../improve-codebase-architecture/DESIGN-IT-TWICE.md).
Otherwise develop and compare the candidates directly.

Write the caller's usage first, including realistic calls and results. Derive
the core types, function signatures, and module boundaries from that usage.
Trace dominant access patterns through the data structures. Keep executable
stubs in scratch files until implementation begins.

Read [Type System Discipline](../principles/type-system-discipline.md) for type
choices and [Boundary Discipline](../principles/boundary-discipline.md) for
validation and effect boundaries. For concurrent writers, read
[Separate Before Serializing Shared State](../principles/separate-before-serializing-shared-state.md).
For retries or partial failure, read
[Make Operations Idempotent](../principles/make-operations-idempotent.md).

Screen candidates for shallow modules using Deep Modules' deletion test. Also
check for these design problems:

- A representation or policy leaks into several modules that must change together.
- Modules follow execution stages while duplicating the same domain knowledge.
- Callers must coordinate internal stages or understand storage and transport details.
- Forwarding methods add a layer without policy, adaptation, or a distinct abstraction.

Compare interface depth, locality, seam placement, and migration cost. Choose a
base and explain which parts of other candidates improve it. Use the
[rationale format](RATIONALE.md) to record the decision alongside the sketch.

## Implement and revise

Pass the chosen sketch, testing boundaries, and constraints to
[implement](../implement/SKILL.md). For non-trivial UI decisions, use
[prototype](../prototype/SKILL.md) and obtain a selection before editing real
components.

Treat repeated deviations as evidence about the design. The same workaround
across callers, recurring special cases, type escape hatches, or unexpected
shared state can mean the shape is wrong. Distinguish these patterns from
isolated edge cases.

When a pattern appears, read
[Redesign from First Principles](../principles/redesign-from-first-principles.md)
and [Subtract Before You Add](../principles/subtract-before-you-add.md).
Re-ground with the new constraints and replace the affected sketch before
adding more implementation. Preserve unrelated user work.
