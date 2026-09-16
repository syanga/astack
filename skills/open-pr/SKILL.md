---
name: open-pr
description: Opening a pull request. Use when work is ready to publish for review, or when asked to open, file, or push up a PR.
---

# Open a PR

The policy lives in the global instructions under Pull Requests. This is the procedure. Run the steps in order. The skill ends with a link and a stop; babysitting is a separate request, handled by [`../babysit-pr/SKILL.md`](../babysit-pr/SKILL.md).

## 1. Rebase onto the base branch

Work happens on a branch, never on the default branch. Unrelated changes that crept onto the branch go to their own branch first. Fetch, then rebase onto the latest base so every later check runs against what will actually merge. Done when `git status` is clean and `git log origin/<base>..HEAD --oneline` lists only this work's commits.

## 2. Prove the change

Run the repo's checks the way CI runs them and keep each command and its result for the body. When the change has an observable effect, verify it on the real artifact, per [`../principles/prove-it-works.md`](../principles/prove-it-works.md). Done when every check the body will name ran in this session and passed.

## 3. Clean the diff

Read the whole diff once as its reviewer. Remove debug output, dead paths, and guards against cases the code cannot reach. Then run [`../no-comments/SKILL.md`](../no-comments/SKILL.md) over the diff. For a change you do not fully trust, run [`../blast-radius/SKILL.md`](../blast-radius/SKILL.md). Done when a second read raises nothing you would ask about in review.

## 4. Shape the commits

Commit liberally while working; the shaping happens here, once. Rebase into small commits that each land on their own, ordered so the sequence proves the work: the failing test before the fix, the deletion before the reshape, per [`../principles/sequence-verifiable-units.md`](../principles/sequence-verifiable-units.md). Each commit is a future PR. Amend when a fix belongs to the commit just made; add a commit when it is separable. Done when `git log --oneline origin/<base>..HEAD` reads as the story of the change.

## 5. Guard the push against secrets

A pre-push secret scan belongs in every repo you push from. If the repo has none, install astack's Gitleaks hook. The astack checkout path is recorded in the install manifest:

```bash
ASTACK=$(python3 -c 'import json, os; print(json.load(open(os.path.expanduser("~/.local/state/astack/manifest.json")))["source"])')
python3 "$ASTACK/scripts/git_hooks.py" install --repo .
```

The installer downloads a pinned Gitleaks release on first use and chains an existing pre-push hook rather than replacing it. It declines a repo with a custom `core.hooksPath` (Husky and similar); in that case say so in the reply and go on. Done when the repo's pre-push hook runs a secret scanner, or the decline is reported.

## 6. Write the title and body

Write both with [`../technical-writing/SKILL.md`](../technical-writing/SKILL.md), then [`../unslop/SKILL.md`](../unslop/SKILL.md).

**Title.** The repo's convention. Where the log uses Conventional Commits, `type(scope): subject`: the changed area as the scope, an imperative subject, a real symbol when one carries the change, no trailing period. For example, `fix(installer): generate the Codex policy from disable-model-invocation`.

**Body.** A briefing for a reviewer who has the diff, and the squash commit body, so about forty lines at most. These sections in order; drop a section with nothing to say.

- `## Why`. The intent and approach in one or two short paragraphs. For a bug fix, the root cause. `Closes #<n>` when an issue exists. No SHAs, no rebase genealogy.
- `## Scope`. Real symbols and paths, both sides of a rename. Only when the boundary matters.
- `## Tradeoffs`. Rejected alternatives a reviewer would ask about. Skip when there was no real choice.
- `## Blast radius`. One to three sentences: what the change touches, why it is safe or risky, the cost if it stays unfixed.
- `## Verification`. Each check from step 2 and its outcome. A performance change gives one number, before and after. A screenshot or recording when it proves a claim.
- The model and harness line the global rules ask for.

Leave out file-by-file lists, methodology, and "Summary" or "Test plan" headings. Done when the body says why the change exists, what is out of scope, and how it was proven.

## 7. Open it

Push the branch, then create the PR against the base branch with `gh pr create --title ... --body-file ...`, never as a draft. If the host opened it as a draft anyway, run `gh pr ready <n>`. A child PR in a stack targets its parent branch; retarget with `gh pr edit <n> --base <parent>`. Inside T3 Code, call `link_pull_request` with the URL. Done when `gh pr view <n> --json url,isDraft,baseRefName` shows the intended base and `isDraft` false.

## 8. Stop

Reply with the link and the verification line from step 2, then stop. Opening a PR does not start a babysit; finish the phase or the stack first, since a babysit per PR stalls the build. A subagent that opens a PR returns the URL to its parent and never babysits. Polling and review threads start only when the user asks, through [`../babysit-pr/SKILL.md`](../babysit-pr/SKILL.md).
