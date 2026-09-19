---
name: to-spec
description: Turn the current conversation and codebase understanding into an implementation spec.
disable-model-invocation: true
---

# To spec

Synthesize the decisions already made. Read any supplied source documents and
inspect the affected code if its current behavior is still unknown. Use the
project's domain vocabulary and relevant ADRs.

Write the spec using [SPEC.md](SPEC.md). Scale its detail to the work. Capture
user-visible behavior and important failure cases without manufacturing a long
list of stories. Separate decisions from assumptions and open questions.

Identify the public interfaces where the specified behavior can be tested.
Read [Test Behavior, Not Implementation](../principles/test-behavior-not-implementation.md).
Reuse existing testing boundaries and decisions from the conversation. Mark a
new boundary as proposed when it changes the design or needs user judgment.

Keep this a synthesis pass. Record missing decisions instead of starting a new
interview or filling gaps with invented requirements. A spec is ready for
implementation only when no open question blocks its acceptance criteria.

Use the destination requested by the user or the project's established spec
location. If neither exists, return the spec in the conversation. Publish to an
external tracker only within the authorized task scope, using available tools
and the project's actual labels. A local spec needs no tracker setup.

Return the spec or its link, its readiness, and any blocking questions. Use
[to-tickets](../to-tickets/SKILL.md) when the user also wants executable slices.
