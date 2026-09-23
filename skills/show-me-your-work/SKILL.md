---
name: show-me-your-work
description: Keep and audit an evidence-backed decision trail for long-running work and later review.
disable-model-invocation: true
---

# Show me your work

Keep one canonical log.

## The format

A single TSV file, one row per decision. Cells stay single-line. Evidence is a pointer, not prose.

Copy [the header template](references/decision-log-template.tsv) to start a clean log. Columns:

- **ts.** ISO8601 timestamp.
- **phase.** The phase or workstream.
- **decision.** What was chosen or done, one line.
- **why.** The reason in plain words. If a principle drove it, say it plainly, not as a jargon tag.
- **evidence.** A link or path that proves it: commit SHA, PR number, `file:line`, or an artifact, trace, or screenshot path. Never a paragraph.
- **result.** The outcome or predicate state: `tests green`, `reverted`, `pixel-diff 0`, `INCONCLUSIVE`, `open`.

An example, plain-spoken so a reviewer reads it at a glance. This is illustration only. Don't copy these rows into a real log.

```
ts	phase	decision	why	evidence	result
2026-05-24T09:02:00Z	frame	counted the work first, about 100 components and roughly 75 hours	wanted to know the size before starting a long run	commit 3a9f1c2	found 5 things to sort out before starting
2026-05-24T09:40:00Z	harness	took screenshots of the old version before changing anything	so we can compare old against new and catch any visual change	scripts/snapshot.sh, baseline/	saved 120 reference screenshots
2026-05-24T11:15:00Z	widget	moved the widget styles over without changing how it looks	keep the change small and the result identical	commit 7c21e0a, pixel-diff 0	looks identical, tests pass
2026-05-24T12:30:00Z	widget	threw out a helper's work because its screenshots were blank	checked the real files instead of trusting its summary	worktree reset	reverted, tightened the instructions for next time
```

## Logging a row

Write each entry the way you'd tell a teammate what you did. Name concrete actions and apply [unslop](../unslop/SKILL.md).

Use `bash "<this skill's directory>/scripts/log.sh" "<logfile>" "<phase>" "<decision>" "<why>" "<evidence>" "<result>"`. The helper writes a timestamp and creates the header on first use. It replaces tabs and line breaks with spaces, and prefixes cells starting with `=`, `+`, `-`, or `@` with a single quote. The execution owner serializes writes to this log. Workers return proposed entries to that owner. The helper does not lock the log.

Log decision points and checkpoints, not every action: a fork chosen, a unit completed with its verification result, a pivot or revert with its trigger, a blocker surfaced, a gate fixed. For loop runs, one row per iteration. Skip the trivial and self-evident.

## Where it lives

By default the log is a working artifact, not committed. Keep it at `decisions.tsv` in the work dir, or `.audit/<task-slug>.tsv` when several efforts run at once, and leave it out of git.

For a multi-phase plan, use `decisions.tsv` in the shared execution store from
[orchestrate/STATE.md](../orchestrate/STATE.md). Keep the path in the plan and handoffs.

Commit it when a reviewer needs the trail and sharing its contents is authorized.

If an existing log has a different header, preserve it under an unused
`decisions.legacy-<date-time>.tsv` name before creating the current format. Log the
transition with a link to that file. Keep earlier facts there rather than inventing
missing timestamps, reasons, or outcomes. The helper rejects an incompatible header.

## Rules

- One row is one decision or checkpoint.
- Append-only. A wrong call gets a new row that supersedes it. Never edit or delete history.
- Prefer evidence produced by committed scripts over hand-made one-offs per [Encode Lessons in Structure](../principles/encode-lessons-in-structure.md).

## Audit the log against the transcript

At the end of the run, before handing back, check the log told the truth. Read this run's transcript through the active environment's session tools or its documented transcript path. Restrict reads to this task's sessions. If transcripts are unavailable, audit the available command records and artifacts, and report that coverage limit. Walk the log against what actually happened:

- Every row maps to a real action. Append a correction for an invented or aspirational entry, identifying the original row.
- Each row's evidence resolves and shows what the row claims.
- A fork, pivot, or abandoned approach that shaped the work but isn't logged is a gap. Add it.
- Keep new entries specific. Identify earlier padding in the audit findings.

If the work diverged from what a row claims, append the corrected result with evidence. Preserve the original row so the correction remains reviewable.

## Independent review of the trail

Before handing back, spawn an independent reviewer to read the audit trail and
the run's transcript, or the available records, and flag what the user should
inspect. It reviews decisions and evidence without redoing the work. Use an
outside provider when available and permitted by the session's model policy:
Codex when hosted in Claude, and Claude when hosted in Codex. Run it per
[provider execution](../arena/PROVIDERS.md), with copies of the trail and
transcript inside its readable snapshot. If an outside provider is unavailable,
not permitted, or fails, use a fresh native agent on the session's model. If
delegation is unavailable, perform the audit yourself and report that
independent review was unavailable. Self-review does not count as independent
review.

- Decisions logged with weak or absent evidence.
- Verification steps skipped or claimed without proof in the transcript.
- Choices that look risky in hindsight (premature, scope-creeping, papering over a symptom).
- Gaps the user would otherwise miss on a casual skim.

Every handoff or final reply for a run that produced a trail ends with an
"Attention" section. Name the reviewer and model when known, or state that only
self-review was available. List flags with links to specific rows or events,
and disclose unavailable transcript or outside-provider coverage. "No flags"
is a valid finding, but it does not replace the review attribution.

## Reviewing the trail

Read top to bottom, follow the evidence pointers, spot-check. GitHub renders a committed TSV as a table. `column -s$'\t' -t decisions.tsv` renders it in a terminal.

## Composing this skill

Other skills route their audit trail here instead of inventing one. Reference it by name and let it own the format. Don't restate the columns.
