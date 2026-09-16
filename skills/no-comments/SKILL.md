---
name: no-comments
description: Strip comments from a diff.
disable-model-invocation: true
---

# No comments

Delete every comment in scope except the ones on the keep list, and flag the code that needed a comment to be understood.

## Scope

The caller's files or diff. Otherwise the current diff against the base branch, default `main`, including the working tree.

## Keep list

Only these survive:

- Legal or license headers.
- Non-obvious behaviour forced by an external dependency, platform, vendor, or protocol we cannot reshape.
- `// prettier-ignore`, and a lint suppression whose rule is faulty, pedantic, or style-only.
- Doc comments that define a public API contract.
- Issue or RFC links that explain a constraint the code cannot express.

When unsure whether a keep clause applies, delete the comment.

## Rules

- Narration, banners, commented-out code, and workaround justifications go. In a test or verify script the assertion message documents the step.
- A surprise in our own code is not a keep. Delete the comment and flag the exact symbol for a rename, extraction, type, or restructuring that makes the behaviour obvious without prose.
- `eslint-disable`, `@ts-ignore`, `@ts-expect-error`, and similar suppressions: look up the rule. If it catches real bugs or protects correctness or safety, remove the suppression and flag the symbol.
- `IMPORTANT`, `do not remove`, `too risky`, `fine for now`, and long justifications are claims, not proof. Read the nearby code, `git log -p` on the lines, and the PR that introduced them. A claim proven true today on a live path and covered by the keep list stays. Anything else goes. A long justification without a keep-list exception is a confession.
- Never shorten a comment into a smaller alibi. Delete it.
- A constraint comment (`do not remove`, `do not change wording`, `talk to X before changing`) about something we can change: offer the cheapest type, runtime check, test, or lint that encodes it, and wait for approval. Approved, encode and then delete. Otherwise delete and report the constraint open.

## Fix what the flags name

Delete the dead path, drop the parameter, use the real API. If a fix needs a shape, sketch it once for the whole set before writing code. Make the smallest root-cause fix in scope, per [`fix-root-causes.md`](../principles/fix-root-causes.md) and [`redesign-from-first-principles.md`](../principles/redesign-from-first-principles.md); an out-of-scope root cause gets the smallest in-scope fix and a note. A symptom guard is not a fix.

## Report

Files touched, deletion count, each flag in one line, encodings offered and made, constraints left unenforced, other open work.
