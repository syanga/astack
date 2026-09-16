---
name: open-pr
description: Opening a pull request. Use when work is ready for review, or when asked to open a PR.
---

# Open a PR

The policy is the Pull Requests section of the global rules. This is the procedure that satisfies it.

## 1. Rebase onto the base

The base is the repository's default branch, which `gh repo view --json defaultBranchRef -q .defaultBranchRef.name` prints. When this PR is a child in a stack, the base is the parent branch instead. If unrelated changes are on the branch, move them to their own branch first. Fetch `origin`. Rebase onto `origin/<base>`. Done when `git status` is clean and `git log --oneline origin/<base>..HEAD` lists only this work's commits.

## 2. Prove the change

Run every check CI runs, the way CI runs it. The workflow files under `.github/workflows` or the repository's CI config list them. Record each command and its result for the Verification section. If the change has an observable effect, verify that effect on the real thing, following [`../principles/prove-it-works.md`](../principles/prove-it-works.md). Done when every check passed in this session and you have recorded each command and its result.

## 3. Clean the diff

Read the whole diff as its reviewer. Remove debug output, dead paths, and guards for cases the code cannot reach. Apply [`../no-comments/SKILL.md`](../no-comments/SKILL.md) to the diff against `<base>` and keep its report. If the change reaches code outside the diff, or you cannot name the safety fact (the one fact the change is safe because of), apply [`../blast-radius/SKILL.md`](../blast-radius/SKILL.md) and keep its hand-back for the Blast radius section. Done when:

- the diff has no debug output and no unreachable guard
- no comment outside the no-comments keep list remains
- you fixed or recorded as open every flag no-comments raised
- when blast-radius ran, you kept its hand-back

## 4. Shape the commits

Rebase the work into verifiable units, in the order that proves the work, following [`../principles/sequence-verifiable-units.md`](../principles/sequence-verifiable-units.md). If a fix belongs to the commit just made, amend it. If a fix is separable, give it its own commit. Done when the checks from step 2 pass at every commit.

## 5. Install the Gitleaks hook

Find out whether the repository's pre-push hook already runs a secret scanner. If it does not, run this skill's `scripts/install_gitleaks_hook.py` from the repository root. If it refuses, record its message for the reply. Done when the pre-push hook runs a secret scanner, or you have recorded the refusal.

## 6. Write the title and body

Apply [`../technical-writing/SKILL.md`](../technical-writing/SKILL.md) to the title and the body.

For the title, when the log uses Conventional Commits, use the changed area as the scope and name the symbol the change is about.

The body is the briefing the global rules describe. It is also the squash commit body, so keep it to forty lines. Write only these sections, in this order, and link any other artifact.

- `## Why`. Give the intent and the approach in one or two short paragraphs. For a bug fix, give the root cause. Add `Closes #<n>` when an issue exists.
- `## Scope`. Include it when the boundary matters. Name the symbols and paths that are in, what stays out, and both sides of a rename.
- `## Tradeoffs`. Include it when there was a real choice. Name the alternative you rejected and why.
- `## Blast radius`. Include it when the change reaches code outside the diff. Give the safety fact, the rung it reached, and the proof, as blast-radius hands them back.
- `## Verification`. List each check from step 2 with its command and result. For a performance change, give one number with its unit, before and after. Attach a screenshot or recording when it proves a claim.
- Add the model and harness line that the global rules require.

Done when:

- the title follows the repository's convention
- the body has a Why section and a Verification section
- every included section carries the content above
- `wc -l` on the body reports at most forty lines

## 7. Open the PR

Push the branch. Create the PR:

```bash
gh pr create --base <base> --title "<title>" --body-file <file>
```

If the host opened it as a draft, run `gh pr ready <n>`. Inside T3 Code, call `link_pull_request` with the URL. Done when `gh pr view <n> --json url,isDraft,baseRefName` shows the base from step 1 and `isDraft` false. Inside T3 Code, done also requires that the link call returned.

## 8. Reply

Reply with the URL, the Verification section, and the flags and constraints no-comments left open. Babysit through [`../babysit-pr/SKILL.md`](../babysit-pr/SKILL.md). Done when the reply carries the URL, the Verification section, and the open flags and constraints.
