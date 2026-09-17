---
name: review-pr
description: Adversarial review of a diff or pull request. Findings posted to the PR, no fixes.
disable-model-invocation: true
---

# Review a PR

The deliverable is a verdict the author can act on, posted to the PR as review comments when there is a PR. The review changes no code. Fixing is a separate request. The first round on a PR reviews the whole change. A later round reviews only the commits since the last reviewed one, and reads what the earlier rounds decided. When the user asks for a full review, run a full round whatever the PR's history.

## 1. Set the scope

Take one of three routes.

- For a PR number or URL, run `gh pr view <n> --json title,body,baseRefName,headRefName,commits,closingIssuesReferences`. Run `git fetch origin <base>`, then `git fetch origin pull/<n>/head`, and record `git rev-parse FETCH_HEAD` as the reviewed SHA. Run `python3 <this skill's directory>/../babysit-pr/scripts/pr.py status --pr <n>` and read `our_reviews`.
  - When `last_sha_in_pr` is false, or the user asked for a full review, this is a full round. The diff is `gh pr diff <n>` and the commit list is `git log --reverse --oneline origin/<base>..<sha>`.
  - Otherwise this is a later round. The diff is `git diff "$(git merge-tree --write-tree <last_sha> $(git merge-base origin/<base> <sha>) | head -1)" <sha>`, which leaves out what a merge of the base brought in. When that diff shows conflict markers, use `git diff <last_sha>..<sha>` and tell the reviewers the base merge is included. The commit list is `git log --reverse --oneline --first-parent <last_sha>..<sha>`.
  - When `total` is above zero, on either kind of round, the earlier rounds are the output of `pr.py history --pr <n>`.

  Then run `tree="$(mktemp -d)/review-<n>" && git worktree add --detach "$tree" <sha> && echo "$tree"` and record the path.
- For the current branch, run `git fetch origin <base>` and `git diff $(git merge-base origin/<base> HEAD)`. The tree is the repository root and the commit list is `git log --reverse --oneline origin/<base>..HEAD`.
- For files or a pasted diff the user names, use those. There is no tree, and the commit list is the one line "none".

Write the diff and the commit list each to a file in the system's temporary directory, and the earlier rounds too when there are any. Done when the diff file is non-empty, the commit list file exists, and, on the PR route, `git -C <tree> rev-parse HEAD` prints the reviewed SHA. Otherwise say so and stop.

## 2. State the intent

Write one paragraph on what the change sets out to do, and save it to a file. Collect its sources in this order, and save them to a second file.

1. The linked issue, from `gh issue view <n>`.
2. A spec file under `docs/` or `specs/`, or a path the user gave.
3. The PR body.
4. The commit messages.
5. The user's message.
6. The code itself, when nothing else exists.

On a later round, the Intent section of the first review `pr.py history` marks as ours is the first source. A request that only says to babysit or review carries no intent. If the intent is still unclear, ask the user now. That question is the only one the review asks. Done when the paragraph accounts for every source that exists and names which ones you found.

## 3. Spawn the reviewers

Build each prompt as a file with this skill's `scripts/build_prompt.py`.

```bash
python3 <this skill's directory>/scripts/build_prompt.py code --intent <file> --diff <file> --commits <file> --tree <tree> --out <code prompt>
python3 <this skill's directory>/scripts/build_prompt.py spec --intent <file> --diff <file> --commits <file> --sources <file> --out <spec prompt>
```

Add `--prior <file>` whenever earlier rounds exist, and `--since <last_sha>` on a later round. Add `--lenses` to keep only the lenses of [`rubric.md`](rubric.md) the change can touch, named by the start of their headings. A change to prose alone keeps `correctness,verification,complexity`. Omit `--lenses` when the change touches code, and omit `--tree` on the route that has none. Then spawn the reviewers in parallel, each told only to read its prompt file in full and follow it.

- On a full round, send the code prompt to three reviewers, spread over as many models as the harness offers. Send it to two when the harness offers one model.
- On a later round, send the code prompt to one reviewer, on a model other than yours when the harness offers one.
- Send the spec prompt to one reviewer on a full round. On a later round, send it only when the new commits add, remove, or change a behaviour the sources name. A commit that fixes a review finding does not.

Name them code reviewer 1, 2, 3 and spec reviewer. Without a subagent tool, follow each prompt file yourself, one after the other.

Done when every reviewer this round calls for has reported.

## 4. Judge

Apply [`judgment.md`](judgment.md) to every finding, code and spec alike. On the PR route, run `git worktree remove --force <tree>` now, before anything touches the network. Done when every finding has a bucket and a one-line rationale, every missing or wrong spec finding quotes its source line, and, on the PR route, the worktree is gone.

## The verdict

The verdict has these parts, whichever route delivers it.

- **Intent.** The paragraph, the sources found, and whether this was a full or a later round.
- **Reviewers.** One line each: name, model, number of findings.
- **Act on**, **Consider**, **Noted**, **Dismissed**, as judgment.md defines them. Each finding carries its location with the quoted line, what is wrong, the evidence with its rung, and who raised it.
- **Agreement.** Where reviewers agreed, where one contradicted another, and which findings came from one reviewer alone. Include it when more than one reviewer ran, the spec reviewer counted.
- **Summary.** One line naming the worst finding, or saying there is nothing to act on.

## 5. Deliver

On the PR route, write a findings file in the system's temporary directory, in the shape the module docstring of `pr.py` documents. Its `comments` hold one entry per act-on and consider finding, and each `body` opens with the bucket and severity. The `line` must be inside a diff hunk of the PR, because GitHub rejects the whole review otherwise. Anchor a finding about an unchanged line on the changed line that leads to it, and name the real `file:line` in the comment, so every act-on and consider finding opens a thread the babysit verdict can see. Its `body` holds the intent, the reviewers, the noted and dismissed findings, the agreement, and the summary. An act-on or consider finding about the PR's title or body has no line of its own. Anchor it on any line inside a diff hunk, and say in the comment that it is about the title or the body. From the repository root, post it:

```bash
python3 <this skill's directory>/../babysit-pr/scripts/pr.py review --pr <n> --commit <reviewed sha> --review-file <file> --model <your model id>
```

It prints `url`, `inline`, and `folded`. `folded` true means every finding went into the review body and no thread was opened. Exit code 2 with a message about the review file means the file is malformed: fix it and post again. Exit code 2 with a message that the commit is not in the PR means the head moved: start again at step 1. Any other exit code 2 means the post failed: retry once, then say the review could not be posted and put the whole verdict in the reply.

Reply on the PR route in this shape, with one title line per act-on finding and the last line only when the review was folded:

```
<review URL>
Act on <n> · Consider <n> · Noted <n> · Dismissed <n>
Act on: <title>
Folded: no thread was opened.
```

On the other two routes, reply with the whole verdict.

Done, on the PR route, when the posted review body carries every other part of the verdict, and the script printed an `inline` count equal to the number of act-on and consider findings, or you reported the fold or the failed post in the reply. Done, on the other routes, when the reply carries every part of the verdict.
