# Audit deliverables

Use the requirements established in SKILL.md's scope check. When a relevant plan or specification is available, read it and extract every actionable deliverable, including code, tests, migrations, configuration, and documentation. Otherwise audit the requirements already identified from the user's request and PR context. Current user decisions take precedence over older plans.

Include work described in prose as well as checkboxes. Exclude background, unresolved proposals, and explicitly deferred work. An implementation that achieves the agreed goal by different means can satisfy the requirement.

## Verify each deliverable

Choose evidence that can establish whether the requirement is met:

- For code and tests, read the implementation and relevant tests. A changed file or checked box alone does not prove completion.
- For a deliverable in another repository, locate the repository from the supplied path or available workspace context and inspect the required contents. Report an unreachable repository as missing coverage.
- For an external configuration or deployment, inspect available read-only evidence when authorized. If it cannot be verified, name the system and the check still needed. Code supporting a deployment does not prove the deployment happened.
- For a required file format or convention, inspect the contents and use the repository's relevant validator when available. Check what the validator establishes before treating a pass as proof of the whole requirement.

Record each deliverable as done, partial, not done, changed approach, or unverified, with its evidence. Distinguish negative evidence from missing access. Investigate discrepancies against the code, history, and prior decisions; report the reason only when evidence supports it.

## Report discrepancies

Feed missing or partial requirements and unrequested changes into the scope assessment. Keep the full item audit in the review records and summarize discrepancies, their impact, and verification limits for the user.

Handle missing work through the same fix and scope-decision process as other findings.
