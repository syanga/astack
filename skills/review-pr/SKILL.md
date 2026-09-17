---
name: review-pr
description: Adversarial review of a diff or pull request. Findings posted to the PR, no fixes.
disable-model-invocation: true
---

# Review a PR

The deliverable is a verdict the author can act on, posted to the PR as review comments when there is a PR. The review changes no code. Fixing is a separate request. The first round on a PR reviews the whole change. A later round reviews only the commits since the last reviewed one, and reads what the earlier rounds decided.

## 1. Set the scope

Take one of three routes.

- For a PR number or URL, run `gh pr view <n> --json title,body,baseRefName,headRefName,commits,closingIssuesReferences`. Run `git fetch origin <base>`, then `git fetch origin pull/<n>/head`, and record `git rev-parse FETCH_HEAD` as the reviewed SHA. Run `python3 <this skill's directory>/../babysit-pr/scripts/pr.py status --pr <n>` and read `our_reviews`.
  - When `last_sha_in_pr` is false, this is a full round. The diff is `gh pr diff <n>` and the commit list is `git log --oneline origin/<base>..<sha>`.
  - When `last_sha_in_pr` is true, this is a later round. The diff is `git diff <last_sha>..<sha>`, the commit list is `git log --oneline <last_sha>..<sha>`, and the earlier rounds are the output of `pr.py history --pr <n>`.

  Then run `tree="$(mktemp -d)/review-<n>" && git worktree add --detach "$tree" <sha> && echo "$tree"` and record the path. Each run gets its own directory and no branch, so an interrupted review or a second session cannot block or delete this one.
- For the current branch, run `git fetch origin <base>` and `git diff $(git merge-base origin/<base> HEAD)`. The tree is the repository root and the commit list is `git log --oneline origin/<base>..HEAD`.
- For files or a pasted diff the user names, use those. There is no tree.

Write the diff, the commit list, and the earlier rounds each to a file in the system's temporary directory. Done when the diff is non-empty and the tree, when there is one, contains the change. Otherwise say so and stop.

## 2. State the intent

Write one paragraph on what the change sets out to do, and save it to a file. Collect its sources in this order, and save them to a second file.

1. The linked issue, from `gh issue view <n>`.
2. A spec file under `docs/` or `specs/`, or a path the user gave.
3. The PR body.
4. The commit messages.
5. The user's message.
6. The code itself, when nothing else exists.

If the intent is still unclear, ask the user now. That question is the only one the review asks. Done when the paragraph accounts for every source that exists and names which ones you found.

## 3. Spawn the reviewers

Build each prompt as a file with this skill's `scripts/build_prompt.py`. It pastes the rubric, the principle files each lens links, and the evidence ladder, and it never rewrites the diff's own text.

```bash
python3 <this skill's directory>/scripts/build_prompt.py code --intent <file> --diff <file> --commits <file> --tree <tree> --out <code prompt>
python3 <this skill's directory>/scripts/build_prompt.py spec --intent <file> --diff <file> --commits <file> --sources <file> --out <spec prompt>
```

Add `--prior <file>` on a later round. Add `--lenses` to drop the lenses of [`rubric.md`](rubric.md) the change cannot touch: a change to prose alone keeps `correctness,verification,complexity`. Then spawn the reviewers in parallel, each told only to read its prompt file in full and follow it.

- On a full round, send the code prompt to three reviewers, each on a different model when the harness offers more than one, and to two when it offers one.
- On a later round, send the code prompt to one reviewer.
- Send the spec prompt to one reviewer on a full round. On a later round, send it only when the new commits change what the PR delivers.

Name them code reviewer 1, 2, 3 and spec reviewer. Without a subagent tool, follow each prompt file yourself, one after the other, and leave the Agreement part out of the verdict.

Done when every reviewer has reported.

## 4. Judge

Apply [`judgment.md`](judgment.md) to every finding. A spec finding is judged like a code finding: missing or wrong starts as act on, unrequested starts as consider. On the PR route, run `git worktree remove <tree>` now, before anything touches the network. Done when every finding has a bucket and a one-line rationale, every spec finding quotes its source line, and, on the PR route, the worktree is gone.

## The verdict

The verdict has these parts, whichever route delivers it.

- **Intent.** The paragraph, the sources found, and whether this was a full or a later round.
- **Reviewers.** One line each: name, model, number of findings.
- **Act on**, **Consider**, **Noted**, **Dismissed**, as judgment.md defines them. Each finding carries its location with the quoted line, what is wrong, the evidence with its rung, and who raised it.
- **Agreement.** Where reviewers agreed, where one contradicted another, and which findings came from one reviewer alone.
- **Summary.** One line naming the worst finding, or saying there is nothing to act on.

## 5. Deliver

On the PR route, write a findings file in the system's temporary directory. Its `comments` hold one entry per act-on and consider finding, with the `path`, the `line` in the head commit, the `bucket` (`act on` or `consider`), and a `body` whose first line is the bucket and severity. The `line` must be inside a diff hunk of the PR, because GitHub rejects the whole review otherwise. Anchor a finding about an unchanged line on the changed line that leads to it, and name the real `file:line` in the comment, so every act-on and consider finding opens a thread the babysit verdict can see. Its `body` holds the intent, the reviewers, the noted and dismissed findings, the agreement, and the summary. From the repository root, post it:

```bash
python3 <this skill's directory>/../babysit-pr/scripts/pr.py review --pr <n> --review-file <file> --model <your model id>
```

The script adds the on-behalf-of header to every body and posts one review on the head commit. It prints `url`, `inline`, and `folded`. `folded` true means GitHub rejected the inline comments as unprocessable, so every finding went into the review body and no thread was opened: say so in the reply. Exit code 2 means the post failed: retry once, then say the review could not be posted and put the whole verdict in the reply.

Reply on the PR route with the review URL, the number of findings per bucket, the titles of the act-on findings, and whether the review was folded. On the other two routes, reply with the whole verdict.

Done, on the PR route, when the script printed an `inline` count equal to the act-on plus consider count, or you reported the fold or the failed post in the reply. Done, on the other routes, when the reply carries every part of the verdict.
