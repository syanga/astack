# Simplification Specialist Review Checklist

This lens hunts unrequested structure: abstractions with one implementation, hand-rolled stdlib, dependencies duplicating platform features, and dead flexibility. Coverage gaps belong to the core checklist. Never flag a test, error path, or edge-case branch for deletion.

## Categories

Use one of these five tags as the finding's `category`.

- `delete`: Dead code, unused flags or configuration, and unused flexibility. Nothing replaces the removed code.
- `stdlib`: Hand-written code or dependencies that duplicate the standard library. Name the replacement function, including built-ins that replace manual loops.
- `native`: Code or dependencies that duplicate platform features. Name the feature, such as `<input type="date">` instead of a date picker library, CSS instead of JavaScript, or a database constraint instead of application code.
- `speculative`: Single-implementation interfaces, factories with one product, wrappers that only delegate, configuration nobody sets, or a layer with one caller. Inspect single-export files for unnecessary separation.
- `shrink`: The same logic in fewer lines. Show the shorter form and report only reductions of at least five lines. Apply the same minimum to manual-loop replacements.

## Examples

- `native`: Replace `moment.js` used for one date format with `Intl.DateTimeFormat`.
- `speculative`: Inline an `AbstractRepository` that has one implementation.
- `delete`: Remove a retry wrapper around an idempotent local call when the retry serves no purpose.

## Suppressions — DO NOT flag these (inherited from the main checklist, binding here)

- "X is redundant with Y" when the redundancy is harmless and aids readability
- Consistency-only changes (wrapping a value in a conditional to match how another constant is guarded)
- Tests, error paths, edge-case branches, input validation, security measures, accessibility — NEVER deletion targets; coverage is the Completeness Gaps category's job
- A single smoke test or assert-based self-check — that is the completeness minimum, not bloat
- Deliberately accepted debt supported by a linked issue or decision record; verify the reference and whether the decision still applies
- ANYTHING already addressed in the diff you're reviewing — read the FULL diff before commenting
