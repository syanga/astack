# Review rubric

Lenses for a code reviewer. Apply every lens below. Where a principle sets a lens's standard, the lens names it, and the principle's text follows the lens in the prompt you received. Each principle is written for the author of a change. Read it as the standard the change is judged against, not as work for you to do. A coding standard the repository documents in `CONTRIBUTING.md`, `CODING_STANDARDS.md`, or `AGENTS.md` overrides a principle where they conflict. Skip anything a linter or formatter already enforces.

## Correctness

Does the code do what the intent says?

- Does the happy path work? Does the sad path?
- Does the code handle empty input, null, boundary values, and concurrent access?
- Does the code catch, propagate, or swallow errors?
- Is there an off-by-one, a type coercion, an overflow, or an encoding mismatch?
- Can state go wrong through a race, a stale closure, or a dangling reference?
- Does the operation converge when it runs twice, or after a crash halfway through? Principle: [`make-operations-idempotent.md`](../principles/make-operations-idempotent.md).
- When two actors touch the same mutable state, are they separated, serialized structurally, or only held apart by convention? Convention is not concurrency control. Principle: [`separate-before-serializing-shared-state.md`](../principles/separate-before-serializing-shared-state.md).

For a suspected bug, trace the path and show the call chain that produces the bad value.

## Root cause or symptom

Is the change fixing the problem or covering it? Read beyond the diff: callers, callees, types, sibling modules. Follow the call chain before judging the layer. Principle: [`fix-root-causes.md`](../principles/fix-root-causes.md).

- Does a guard hide an invariant violation, a retry hide a broken contract, or a cast hide a modelling error?
- Does a fix in module A belong in module B's contract?
- Is a rule written as a comment or convention where a type, lint, or runtime check could make the wrong thing impossible? Principle: [`encode-lessons-in-structure.md`](../principles/encode-lessons-in-structure.md).

## Structure

Does the change fit the system it lands in?

- Does the code validate once at the boundary and trust the value inside? Principle: [`boundary-discipline.md`](../principles/boundary-discipline.md).
- Does the data shape match the access pattern? Principle: [`model-the-domain.md`](../principles/model-the-domain.md).
- Can an illegal state be represented, can two primitives be swapped, can a match miss a variant, does a cast lie? Principle: [`type-system-discipline.md`](../principles/type-system-discipline.md).
- Would the code look like this if the requirement had been known from the start? Principle: [`redesign-from-first-principles.md`](../principles/redesign-from-first-principles.md).
- Does a new API sit beside the old one with no external consumer? Principle: [`migrate-callers-then-delete-legacy-apis.md`](../principles/migrate-callers-then-delete-legacy-apis.md).
- Does one function mix orchestration with low-level detail?
- Does the change add a dependency that makes the next change harder?
- Does logic sit in its canonical layer, or does feature logic leak into a shared path, a bespoke helper sit beside an existing one, or code land in the wrong package?
- Is independent work serialized for no reason? Can related updates leave state half-applied?

## Verification

Can you tell it works from reading it?

- Do the tests assert a literal observed value, rather than the calls made, a restated constant, or a value the test computed? Principle: [`test-behavior-not-implementation.md`](../principles/test-behavior-not-implementation.md).
- Would an assertion or invariant in the code catch a regression?
- Does a bug fix carry the test for the bug? Does an integration change test the full path?
- Does the code check the real thing, rather than a proxy such as an mtime or a cached value? Principle: [`prove-it-works.md`](../principles/prove-it-works.md).

## Complexity

Is the complexity paid for by what the code does? Look for the restructuring that makes a branch, helper, mode, or layer disappear while behaviour stays the same, and say so when one exists. Working code is not the bar. A change that keeps incidental complexity a restructuring would delete is a finding.

- Is there an abstraction with one call site, a parameter for a case that does not exist, dead code, a vestigial parameter, or a compatibility path whose migration is done? Principles: [`laziness-protocol.md`](../principles/laziness-protocol.md), [`subtract-before-you-add.md`](../principles/subtract-before-you-add.md).
- How many layers must a reader trace and how much state must a reader hold? A pass-through wrapper, a one-caller helper, or mutable scope wider than needed adds to both. Principle: [`minimize-reader-load.md`](../principles/minimize-reader-load.md).
- Does a generic or magical mechanism hide a simple data-shape assumption?
- Was an ad hoc conditional added to an unrelated flow? A scattered special case is a design problem, not a style nit, and the remedy is a helper, a state machine, or a module.
- Did a file grow from under a thousand lines to over? Decompose first. A waiver needs a structural reason.
- Does everything this change ships earn its place? A feature, control, or option nobody asked for costs more than it returns, and a half-finished feature costs more than a missing one.

## Security

Flag only what you can trace through the code. "This could be an injection point" without the input path is not a finding.

- Does user input reach SQL, a shell, eval, or HTML without sanitising?
- Is a new endpoint missing authentication or authorization?
- Are there secrets in code, logs, or error messages?
- Is there a check-then-use race on a security-relevant path?
- Is model output treated as trusted, fed to a tool, a query, or a write without validation?
