---
name: principles
description: Engineering principles index.
disable-model-invocation: true
---

# Principles

Before applying a principle, read its file in full. In the reply, name the decision the principle changed.

## Shaping a change

- [`laziness-protocol.md`](laziness-protocol.md). Refactoring, sizing a diff, or adding a layer.
- [`foundational-thinking.md`](foundational-thinking.md). Choosing core types and data structures, or sequencing setup before feature work.
- [`subtract-before-you-add.md`](subtract-before-you-add.md). Sequencing an addition or a refactor.
- [`redesign-from-first-principles.md`](redesign-from-first-principles.md). Integrating a new requirement into an existing design.
- [`attack-the-premise.md`](attack-the-premise.md). Repeated fixes for an imbalance across actors share one premise and have failed the same check.
- [`outcome-oriented-execution.md`](outcome-oriented-execution.md). Planned rewrites and migrations with explicit phase boundaries.
- [`experience-first.md`](experience-first.md). Product, UX, or feature-scope tradeoffs.
- [`exhaust-the-design-space.md`](exhaust-the-design-space.md). Novel interactions or architectural choices with no established precedent.
- [`minimize-reader-load.md`](minimize-reader-load.md). Reviewing code that is hard to trace, or adding a layer or a piece of state.
- [`build-the-lever.md`](build-the-lever.md). Work beyond a couple of obvious edits.
- [`encode-lessons-in-structure.md`](encode-lessons-in-structure.md). Writing the same instruction twice, or handling a correction from the user or a failed test.

## Designing structure

- [`model-the-domain.md`](model-the-domain.md). Stateful logic, or code that branches a lot or repeats a shape assumption across files.
- [`boundary-discipline.md`](boundary-discipline.md). Validation, error handling, framework adapters.
- [`type-system-discipline.md`](type-system-discipline.md). Designing a type or a signature in a typed language.
- [`make-operations-idempotent.md`](make-operations-idempotent.md). Commands and loops that must survive crashes and retries.
- [`migrate-callers-then-delete-legacy-apis.md`](migrate-callers-then-delete-legacy-apis.md). A new internal API while old callers still exist.
- [`separate-before-serializing-shared-state.md`](separate-before-serializing-shared-state.md). Concurrent actors that might write the same file, branch, or key.

## Verifying the work

- [`prove-it-works.md`](prove-it-works.md). Before you call the work done.
- [`fix-root-causes.md`](fix-root-causes.md). Debugging.
- [`sequence-verifiable-units.md`](sequence-verifiable-units.md). Multi-step work, and stacking commits and PRs.
- [`test-behavior-not-implementation.md`](test-behavior-not-implementation.md). Writing, changing, or keeping a test.

## Managing the work

- [`guard-the-context-window.md`](guard-the-context-window.md). Large outputs, long files, repeated reads, or plans for parallel work.
- [`never-block-on-the-human.md`](never-block-on-the-human.md). Deciding whether to ask for permission during an authorized task.
