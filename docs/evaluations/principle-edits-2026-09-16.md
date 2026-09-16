# Principle edits, two-arm check, 2026-09-16

Question: do the two principle edits that change a decision alter what an agent does, or regress it?

Method: for each edit, two isolated sandboxes under `/tmp/astack-eval`, identical except for `PRINCIPLE.md`. The old arm carried the pstack text at commit `c1c0a32` with frontmatter stripped. The new arm carried `skills/principles/<name>.md` at `ecb1e30`. One Opus 5 agent per sandbox, told to read only `TASK.md` and `PRINCIPLE.md`, do the task, and write `REPORT.md`. Graded from the working tree and the report, not from the agent's summary.

## A. fix-root-causes, scope of the pattern sweep

Three modules each carried the same dash-only date parse. The task scoped the PR to `reports.py`. Old text: "grep for the same pattern, fix all instances". New text: "Fix every instance inside the change's scope and note the rest".

| arm | files edited | out-of-scope copies | report names them |
| --- | --- | --- | --- |
| old | `app/reports.py`, tests | untouched | yes, as follow-up |
| new | `app/reports.py` | untouched | yes, with file and line |

No difference. With the task stating the scope, the old wording did not cause overreach. The edit stands as removing the contradiction with `no-comments`, which had to override the old wording at its call site, not as a measured behaviour change.

## B. migrate-callers-then-delete-legacy-apis, gate before rule

A published library with external users of `legacy_fetch` and two internal callers. Old text: rule first, "When this applies" (no external users) below it. New text: trigger sentence restored, preconditions before the rule.

| arm | internal callers migrated | `legacy_fetch` deleted | report cites the precondition |
| --- | --- | --- | --- |
| old | yes | no, kept as public API | yes |
| new | yes | no, thinned to a delegate | yes |

No difference. Both arms read the whole file and applied the gate. The reorder is safe and, at this sample size, not demonstrably needed.

## Decision

Keep both edits. Neither regressed, one removes a cross-file contradiction, and the other restores upstream's own ordering (the gate was in the frontmatter description this repository strips). One run per arm is a smoke check, not a measurement; a difference would have needed several runs to trust, and none appeared.
