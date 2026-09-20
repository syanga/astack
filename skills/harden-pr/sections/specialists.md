# Specialist reviews

## Select reviewers

Inspect the changed files and their callers, repository conventions, and test framework. Select reviewers by the behavior affected:

| When | Checklist |
|---|---|
| Every hardening review | [Testing](../specialists/testing.md) and [maintainability](../specialists/maintainability.md) |
| Changes affecting authorization, security controls, untrusted input, secrets, privileges, or sensitive data, including callers and ordering | [Security](../specialists/security.md) |
| Changes affecting I/O, resource bounds, complexity, hot paths, caching, payload size, rendering cost, or explicit performance claims | [Performance](../specialists/performance.md) |
| Schema changes or data migrations | [Data migration](../specialists/data-migration.md) |
| API contract changes | [API contract](../specialists/api-contract.md) |
| More than 100 added and removed lines, or requested simplification | [Simplification](../specialists/simplification.md) |

Honor explicit specialist requests, including `--all-specialists`. Report which reviewers were selected and which were skipped. Functional frontend checks belong to the core checklist; aesthetic review is outside this skill.

## Dispatch

Run selected specialists as fresh independent subagents in parallel with the native adversarial pass. Start without inherited conversation when supported and supply the required context explicitly. Follow-up clarification or focused fix verification may reuse a reviewer, but does not count as another fresh independent pass. Give each the full text of its checklist, the shared diff and source snapshot, relevant commit history and prior decisions, and the repository's language and test conventions. Include the response format below and the evidence and severity requirements from [SKILL.md](../SKILL.md#verify-findings).

Include the full text of the principle linked from the testing checklist in the testing reviewer's prompt.

For TypeScript changes, include [TypeScript guidance](../../typescript-best-practices/SKILL.md) in the maintainability review. Apply the existing defect and advisory criteria; style advice does not expand the hardening scope.

Ask each reviewer to apply its checklist, verify findings against the source, and leave the source unchanged. When a focused test would demonstrate a finding, request a proposed test using the repository's conventions.

## Response format

Each reviewer returns one JSON object per finding:

```json
{"severity":"CRITICAL|INFORMATIONAL","path":"file","line":42,"category":"category","summary":"Problem and failure conditions","evidence":"Supporting code or reproduction","fix":"Recommended fix","specialist":"name"}
```

Required fields are severity, path, category, summary, evidence, and specialist. Line, fix, fingerprint, and test_stub are optional. Use `advisory: true` for optional suggestions, including simplification and optimization; `lines_removable` may record the estimated reduction. Return `NO FINDINGS` when no supported findings remain.

## Collect and assess

Wait for each selected reviewer and record its outcome. For malformed output, ask the reviewer to correct it. Failure, timeout, unavailable subagents, or an unusable response is missing coverage; continue with completed results.

Merge reports of the same failure, preserving their evidence and naming the reviewers who raised it. A supplied fingerprint or matching location helps find duplicates but does not establish that they describe the same issue. Verify the merged claims using the evidence requirements in SKILL.md.

Present confirmed defects by severity and keep advisory suggestions separate. Apply simplification only when it is within the user's requested scope or the user approves the scope change. Send findings through SKILL.md's fix process.

Record the selected and skipped reviewers, completed and unavailable coverage, and findings from each. Preserve unresolved assumptions and failures in the final assessment.
