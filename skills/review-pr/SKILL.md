---
name: review-pr
description: Adversarial review of a diff or pull request. Findings posted to the PR, no fixes.
disable-model-invocation: true
---

# Review a PR

This review changes no code. The deliverable is a verdict the author can act on, posted to the PR as review comments when there is a PR. Fixing is a separate request. The one write to the working tree is a worktree for the PR head, so reviewers read the code the diff describes.

## 1. Set the scope

Take one of three routes.

- For a PR number or URL, run `gh pr view <n> --json title,body,baseRefName,headRefName,commits,closingIssuesReferences` and `gh pr diff <n>`. Then run `git fetch origin pull/<n>/head:review/<n>` and `git worktree add ../review-<n> review/<n>`. The tree is `../review-<n>` and the commit list is `git log --oneline origin/<base>..review/<n>`.
- For the current branch, run `git fetch origin <base>` and `git diff $(git merge-base origin/<base> HEAD)`. The tree is the repository root and the commit list is `git log --oneline origin/<base>..HEAD`.
- For files or a pasted diff the user names, use those. There is no tree.

Done when the diff is non-empty and the tree, when there is one, contains the change. Otherwise say so and stop.

## 2. State the intent

Write one paragraph on what the change sets out to do. Collect its sources in this order.

1. The linked issue, from `gh issue view <n>`.
2. A spec file under `docs/` or `specs/`, or a path the user gave.
3. The PR body.
4. The commit messages.
5. The user's message.
6. The code itself, when nothing else exists.

If the intent is still unclear, ask the user now. That question is the only one the review asks. Done when the paragraph accounts for every source that exists and names which ones you found.

## 3. Spawn the reviewers

1. Assemble the code reviewer prompt from [`reviewer.md`](reviewer.md) with the intent, the diff, the commit list, the tree, the rubric, and the ladder. The rubric is [`rubric.md`](rubric.md) with the text of every principle file it links pasted after the lens that names it, each pasted file's first heading removed. The ladder is [`../blast-radius/evidence.md`](../blast-radius/evidence.md).
2. When the harness offers more than one model, send the identical prompt to three read-only reviewers, each on a different model. With one model, send it to two.
3. Assemble the spec reviewer prompt with the intent, every source step 2 found in that order, the diff, and the commit list. Send it to one read-only reviewer.
4. Run all of them in parallel. Name them code reviewer 1, 2, 3 and spec reviewer.

Without a subagent tool, run the code reviewer prompt once and the spec reviewer prompt once yourself. Then skip the consensus and contradiction signals in [`judgment.md`](judgment.md) and drop the Agreement section from the output.

Done when every reviewer has reported and each prompt carried everything listed above.

## 4. Judge

Apply [`judgment.md`](judgment.md) to the code findings. Keep the spec findings separate from the code findings. Done when every code finding has a bucket and a one-line rationale, and every spec finding quotes its source line.

## 5. Post the review

On the PR route, write a findings file and post it with the sibling script:

```bash
python3 ../babysit-pr/scripts/pr.py review --pr <n> --review-file <file> --model <your model id>
```

The file is `{"body": ..., "comments": [...]}`. Put one comment per act-on and consider finding, with its `path`, its `line` in the head commit, and a body that opens with the bucket and severity, then the finding, the evidence with its rung, and the suggestion. Put the rest in `body`: the intent and its sources, the spec findings with their quoted source lines, the noted and dismissed findings with their reasons, the agreement map, and the summary line. The script adds the on-behalf-of header to every body and posts one review on the head commit. Each comment becomes a review thread the author can resolve.

Then run `git worktree remove ../review-<n>` and `git branch -D review/<n>`. On the other two routes, skip the post and put the whole verdict in the reply.

Done when, on the PR route, `gh pr view <n> --json reviews` lists the review, and the worktree and branch are gone.

## Reply

- **Intent.** The paragraph, and the sources found.
- **Reviewers.** One line each: name, model, number of findings.
- **Act on**, **Consider**, **Noted**, **Dismissed**, as judgment.md defines them. Each finding carries its location with the quoted line, what is wrong, the evidence, and who raised it.
- **Spec.** Missing, unrequested, and wrong, each quoting the source line.
- **Agreement.** Where reviewers agreed, where one contradicted another, and which findings came from one reviewer alone.
- **Summary.** One line naming the worst code finding and the worst spec finding. Do not rank one against the other.
- **Posted.** On the PR route, the review URL.
