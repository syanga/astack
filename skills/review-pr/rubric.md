# Review rubric

Lenses for a code reviewer. Apply the ones the change touches; a simple bug fix needs no paragraph on architecture. Each lens names the principle that sets its standard. The principle's text follows the lens in the prompt you received, and the lens says how a violation looks in a diff. A coding standard the repository documents (`CONTRIBUTING.md`, `CODING_STANDARDS.md`, `AGENTS.md`) overrides a principle where they conflict. Skip anything a linter or formatter already enforces.

Order your findings: structural regressions and missed simplifications first, then tangled branching, then boundary, type, and file-size concerns, then smaller legibility issues. Prefer a few high-conviction findings to a long list of nits.

## Correctness

Does the code do what the intent says?

- Does the happy path work? Does the sad path?
- Edge cases: empty input, null, boundary values, concurrent access.
- Does the code catch, propagate, or swallow errors?
- Off-by-one, type coercion, overflow, encoding.
- State: races, stale closures, dangling references.
- Run twice, or after a crash halfway through, does it converge? Principle: [`make-operations-idempotent.md`](../principles/make-operations-idempotent.md).
- When two actors touch the same mutable state, are they separated, serialized structurally, or only held apart by convention? Convention is not concurrency control. Principle: [`separate-before-serializing-shared-state.md`](../principles/separate-before-serializing-shared-state.md).

For a suspected bug, trace the path and show the call chain that produces the bad value.

## Root cause or symptom

Is the change fixing the problem or covering it? Read beyond the diff: callers, callees, types, sibling modules. Follow the call chain before judging the layer. Principle: [`fix-root-causes.md`](../principles/fix-root-causes.md).

- A guard that hides an invariant violation. A retry that hides a broken contract. A cast that hides a modelling error.
- A fix in module A that belongs in module B's contract.
- A rule written as a comment or convention where a type, lint, or runtime check could make the wrong thing impossible. Principle: [`encode-lessons-in-structure.md`](../principles/encode-lessons-in-structure.md).

## Structure

Does the change fit the system it lands in?

- Does the code validate once at the boundary and trust the value inside? Principle: [`boundary-discipline.md`](../principles/boundary-discipline.md).
- Does the data shape match the access pattern? A new branch on an if-chain, or a second boolean kept in sync with the first, is the tell. Principle: [`model-the-domain.md`](../principles/model-the-domain.md).
- Can an illegal state be represented, can two primitives be swapped, can a match miss a variant, does a cast lie? Principle: [`type-system-discipline.md`](../principles/type-system-discipline.md).
- Would the code look like this if the requirement had been known from the start? Principle: [`redesign-from-first-principles.md`](../principles/redesign-from-first-principles.md).
- Does a new API sit beside the old one with no external consumer? Principle: [`migrate-callers-then-delete-legacy-apis.md`](../principles/migrate-callers-then-delete-legacy-apis.md).
- Does one function mix orchestration with low-level detail?
- Does the change add a dependency that makes the next change harder?
- Does logic sit in its canonical layer? Flag feature logic leaking into a shared path, a bespoke helper beside an existing canonical one, and code in the wrong package or module.
- Is independent work serialized for no reason? Can related updates leave state half-applied?

Simple code without abstraction is fine. Premature abstraction costs more than duplication.

## Verification

Can you tell it works from reading it?

- Do the tests assert a literal observed value, rather than the calls made, a restated constant, or a value the test computed? Principle: [`test-behavior-not-implementation.md`](../principles/test-behavior-not-implementation.md).
- Would an assertion or invariant in the code catch a regression?
- A bug fix carries the test for the bug. An integration change tests the full path.
- Does the code check the real thing, rather than a proxy such as an mtime or a cached value? Principle: [`prove-it-works.md`](../principles/prove-it-works.md).

## Complexity

Is the complexity paid for by what the code does? Be ambitious here. Look for the restructuring that makes a branch, helper, mode, or layer disappear while behaviour stays the same, and say so when one exists. Working code is not the bar. A change that keeps incidental complexity a restructuring would delete is a finding.

- An abstraction with one call site, a parameter for a case that does not exist, dead code, a vestigial parameter, a compatibility path whose migration is done. Principles: [`laziness-protocol.md`](../principles/laziness-protocol.md), [`subtract-before-you-add.md`](../principles/subtract-before-you-add.md).
- Layers a reader must trace and state a reader must hold: a pass-through wrapper, a one-caller helper, mutable scope wider than needed. Principle: [`minimize-reader-load.md`](../principles/minimize-reader-load.md).
- A generic or magical mechanism that hides a simple data-shape assumption. Prefer direct, boring code.
- An ad hoc conditional added to an unrelated flow. A scattered special case is a design problem, not a style nit. The remedy is a helper, a state machine, or a module.
- A file pushed from under a thousand lines to over. Decompose first; a waiver needs a structural reason.
- Does the shipped surface earn its place? A feature, control, or option nobody asked for costs more than it returns, and a half-finished feature costs more than a missing one.

## Security

Flag only what you can trace through the code. "This could be an injection point" without the input path is not a finding.

- User input reaching SQL, a shell, eval, or HTML without sanitising.
- Missing authentication or authorization on a new endpoint.
- Secrets in code, logs, or error messages.
- Check-then-use races on security-relevant paths.
- Model output treated as trusted: fed to a tool, a query, or a write without validation.
