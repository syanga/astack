# Challenge the evidence

Choose experiments around the change's important invariants and suspected failures. Start from the repository's existing verification paths. A passing suite is useful evidence only for behavior it actually exercises.

## Reproduce and distinguish

For a reported defect, reproduce the trigger when feasible and retain a focused test that fails before the fix and passes afterward. When execution is impractical, trace the relevant code and constraints and state the unverified part. Separate an environment or setup failure from a demonstrated product failure.

Use fault injection for partial writes, dependency errors, delayed responses, cancellation, and restart. For concurrency, control the interleaving with barriers or deterministic scheduling where possible. Use properties or model-based tests for invariants across sequences of operations. Compare against a trusted implementation when an independent oracle is available. Choose the technique that answers the question; every change need not use every technique.

## Targeted mutations

Use mutations when tests may pass despite the failure we care about. A useful mutation represents a plausible defect: bypass an ownership check, remove an atomic predicate, skip a rollback, or change a boundary comparison. Run the unmodified baseline first, then each mutation against the relevant tests in a disposable copy or worktree.

Confirm that a failing mutant fails because the test detects the intended behavioral difference. Syntax errors, import failures, and unrelated timeouts do not demonstrate useful detection. If a mutant survives, determine whether it is equivalent, unreachable under the contract, or exposes a coverage gap. Strengthen the test for a real gap, rerun the mutation, and verify that the restored implementation passes.

Record the important mutation and detecting test, or explain why it survived. Prefer this evidence to a blanket mutation-score target. Keep mutations and destructive experiments isolated from the working change and live systems.

## Reassess after fixes

Run the checks affected by the fix, then the repository's required checks. Ask an independent reviewer to examine changes to important invariants. Broaden verification when failures or changed contracts reveal wider consequences; stop repeating checks once they pass and no new concern justifies another run.

Keep evidence associated with the commit or local diff it tested. Report what ran, its outcome, and limits that affect confidence. For performance claims, use a comparable workload and report the primary before-and-after result with its unit.
