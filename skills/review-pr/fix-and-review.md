# Fix and review until clean

The parent applies repairs after the independent reviewers finish. Keep the
Standards and Spec findings separate throughout the loop.

1. Verify each finding against the reviewed source and requirements. Record its
   axis, evidence, and disposition in the temporary review directory. Fix
   confirmed defects and requirements or documented-standard violations within
   the authorized scope. Record evidence for dismissals and duplicates. Defer
   optional smell suggestions and out-of-scope improvements without treating
   them as blockers. A defect that prevents required behavior remains a blocker.
2. Batch supported fixes. Ask only when a fix needs a product decision, a scope
   change, or missing authorization. Continue independent repairs while waiting.
   Follow the user's design-selection workflow for UI changes. Apply
   [no-comments](../no-comments/SKILL.md) to changed code and
   [unslop](../unslop/SKILL.md) to changed prose.
3. Run the required checks and verification affected by the fixes, including
   the caller's acceptance checks. For performance work, repeat the frozen
   measurement. For refactors, verify the preserved behavior. Before adding or
   changing a test, apply
   [test behavior](../principles/test-behavior-not-implementation.md).
   Follow [test results](../open-pr/test-results.md) to reuse valid evidence and
   publish new results with the tested snapshot.
4. After any repair, refresh the source snapshot and the full requested diff
   against the original comparison base, including local repairs. Repeat both
   applicable reviews from step 4 of [review-pr](SKILL.md) in separate contexts.
   Give reviewers prior findings and dispositions to check fixes for regressions.
   Carry forward a dismissal only while its supporting behavior and assumptions
   still hold. Collect both reports before the next batch of fixes.

Convergence requires a completed review of the final snapshot, passing required
checks, and no unresolved verified defects or requirements or documented-standard
violations within scope. Investigations that could prevent required behavior
must also be resolved. Deferred advice does not block convergence. A failed or
unavailable reviewer is missing coverage, never a clean result. If the user has
confirmed there is no spec, report Standards-only coverage instead of a Spec pass.

Continue while authorized repairs make progress. After every two rounds, report
remaining blockers and recurring findings. If repairs stall, report the blockers
and what would unblock them. Honor a user-supplied time or token budget and report
unfinished work when it expires. A needed decision or exhausted budget is not
convergence.

Return to step 5 of [review-pr](SKILL.md) with the final reports and repair record.
Commit, push, or merge only within the user's requested follow-through. Publish
actionable findings against the PR commit actually reviewed using
[posting](../harden-pr/posting.md). Keep local repair results distinct from the
published PR until the fixes are pushed and reviewed.
