---
name: correct
description: Find the mistakes agents keep repeating in a repository and change the repository so each one fails a check or cannot be written.
disable-model-invocation: true
---

# Correct

The operator keeps correcting agents in this repo for the same mistakes. Change the repo so the next agent can't make them.

Assume every contributor is an agent that sees only the files it opened, copies the nearest example, and takes the shortest path that compiles. Design the repo so a change that looks right from one file is right for the whole repo.

For a diagnosis-only request, give the Reply below and change nothing.

## Find the mistake classes

First, read recent commits, reverts, review comments, agent instruction files (`AGENTS.md`, `CLAUDE.md`), and comments that explain workarounds. Group the mistakes into classes. A class counts once it has happened twice.

## Fix each class at the highest level that works

[Encode Lessons in Structure](../principles/encode-lessons-in-structure.md) explains why a mechanism beats an instruction.

1. **Eliminate it with architecture.** Give each piece of state one owner and each task one supported way. Hide internals so the wrong import fails. Replace hand-synced lists with one source of truth. Delete old ways and dead code an agent would copy, per [Migrate Callers Then Delete Legacy APIs](../principles/migrate-callers-then-delete-legacy-apis.md). If external users depend on an old way, keep it and report the migration it needs.
2. **Enforce it with types so the bad state can't be written.** If bad code still compiles, add a lint or CI check whose error names the file, type, or function to use instead. If the pattern is already common, fail only when a change adds more.
3. **Test the behavior.** Fix or delete any test that still passes when the behavior it checks breaks, or that fails when only the internals change.
4. **Write docs or agent rules last, only for judgment calls.** Nothing fails when an agent skips them.

## Fix and prove

Then fix the most frequent classes now, one commit each when commits are in scope, otherwise one reviewable diff each. Prove each new check fails on a real past mistake. Run the same command locally and in CI. Without CI, wire it into the repo's existing check command or a pre-commit hook, or mark its rule-table row as not run automatically and report the gap. Exceptions go on the offending line with a reason, an expiry date, and a human's approval. Ask the operator for that approval, and never record one you did not get.

## Keep the rule table

Last, keep a table in the repo's agent instruction file (`AGENTS.md`, or `CLAUDE.md` when there is no `AGENTS.md`) that pairs each rule with what enforces it. When the operator corrects you, fix the mistake and add the rule. If the rule was already there and nothing enforces it, that's a repeat, so fix it at the highest level in the same change. Drop a rule once its mistake can't happen. Deliver through [open-pr](../open-pr/SKILL.md) within the task's delivery scope.

**Reply:** each class with its evidence, the level you picked, and why a higher level didn't work.
