# Testing Specialist Review Checklist

Read and apply [test-behavior-not-implementation.md](../../principles/test-behavior-not-implementation.md) when evaluating existing or proposed tests relevant to the change.

## Finding standard

For a coverage defect, name the required behavior, a plausible failure under supported conditions, and why the relevant existing tests would miss it. Cite the requirement and inspected tests, or state that no relevant test exists. An untested branch or utility without a direct unit test is an investigation prompt, not a defect by itself. Tests through callers count when their assertions detect the failure.

Use the categories below to investigate relevant failures. Report test isolation or flakiness defects with their trigger and observable consequence under the skill's evidence requirements.

## Categories

### Missing Negative-Path Tests
- Required rejection or recovery behavior that tests would still pass if broken
- Guards whose removal would permit an invalid operation without failing a test
- Error paths whose incorrect result or cleanup would escape existing assertions
- Required access denial that existing tests do not verify

### Missing Edge-Case Coverage
- Boundary values: zero, negative, max-int, empty string, empty array, nil/null/undefined
- Single-element collections (off-by-one on loops)
- Unicode and special characters in user-facing inputs
- Concurrent access patterns with no race-condition test

### Test Isolation Violations
- Tests sharing mutable state (class variables, global singletons, DB records not cleaned up)
- Order-dependent tests (pass in sequence, fail when randomized)
- Tests that depend on system clock, timezone, or locale
- Unintended network dependencies that make tests unreliable

### Flaky Test Patterns
- Timing-dependent assertions (sleep, setTimeout, waitFor with tight timeouts)
- Assertions on ordering of unordered results (hash keys, Set iteration, async resolution order)
- Tests that depend on external services (APIs, databases) without fallback
- Randomized test data without seed control

### Security Enforcement Tests Missing
- Auth/authz checks in controllers with no test for the "unauthorized" case
- Rate limiting logic with no test proving it actually blocks
- Input sanitization with no test for malicious input
- CSRF/CORS configuration with no integration test

### Coverage Gaps
- Required behavior of a new public operation that existing tests do not exercise
- Changed behavior where tests assert only the old contract
- Caller tests whose assertions miss a plausible failure in a shared utility
