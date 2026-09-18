# Review skill adaptations

Both skills were reset to byte-for-byte copies of the pinned upstream directories before these edits. The repository, paths, commits, and licenses are recorded in [manifest.json](manifest.json). Preserve upstream wording unless a specific integration or correctness issue requires a change.

## review-pr

Source: Matt Pocock's `skills/engineering/code-review` at `74ca5fe077456a0b3b2f5310cf9430999fd0b5fd`.

The two axes, twelve Fowler smells, reviewer criteria, aggregation, and explanation of separate reports retain their original wording and placement. Changes are limited to:

- Rename the skill, shorten the catalog description, and preserve astack's explicit invocation policy. Keep Matt's Codex display metadata and add the matching policy.
- Replace Matt's setup command and tracker-file dependency with the repository's available tracker integration.
- Infer a PR's target branch when the user supplied no base; review its head commit and record the SHA for posting.
- Put the user's supplied source and current requirements first.
- Describe the requested changes and give both reviewers the shared diff and relevant commit history, replacing the HEAD-specific comparison and prescribed Git commands.
- State that reviewers report findings without changing code.
- Link to optional PR posting when requested.

No prompt builder, separate reviewer templates, snapshot script, incremental-review protocol, or extracted smell baseline remains in review-pr.

## harden-pr

Source: gstack's `review/` at `a6b3a57512ca6d5c6aa5b68f74f736195021f96e`. The generated Markdown supplies the review text; astack does not run gstack's generator.

The core review checks, specialist criteria, test proposals, independent adversarial prompts, structured outside review, and review-after-fixes are retained. Workflow and reporting instructions are consolidated as described below. Most specialist criteria retain their upstream wording; dispatch and response instructions live in sections/specialists.md.

### Runtime integration

- Rename the skill and use astack's invocation metadata. Replace absolute gstack skill paths with local references and host-specific agent flags with available subagent tools.
- Omit the generated global preamble, onboarding, telemetry, Aside integration, global learning store, release-version queue, and private gstack design-document reference. Official documentation replaces Aside for API verification. PR/review history replaces global decision and suppression ledgers.
- Omit generator templates and their manifest. Omit TODOS-format.md, which serves gstack's ship and planning skills rather than this review.
- Infer specialist scope from the changed code instead of requiring gstack-diff-scope. Use relevant repository detectors and documented design requirements for functional frontend checks.
- Replace the Codex-only probe/launcher with a short outside-review reference that supports the current host. Keep the outside adversarial prompt and Codex's built-in structured review. Preserve failures as missing coverage and keep model identity unknown unless reported.
- Keep review snapshots, findings, dispositions, and per-pass outcomes in review artifacts instead of requiring gstack-review-log or a new astack orchestration framework.
- Use the existing astack PR helper to post assessments of existing PRs and for requested follow-through. Keep those instructions separate from the review itself. The helper derives attribution from `git config user.name` and recognizes earlier signed reviews independently of the current name.

### Policy and correctness

- Accept an explicit outside adversarial recommendation without requiring the exact `Recommendation:` prefix. Keep the structured review's severity requirements.
- Remove the three-line duplication threshold and the redundant 100,000-row index-creation rule. Limit the testing checklist's network warning to unintended dependencies that make tests unreliable.

- Establish requirements once in SKILL.md. Shorten plan-completion.md to the deliverable audit, preserving checks for code, cross-repository contents, external state, and required formats. Return discrepancies to the same scope assessment.
- Rename review-army.md to specialists.md and centralize selection, response format, aggregation, and coverage reporting there. Remove the calculated PR Quality Score and duplicated specialist schemas.
- Keep the independent adversarial pass blind to prior findings. Move the gap-focused red-team dispatch into adversarial.md and select it for unresolved interactions, consequential coverage gaps, or explicit requests instead of a line-count threshold.
- Remove design-checklist.md and its aesthetic rules. Retain functional accessibility, interaction, responsive-layout, and project-requirement checks in checklist.md.
- Keep review criteria in checklist.md and put severity, fix policy, and reporting in SKILL.md. Determine severity from the actual failure and impact rather than the checklist category. Renumber the workflow and update supporting references.

- Use review-pr's fixed-point selection and share the captured diff and source across reviewers. Refresh the snapshot after fixes instead of prescribing a Git comparison in each prompt.
- Remove the Greptile workflow and its supporting file, TODOS cross-reference, documentation-staleness scan, and unavailable `slop:diff` command from the entrypoint. Keep unslop for changed prose.
- Replace numerical confidence scores, filtering, and agreement boosts with a concise evidence requirement. Verify findings against the implementation or a reproduction, trace generated and inherited behavior, and state unresolved assumptions. Consolidate the later verification-of-claims instructions here.
- Post the final assessment and remaining actionable findings when reviewing an existing PR. Report local fixes in the conversation until an authorized push and review.

- Resolve ASK decisions from user authorization and actual product/scope choices. Severity, test stubs, and fix length alone do not require approval. UI fixes follow the user's design-selection workflow.
- Run testing and maintainability on every hardening review. A small consequential change still needs coverage. Remove historical hit-rate gating because astack has no corresponding store; honor explicit specialist requests.
- Preserve coverage failures, including malformed specialist responses and failed red-team passes. Read tests and fixtures as evidence instead of limiting them to summaries. Re-review changed source before claiming convergence; continue useful work rather than stopping at three fix cycles.
- Keep current user requirements above older plans. Verify cross-repository deliverable contents, audit all plan items, and avoid guessing that unfinished work was forgotten or caused by context exhaustion.
- Retain concrete checklist checks while correcting unsupported indexing assumptions, blanket string coercion for hashes, executable JSON-parsing claims, general ALTER TABLE CONCURRENTLY advice, destructive release retry advice, and the unsafe email-validator simplification example. Omit gstack-specific effort estimates and its ETHOS rule.

Targeted mutation and fault-injection guidance remains an astack addition in verification.md, reached when those experiments are useful. The PR helper and short follow-through guidance retain material from the earlier pstack/Matt adaptations and their licenses.

## Writing and verification

Writing-for-agents governs invocation, pointers, and conditional references. The unslop pass checks additions and modified passages; the user's instruction to preserve clear upstream wording takes precedence over applying house-style substitutions throughout the originals.

Installer checks and the repository test suite validate packaging and the retained PR transport. CLI help checks validate the documented invocation options. They do not establish review quality or successful live outside-provider execution.
