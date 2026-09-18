---
name: open-pr
description: Opening a pull request. Use when work is ready for review, or when asked to open a PR.
---

# Open a PR

Check whether the branch already has an open PR and update it when it does. Review the diff against the intended base and make sure its contents match the user's goal. Keep PRs narrow and unpushed commits coherent. Preserve commits already under review and add new commits for updates.

Before opening a new PR, rebase onto the latest default branch, or its parent's current tip for a stack child. Independent work and stack roots target the default branch. Each child targets its parent branch. Start substantial work on a new stack from the latest default branch.

Apply [no-comments](../no-comments/SKILL.md) to the code diff before review. Run the repository's required checks and verify the changed behavior. Use results from this session when they still cover the final diff. Report anything you could not verify.

Before pushing, install the Gitleaks hook from the repository root:

```bash
python3 "<this skill's directory>/scripts/git_hooks.py" install --repo .
```

The installer checks an existing installation and installs one when missing. If it refuses, fix the cause before pushing or stop and report it.

Apply [technical-writing](../technical-writing/SKILL.md) to the title, description, and commit messages.

Follow the repository's title convention, using recent merged PRs and Git history as examples. Where the repository uses Conventional Commits, use `type(scope): subject`, with a type such as `feat`, `fix`, `docs`, `refactor`, `test`, `chore`, or `perf`, and the changed area as the scope. Keep the subject short and imperative, with no trailing period. Prefer a title that explains the outcome, such as `fix(web): honor worktree defaults for new threads`. Name a real symbol when it carries the change.

The description is a briefing for a reviewer who has the diff but has not seen this conversation. Explain why the change exists, what is out of scope when that matters, and how you verified it. Keep it readable in under a minute and within about 40 lines, since it may become the squash commit body.

Use these sections in this order by default. Omit sections that have nothing useful to say. A small PR can use a brief description without headings.

- `## Why`. State the problem or goal and the approach in one or two short paragraphs. For a bug fix, explain what went wrong and what happens now.
- `## Scope`. Name the symbols and paths that define the boundary, including both sides of a rename or retarget. State what is in and out when that distinction matters.
- `## Tradeoffs`. Explain rejected alternatives a reviewer would reasonably ask about. Omit this section when there was no real choice.
- `## Blast radius`. In one to three sentences, name who or what the change affects and why it is safe or risky. Include the cost of leaving the problem unresolved when it helps assess the change.
- `## Verification`. Name the commands or real run paths and their results. For a performance change, give a primary measured result with its unit, before and after.

When screenshots or recordings demonstrate the change, upload them as GitHub attachments using `gh pr edit --attach` or GitHub's editor.

Keep logs, SHAs, exhaustive file inventories, and bulky evidence in linked artifacts. End with the model and harness attribution. Use commit bodies to explain what the subject leaves unsaid.

Create or update the PR using a file for the body. Confirm that it targets the intended base and is open for review, not a draft. In T3 Code, link it with `link_pull_request` when available. Return the URL, a brief verification summary, and any unresolved findings or limitations.

Opening a PR does not start a babysit. Finish the requested phase or stack first. If the user also requested babysitting, then continue with [babysit-pr](../babysit-pr/SKILL.md).
