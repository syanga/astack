---
name: tdd
description: Test-driven development. Use for test-first features, red-green-refactor, regression tests, or bug fixes with a practical local test path.
---

# Test-driven development

Make the intended behavior executable before changing its implementation.
Work in vertical slices, one failing test and its implementation at a time.

## Choose the boundary and behavior

Read [Test Behavior, Not Implementation](../principles/test-behavior-not-implementation.md)
before writing or changing tests. Use the project's domain vocabulary and
existing test conventions.

Name the behavior and the public interface where a caller can observe it.
Reuse testing boundaries already chosen in the spec or conversation. Otherwise
choose an existing boundary that reaches the behavior and state the choice.
Ask only when the choice depends on unresolved requirements or would change
the design. For interface design, read [Deep Modules](../principles/deep-modules.md).

Prefer the highest practical boundary that exercises the real behavior with a
fast, reliable signal. A cheap unit test is insufficient when the defect
requires several callers or an integration path. Read [mocking.md](mocking.md)
when external dependencies need control.

## Run the loop

1. Write one focused test for the next behavior. Derive its expected result
   independently from the requirement or a worked example.
2. Run it before changing the implementation. Confirm that it fails for the
   intended behavior, rather than a syntax, fixture, or environment error.
3. Make the smallest implementation change that satisfies the behavior and
   preserves nearby contracts.
4. Run the test again and confirm it passes. Run affected adjacent tests and
   type checks when the change reaches beyond this test.
5. Refactor when the passing code reveals a concrete simplification. Keep
   behavior fixed and rerun the affected checks.
6. Choose the next behavior based on what this cycle revealed.

Keep each completed slice green. Avoid writing the full test suite up front
against an imagined implementation. Preserve existing assertions unless the
behavioral contract has changed, and state that change.

## When a regression test is impractical

If the test would mostly exercise mocks, require unrelated fixture work, or
depend on unavailable infrastructure, explain the limitation before fixing.
Choose the closest useful check, such as a reproduction command, targeted
script, browser interaction, or replayed input. Capture failing-before and
passing-after evidence when possible.

For a hard-to-reproduce bug, follow [diagnosing-bugs](../diagnosing-bugs/SKILL.md)
to establish a useful signal. A manual check is evidence with a narrower
guarantee than a retained regression test. State that limit.

## Finish

Run the final checks appropriate to the change. Report the failure observed
before the fix, the passing check afterward, and any missing regression
coverage. If no failing-before check ran, say so.
