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

## Validate and submit astack improvements

For approved astack changes, run the checks required by the repository and follow
[open-pr](../open-pr/SKILL.md) to submit a PR. If the user requested a proposal or
local edits only, stop at that stage. The caller owns any selection of proposed
changes before application.

Explain the failure the change addresses and how the result was verified.
Include only the session evidence needed to justify the change, with private
transcript details removed from the public PR. If repository access or publishing
is unavailable, retain the local diff and report the blocker.

Return the PR link and verification result. Merging and reinstalling are separate
actions governed by the user's request.
