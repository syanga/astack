---
name: principles
description: Engineering principles index.
disable-model-invocation: true
---

# Principles

One file per principle. Each opens with when it applies. Read a file in full before citing it, and name the decision it changed; a citation with no decision behind it is a name drop.

Other skills reach these files by relative path, for example `../principles/laziness-protocol.md` from the review rubric.

**Core**

- [`laziness-protocol.md`](laziness-protocol.md). Refactoring, sizing a diff, or tempted to add an abstraction or layer. Bias to deletion and the smallest change that solves the problem.
- [`subtract-before-you-add.md`](subtract-before-you-add.md). Sequencing an addition, refactor, or rewrite. Remove dead weight first, then build on the simpler base.
- [`minimize-reader-load.md`](minimize-reader-load.md). Reviewing or shaping code that is hard to trace. Count layers and hidden state.
- [`redesign-from-first-principles.md`](redesign-from-first-principles.md). Integrating a new requirement into an existing design. Design as if it had been there from day one.
- [`build-the-lever.md`](build-the-lever.md). Any non-trivial work. Build the script that does or proves it, so a reviewer can rerun it.
- [`encode-lessons-in-structure.md`](encode-lessons-in-structure.md). Writing the same instruction a second time. Make it a check instead.

**Architecture**

- [`model-the-domain.md`](model-the-domain.md). Stateful logic, or code that branches a lot or repeats a shape assumption across files.
- [`boundary-discipline.md`](boundary-discipline.md). Validation, error handling, framework adapters. Guards at the boundary, trust inside.
- [`type-system-discipline.md`](type-system-discipline.md). Designing types or a signature in a typed language. Make illegal states unrepresentable.
- [`make-operations-idempotent.md`](make-operations-idempotent.md). Commands and loops that run amid crashes and retries.
- [`migrate-callers-then-delete-legacy-apis.md`](migrate-callers-then-delete-legacy-apis.md). A new internal API while old callers still exist. Migrate and delete in one wave.
- [`separate-before-serializing-shared-state.md`](separate-before-serializing-shared-state.md). Concurrent actors that might write the same file, branch, or key.

**Verification**

- [`prove-it-works.md`](prove-it-works.md). Before declaring done. Verify the real artifact, not a proxy.
- [`fix-root-causes.md`](fix-root-causes.md). Debugging. Reproduce, then trace to the cause before changing code.
- [`sequence-verifiable-units.md`](sequence-verifiable-units.md). Multi-step work and how commits and PRs are stacked. Each unit ends in a check.
- [`test-behavior-not-implementation.md`](test-behavior-not-implementation.md). Writing, changing, or keeping a test. Assert what a user observes.
