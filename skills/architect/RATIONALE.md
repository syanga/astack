# Design rationale

Keep the rationale beside the sketch. Use these sections where they carry a
decision, with at least one concrete alternative.

## Problem

State the desired behavior and the existing contracts that make the shape
non-obvious.

## Caller usage

Show realistic imports, calls, and results. Write this before the types. Resolve
disagreements between the two in favor of the intended caller experience.

## Shape

Show data structures, signatures, ownership, validation boundaries, and data
flow. State which complexity the interface hides and which obligations remain
with callers. Name the testing boundaries.

## Decision and alternatives

Compare the distinct candidates. Record the chosen base, any parts adopted
from other candidates, and why the alternatives lost. Explain tradeoffs that a
future reader might mistake for omissions.

## Open questions and next step

Separate unresolved requirements from implementation details the agent can
decide. Name the first verifiable slice to build.
