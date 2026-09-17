---
name: review-pr
description: Adversarial review of a diff or pull request. Findings posted to the PR, no fixes.
disable-model-invocation: true
---

# Review a PR

The deliverable is a verdict the author can act on, posted to the PR as review comments when there is a PR. The review changes no code. Fixing is a separate request. It creates one local branch and worktree for the PR head, so reviewers read the code the diff describes, and removes both.

## 1. Set the scope

Take one of three routes.

- For a PR number or URL, run `gh pr view <n> --json title,body,baseRefName,headRefName,commits,closingIssuesReferences` and `gh pr diff <n>`. Run `gh api "repos/{owner}/{repo}/pulls/<n>/comments" --paginate` and keep the result: it is every earlier review comment and reply on the PR. An interrupted review leaves its worktree and branch behind, and the fetch then fails. Remove them first with `git worktree remove --force ../review-<n>` and `git branch -D review/<n>`, and ignore the error when either does not exist. Then run `git fetch origin pull/<n>/head:review/<n>` and `git worktree add ../review-<n> review/<n>`. The tree is `../review-<n>` and the commit list is `git log --oneline origin/<base>..review/<n>`.
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

1. Assemble the code reviewer prompt from [`reviewer.md`](reviewer.md), which says how to fill each placeholder from [`rubric.md`](rubric.md), the principle files it links, and [`../blast-radius/evidence.md`](../blast-radius/evidence.md).
2. When the harness offers more than one model, send the identical prompt to three read-only reviewers, each on a different model. With one model, send it to two.
3. Assemble the spec reviewer prompt from the same file. Send it to one read-only reviewer.
4. Run all of them in parallel. Name them code reviewer 1, 2, 3 and spec reviewer.

Without a subagent tool, run the code reviewer prompt once and the spec reviewer prompt once yourself. Then skip the consensus and contradiction signals in [`judgment.md`](judgment.md) and leave the Agreement part out of the verdict.

Done when every reviewer has reported and each prompt carried everything listed above.

## 4. Judge

Apply [`judgment.md`](judgment.md) to the code findings, using the earlier review comments from step 1 for its already-answered filter. Keep the spec findings separate from the code findings. On the PR route, run `git worktree remove ../review-<n>` and `git branch -D review/<n>` now, before anything touches the network. Done when every code finding has a bucket and a one-line rationale, every spec finding quotes its source line, and, on the PR route, the worktree and branch are gone.

## The verdict

The verdict has these parts, whichever route delivers it.

- **Intent.** The paragraph, and the sources found.
- **Reviewers.** One line each: name, model, number of findings.
- **Act on**, **Consider**, **Noted**, **Dismissed**, as judgment.md defines them. Each finding carries its location with the quoted line, what is wrong, the evidence with its rung, and who raised it.
- **Spec.** Missing, unrequested, and wrong, each quoting the source line.
- **Agreement.** Where reviewers agreed, where one contradicted another, and which findings came from one reviewer alone.
- **Summary.** One line naming the worst code finding and the worst spec finding, each on its own terms.

## 5. Deliver

On the PR route, write a findings file in the system's temporary directory. Its `comments` hold one entry per act-on and consider finding, with the `path`, the `line` in the head commit, the `bucket` (`act on` or `consider`), and a `body` whose first line is the bucket and severity. The `line` must be inside a diff hunk, because GitHub rejects the whole review otherwise. A finding on any other line goes in the review `body`. Its `body` holds every other part of the verdict. From the repository root, post it with the sibling skill's script:

```bash
python3 <this skill's directory>/../babysit-pr/scripts/pr.py review --pr <n> --review-file <file> --model <your model id>
```

The script adds the on-behalf-of header to every body and posts one review on the head commit. It prints `url`, `inline`, and `folded`. `folded` true means GitHub rejected the inline comments as unprocessable, usually a line outside the diff, so every finding went into the review body and no thread was opened: say so in the reply. Exit code 2 means the post failed: retry once, then say the review could not be posted and put the whole verdict in the reply.

Reply on the PR route with the review URL, the number of findings per bucket, the titles of the act-on findings, and whether the review was folded. On the other two routes, reply with the whole verdict.

Done, on the PR route, when the script printed an `inline` count equal to the act-on plus consider count, or you reported the fold or the failed post in the reply. Done, on the other routes, when the reply carries every part of the verdict.
