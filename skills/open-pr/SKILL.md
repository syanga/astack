---
name: open-pr
description: Opening a pull request. Use when work is ready to publish for review, or when asked to open, file, or push up a PR.
---

# Open a PR

The standing rules for titles, bodies, drafts, rebasing, babysitting, and merging live in the global instructions under Pull Requests. This is the procedure that satisfies them. Run the steps in order. The skill ends with a link and a stop; babysitting is a separate request, handled by [`../babysit-pr/SKILL.md`](../babysit-pr/SKILL.md).

## 1. Rebase onto the base branch

Work happens on a branch, never on the default branch. Fetch, then rebase onto the latest base so every later check runs against what will actually merge. Done when `git status` is clean and `git log origin/<base>..HEAD --oneline` lists only this work's commits.

## 2. Prove the change

Run the repo's checks the way CI runs them (tests, typecheck, lint) and keep each command and its result for the body. When the change has an observable effect, verify that effect on the real artifact rather than a proxy, per [`../principles/prove-it-works.md`](../principles/prove-it-works.md). Done when every check the body will name ran in this session and passed.

## 3. Read the diff as the reviewer

Read the whole diff once. Delete narration comments, commented-out code, debug output, and guards against cases the code cannot reach. Keep a comment only when it says something the code cannot: a why, an external constraint, a link. For a change you do not fully trust, run [`../blast-radius/SKILL.md`](../blast-radius/SKILL.md) before going on. Done when a second read raises nothing you would ask about in review.

## 4. Shape the commits

Rebase into small commits that each stand on their own, ordered so the sequence proves the work: the failing test before the fix, the deletion before the reshape, per [`../principles/sequence-verifiable-units.md`](../principles/sequence-verifiable-units.md). Amend when a fix belongs to the commit just made; add a commit when it is separable. Done when `git log --oneline origin/<base>..HEAD` reads as the story of the change.

## 5. Guard the push against secrets

A pre-push secret scan belongs in every repo you push from. If the repo has none, install astack's Gitleaks hook. The astack checkout path is recorded in the install manifest:

```bash
ASTACK=$(python3 -c 'import json, os; print(json.load(open(os.path.expanduser("~/.local/state/astack/manifest.json")))["source"])')
python3 "$ASTACK/scripts/git_hooks.py" install --repo .
```

The installer downloads a pinned Gitleaks release on first use and chains an existing pre-push hook rather than replacing it. It declines a repo with a custom `core.hooksPath` (Husky and similar); in that case say so in the reply and go on. Done when the repo's pre-push hook runs a secret scanner, or the decline is reported.

## 6. Write the title and body

Title in the repo's convention, Conventional Commits where the log uses them, imperative, no trailing period. The body is a briefing for a reader who has the diff open. Open with the problem in one or two sentences, then how the change solves it. Add `## Verification` naming each check from step 2 and its result. Add `## Scope` only when the boundary of what is in and out matters, `## Tradeoffs` only for a rejected alternative a reviewer would otherwise ask about, and `## Blast radius` when the change touches code beyond the diff. Leave out SHAs, file-by-file lists, and "Summary" or "Test plan" boilerplate. End with the model and harness blurb the global rules ask for. Check the prose against the rules in [`../unslop/SKILL.md`](../unslop/SKILL.md). Done when the body says why, what, and how it was proven, in under about forty lines.

## 7. Open it

Push the branch, then create the PR against the base branch with `gh pr create --title ... --body-file ...`, never as a draft. A child PR in a stack targets its parent branch. Inside T3 Code, call `link_pull_request` with the URL. Done when `gh pr view --json url,isDraft,baseRefName` shows the intended base and `isDraft` false.

## 8. Stop

Reply with the link and the verification line from step 2, then stop. Polling checks and answering review comments start only when the user asks, through [`../babysit-pr/SKILL.md`](../babysit-pr/SKILL.md).
