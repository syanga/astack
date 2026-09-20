# Improve an installed skill at its source

## Identify the owning repository

Confirm who manages the installed file using installation records or project
provenance. A matching skill name alone does not establish ownership.

For astack-managed skills, the source repository is
[syanga/astack](https://github.com/syanga/astack). Skill sources live under
`skills/`, and shared instruction sources live under `instructions/`.
For project-local or third-party skills, use their owning repository instead.

Locate an existing checkout from the session's project paths or known worktrees
and verify its Git remote. If none is available, clone the owning repository
into a separate task workspace. Preserve unrelated work in existing checkouts.

Read that repository's instructions. Compare the installed copy with its source
before drafting changes. Account for local edits and changes already made upstream.
Prepare the diff against source files. Installed copies are deployment outputs.

Follow the owning repository's authoring, verification, and delivery instructions.
In astack, the repository's `AGENTS.md` points to its local `author-skill` workflow.
