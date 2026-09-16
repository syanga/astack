---
name: review-pr
description: Adversarial review of a diff or pull request. Findings, no fixes.
disable-model-invocation: true
---

# Review a PR

Read-only. The deliverable is a verdict the author can act on. Fixing is a separate request. The one write this skill makes is a worktree for the PR head, so reviewers read the code the diff describes.

## 1. Set the scope

- For a PR number or URL: run `gh pr view <n> --json title,body,baseRefName,headRefName,commits,closingIssuesReferences`, then `git fetch origin pull/<n>/head:review/<n>` and `git worktree add ../review-<n> review/<n>`. Reviewers read that worktree.
- For the current branch: run `git fetch origin <base>`, then `git diff $(git merge-base origin/<base> HEAD)`, which covers the working tree. Reviewers read this tree.
- For files or a pasted diff the user names: use those.

Record the diff, the commit list from `git log --oneline origin/<base>..HEAD`, and the base. Done when the diff is non-empty and the tree the reviewers will read contains the change. Otherwise say so and stop.

## 2. State the intent

Write one paragraph on what the change sets out to do. Collect its sources in this order: the linked issue (`gh issue view <n>`), a spec file under `docs/` or `specs/` or a path the user gave, the PR body, the commit messages, the user's message, and the code itself when nothing else exists. If the intent is still unclear, ask the user now. This is the only question the review asks. Done when the paragraph accounts for every source that exists and names which ones you found.

## 3. Spawn the reviewers

Assemble the code reviewer prompt from [`reviewer.md`](reviewer.md): the intent, the diff, the commit list, the worktree path, [`rubric.md`](rubric.md) with the text of every principle file it links pasted after the lens that names it, and the ladder from [`../blast-radius/evidence.md`](../blast-radius/evidence.md). Send the identical prompt to three read-only reviewers, or two when the harness offers one model. Give each reviewer a different model when you can. Two models rarely make the same mistake. Assemble the spec reviewer prompt with the intent, its sources, the diff, and the commit list. Send it to one read-only reviewer. Run all of them in parallel.

Without a subagent tool, run the code reviewer prompt once and the spec reviewer prompt once yourself, and drop the Agreement section from the output.

Done when every reviewer has reported and each prompt carried everything listed above.

## 4. Judge

Apply [`judgment.md`](judgment.md) to the code findings. Keep the spec findings separate. Never merge or rerank them with code findings. Remove the worktree with `git worktree remove ../review-<n>`. Done when every code finding has a bucket and a one-line rationale, every spec finding quotes its source line, and the worktree is gone.

## Output

- **Intent.** The paragraph, and the sources found.
- **Reviewers.** One line each: label, model, number of findings.
- **Act on**, **Consider**, **Noted**, **Dismissed**, as judgment.md defines them. Each finding carries its location with the quoted line, what is wrong, the evidence, and who raised it.
- **Spec.** Missing, unrequested, and wrong, each quoting the source line.
- **Agreement.** Where reviewers agreed, where one contradicted another, and which findings came from one reviewer alone.
- **Summary.** One line: the number of findings per axis and the worst in each. No single winner across the two axes.
