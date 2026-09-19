# Spec format

Use these sections, omitting optional material that adds no decision.

## Problem

Describe the user's current difficulty and the behavior they need.

## Solution and acceptance criteria

Describe the solution from the user's perspective. List observable outcomes,
including important failure cases. Use user stories only when distinct actors
or goals make them useful.

## Implementation decisions

Record module responsibilities, interface changes, invariants, schema changes,
and external contracts already decided. Refer to symbols and locations when
they identify a real constraint. Avoid speculative file-by-file instructions.

If a prototype captures a decision more precisely than prose, include the
relevant type, reducer, schema, or state transition fragment and link its source.

## Testing decisions

Name the behavior under test, the public interfaces that expose it, and relevant
test conventions. Identify required integration or manual checks and any
unavailable environment. Link the shared testing principle instead of copying it.

## Out of scope

State the adjacent work this spec deliberately excludes.

## Open questions

List unresolved requirements, assumptions that need confirmation, and decisions
that block implementation. Keep settled decisions in their sections above.
