# Review rubric

Lenses for a code reviewer. Apply the ones the change touches; a simple bug fix does not need a paragraph on architecture. Each lens names the principle that defines its standard. Read the principle file before applying the lens. The file is the rule; the lens is how its violation looks in a diff.

## Correctness

Does the code do what the intent says?

- Edge cases: empty input, null, boundary values, concurrent access.
- Errors caught, propagated, or silently swallowed.
- Off-by-one, type coercion, overflow, encoding.
- State: races, stale closures, dangling references.
- Run twice, or after a crash halfway through, does it converge? [`make-operations-idempotent.md`](../principles/make-operations-idempotent.md).
- Two actors touching the same mutable state: separated, serialized structurally, or held apart by convention? [`separate-before-serializing-shared-state.md`](../principles/separate-before-serializing-shared-state.md).

For a suspected bug, trace the path. Show the call chain that produces the bad value rather than noting that it could.

## Root cause or symptom

Is the change fixing the problem or covering it? Answering this means reading beyond the diff: callers, callees, types, sibling modules. Follow the call chain before judging the layer. [`fix-root-causes.md`](../principles/fix-root-causes.md).

- A guard that hides an invariant violation. A retry that hides a broken contract. A cast that hides a modelling error.
- A fix in module A that belongs in module B's contract.
- A rule written as a comment or convention where a type, lint, or runtime check could make the wrong thing impossible. [`encode-lessons-in-structure.md`](../principles/encode-lessons-in-structure.md).

## Structure

Does the change fit the system it lands in?

- Validation once at the boundary, trusted inside. [`boundary-discipline.md`](../principles/boundary-discipline.md).
- Data shape matches the access pattern. A new branch on an if-chain or a second boolean kept in sync is the tell. [`model-the-domain.md`](../principles/model-the-domain.md).
- Illegal states representable, primitives interchangeable, matches not exhaustive, casts that lie. [`type-system-discipline.md`](../principles/type-system-discipline.md).
- Bolted on or integrated: would the code look like this if the requirement had been known from the start? [`redesign-from-first-principles.md`](../principles/redesign-from-first-principles.md).
- A new API beside the old one with no external consumer. [`migrate-callers-then-delete-legacy-apis.md`](../principles/migrate-callers-then-delete-legacy-apis.md).

Simple code without abstraction is fine. Premature abstraction costs more than duplication.

## Verification

Can you tell it works from reading it?

- Tests present, and testing behaviour rather than the calls made. [`test-behavior-not-implementation.md`](../principles/test-behavior-not-implementation.md).
- A bug fix carries the test for the bug. An integration change tests the full path.
- The code checks the real thing, not a proxy such as an mtime or a cached value. [`prove-it-works.md`](../principles/prove-it-works.md).

## Complexity

Is the complexity paid for by what the code does?

- Abstractions with one call site, parameters for cases that do not exist, dead code, vestigial parameters, compatibility paths whose migration is done. [`laziness-protocol.md`](../principles/laziness-protocol.md), [`subtract-before-you-add.md`](../principles/subtract-before-you-add.md).
- Layers a reader must trace and state a reader must hold: pass-through wrappers, one-caller helpers, mutable scope wider than needed. [`minimize-reader-load.md`](../principles/minimize-reader-load.md).
- A file pushed past about a thousand lines, ad hoc conditionals added to an unrelated flow, feature checks scattered through shared code.

Look for the restructuring that makes a branch, helper, or layer disappear while behaviour stays the same, and say so when one exists. Simpler is better unless simpler is wrong.

## Security

Flag only what you can trace through the code. "This could be an injection point" without the input path is not a finding.

- User input reaching SQL, a shell, eval, or HTML without sanitising.
- Missing authentication or authorization on a new endpoint.
- Secrets in code, logs, or error messages.
- Check-then-use races on security-relevant paths.
- Model output treated as trusted: fed to a tool, a query, or a write without validation.
