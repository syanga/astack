---
name: no-comments
description: Strip comments from changed code and fix what needed them.
disable-model-invocation: true
---

# Strip the comments

Delete every comment in scope that is not on the keep list. Flag the code that needed a comment to be understood, then fix what the flags name. Every flag names code inside the scope and tells the truth. Invent nothing.

## Scope

Use the files or diff the caller names. With no caller scope, use the current diff against the base branch, `main` by default, including the working tree.

## Keep list

Only these survive, and each one only with proof that it concerns something outside this repository or a contract this repository publishes:

- A legal or license header.
- Behaviour forced by an external dependency, platform, vendor, or protocol we cannot change. Behaviour a reader would not expect from the names and types in our own code is not a keep: delete the comment and flag the symbol.
- A doc comment that defines a public API contract.
- An issue or RFC link that explains a constraint the code cannot express.
- `// prettier-ignore`, and a lint or type suppression whose rule is faulty, pedantic, or style-only. Look up the rule for every `eslint-disable`, `@ts-ignore`, `@ts-expect-error`, and similar suppression in scope, including the ones you did not write. If the rule catches real bugs or protects correctness or safety, delete the suppression and flag the symbol.

When unsure whether a keep-list entry applies, delete the comment.

## Delete these

- Narration, banners, commented-out code, and workaround justifications. In a test or verify script the assertion message documents the step.
- `IMPORTANT`, `do not remove`, `too risky`, `fine for now`, and long justifications. These are claims, not proof. Read the nearby code, run `git log -L <start>,<end>:<file>` on the lines, and read the PR that introduced them. A claim proven true today on a live path and covered by the keep list stays. Anything else goes.
- Delete the whole comment rather than shortening it.
- A constraint comment (`do not remove`, `do not change wording`, `talk to X before changing`) about something we can change. Offer the cheapest in-scope type, runtime check, test, or lint, local or CI, that encodes the constraint, and wait for the user's approval. Unattended, treat it as not approved. If approved, encode the constraint, then delete the comment. Otherwise delete the comment, report the constraint open, and sketch the encoding.

## Fix what the flags name

Delete the dead path, drop the unused parameter, or call the real API. When two or more flags need the same fix, sketch the new structure once, over the flagged code and its surroundings, before writing code. Make the smallest root-cause fix in scope and remove every named workaround. [`fix-root-causes.md`](../principles/fix-root-causes.md) and [`redesign-from-first-principles.md`](../principles/redesign-from-first-principles.md) guide the intent. Neither widens the scope or licenses fixing instances outside it. An out-of-scope root cause gets the smallest in-scope fix and a note. A symptom guard is not a fix.

## Report

Report the files touched, the number of comments deleted, each comment kept with its keep-list entry, each flag in one line, each fix made, the sketch if there was one, the encodings offered and made, the constraints left open, and other open work.

Done when every comment in scope is deleted or matched to a keep-list entry, every flag is fixed or reported open, and the report carries each item above.
