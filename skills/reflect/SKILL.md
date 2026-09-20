---
name: reflect
description: Extract durable lessons and reusable workflows from the active conversation into existing guidance or a justified new skill.
disable-model-invocation: true
---

# Reflect

Mine the current conversation for durable lessons and demonstrated reusable workflows. Route them into existing guidance or a new skill when no existing home fits.

## When to invoke

Invoke when the user says "reflect" or "/reflect". Skip when the conversation is trivial, off-topic, or already covered by an existing skill the parent followed correctly. Do not generalize incidental one-offs. An explicitly requested workflow capture can use one completed, verified example.

## Process

### 1. Locate the active transcript

The parent finds its own transcript before fanning out. Use the location supplied by the harness, verify that it belongs to the active session, and search only the active workspace if needed. If no transcript resolves, write a tight digest of the session and pass that instead.

### 2. Spawn three reviewers in parallel

Spawn three reviewers in parallel with the available subagent tool. Reviewers need read access to context referenced in the transcript. If subagents are unavailable, apply the three lenses directly and report that the review was not independent.

| Lens | Prompt template |
|---|---|
| Judgment | [references/judgment-reviewer.md](references/judgment-reviewer.md) |
| Tooling | [references/tooling-reviewer.md](references/tooling-reviewer.md) |
| Divergent | [references/divergent-reviewer.md](references/divergent-reviewer.md) |

Pass each template verbatim, substituting the transcript path or digest where marked. Include the absolute template path so reviewers can resolve its pointers. Reviewers return findings in their response.

### 3. Synthesize

Spawn one synthesizer subagent with read access to the cited sources. Use [references/synthesizer.md](references/synthesizer.md) verbatim, with each reviewer's full output inlined where marked and the absolute template path supplied. The synthesizer returns a structured Accepted / Rejected / Backlog list. If subagents are unavailable, synthesize directly.

### 4. Structural enforcement check

Sanity-check the synthesizer's Accepted list. For any item that would be enforced more reliably by a lint rule, script, metadata flag, or runtime check, move it from Accepted to Backlog. Read [encode lessons in structure](../principles/encode-lessons-in-structure.md). Route shared lessons to an existing applicable principle instead of duplicating them across skills.

### 5. Apply

Read [writing-for-agents](../writing-for-agents/SKILL.md) before drafting edits. For installed skills, follow its [source-editing workflow](../writing-for-agents/SOURCE-EDITS.md) to locate the owning repository. Present the synthesizer's full Accepted/Rejected/Backlog output with concrete proposed diffs. The user picks which subset to apply and may redirect routings. Wait for selection unless the user already authorized those edits.

Report Backlog items as implementation proposals. File them to a tracker only when requested.

For each approved Accepted item, follow the Routing field exactly:

- Trivial existing-skill edit (a one-line bullet, a tightened sentence, a stale fact corrected): parent does directly.
- Substantive existing-skill edit (a new section, a new pattern table, more than ~10 lines): follow `writing-for-agents`, then exercise a representative case when the edit changes behavior.
- `tune description: <skill path>` (the skill exists but didn't trigger when it should have): follow `writing-for-agents` and check the description against representative requests.
- `new skill: <source path>`: follow `writing-for-agents` and its skill-mechanics reference in the owning repository's skill source location. Exercise a representative task before delivery.
- `instructions: <source path>`: update the owning instruction source under `writing-for-agents`.

If your environment ships a SKILL.md validator, run it on every touched skill before declaring done. Skip this step if it doesn't.

For approved astack improvements, follow the source-editing workflow to submit a PR.

## Route learnings

Reviewers and the synthesizer apply these rules to the active transcript only.

- **Existing guidance.** A body correction needs evidence from guidance the session used. For a model-invoked skill visible in the catalog that should have triggered, propose `tune description: <skill path>`. Distinguish a missing rule from a failure to follow a clear existing rule.
- **Workflow capture.** A repeated workflow, or an explicitly requested capture of a completed and verified workflow, can justify a skill even when no skill was invoked. Cite the actual steps, outcome, and non-obvious decisions. Read plausible existing homes before proposing a new one. If one already owns the workflow, extend it where the demonstrated gap belongs. Leave unsuccessful or unverified steps as unresolved evidence, not instructions to repeat.
- **Standing preferences.** An explicit durable preference or consistent repeated correction belongs in instructions when it applies across tasks. Keep task-specific requests within their original scope.
- **Destination.** Put cross-project workflows in astack's `skills/` and project-specific workflows in the owning repository's skill source directory. Put standing preferences in astack's `instructions/` or the project's instruction source, according to scope. Name the repository and source path in every proposed routing. Edit source files rather than installed or generated copies. Keep private transcript details out of committed guidance and public PRs.

New skills need a recognizable trigger and reusable decisions that existing guidance does not cover. Prefer a demonstrated command or helper when prose would only repeat a mechanical procedure.

## Summarize for the user

Short list, no preamble:

- Edits applied: `<skill path>`. What changed, one line each.
- New skills created: `<skill path>`. One line each (rare).
- Backlog proposed, or filed when requested: one line each.
- PR opened: link and verification result. Omit when no PR was requested or possible.
- Dropped: one line per rejected finding + reason from the synthesizer.
