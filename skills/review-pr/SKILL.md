---
name: review-pr
description: Adversarial review of a diff or pull request.
disable-model-invocation: true
---

# Review a PR

Read-only. The deliverable is a verdict the author can act on, with every finding sorted into act on, consider, noted, or dismissed. Nothing changes during the review; fixing is a separate request.

## 1. Fix the scope

For a PR number or URL, take `gh pr diff <n>` and `gh pr view <n> --json title,body,commits,closingIssuesReferences`. For the current branch, fetch the base and diff from the merge base, `git diff $(git merge-base origin/<base> HEAD)`, which includes the working tree. Done when the ref resolves and the diff is non-empty; otherwise say so and stop.

## 2. State the intent

One paragraph on what this change is supposed to accomplish, from the PR body, the commit messages, the linked issue (`gh issue view`), and the user's message. This is the one place a question to the user is allowed: if the intent is unclear, ask before spawning anything. Code reviewers take the intent as given and judge execution. The spec reviewer judges delivery against it.

## 3. Spawn the reviewers, in parallel, read-only

- At least two code reviewers, each given the code reviewer prompt in [`reviewer.md`](reviewer.md) filled with the intent, the diff, [`rubric.md`](rubric.md), and the ladder from [`../blast-radius/evidence.md`](../blast-radius/evidence.md). Identical prompt to all. When the harness lets you choose models, give each reviewer a different one; independence is where the signal comes from.
- One spec reviewer, given the spec reviewer prompt in [`reviewer.md`](reviewer.md) with the intent, its sources, and the diff.

Point every reviewer at the repository so it can read beyond the diff. Without a subagent tool, run each prompt yourself in turn and write the findings down before starting the next.

## 4. Judge

Apply [`judgment.md`](judgment.md). Merge duplicates and record which reviewers raised each. Trace the call site of every hypothetical before accepting it. Done when every finding has a bucket and a one-line rationale, and act on holds five items or fewer, or you say why more survived.

## Output

- **Intent.** The paragraph from step 2.
- **Reviewers.** One line each: label, model, number of findings.
- **Act on.** Findings that would block the PR. Location with the quoted line, what is wrong, why it matters, who raised it.
- **Consider.** Legitimate points with a real cost to address now. Same shape.
- **Noted.** Valid, low priority. One line each.
- **Dismissed.** Rejected findings with the reason. This section is how the user overrides you.
- **Spec.** Missing, unrequested, and wrong against the intent, each quoting the source line.
- **Agreement.** Where reviewers agreed, where they split, and what that says.
