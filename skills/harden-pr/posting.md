# Post the review

Invoking review-pr or harden-pr authorizes publication on an existing PR unless the user restricts it, such as "conversation only" or "do not post". Otherwise, report in the conversation.

At the end of each invocation, post one informational GitHub review with event `COMMENT`, even when no findings remain. Combine internal repair passes in that record. A later invocation adds a new record.

Include the reviewed commit, the review's fixed point, the outcome, repairs, remaining findings, verification, and completed and missing coverage in the body. Preserve review-pr's separate axes. Keep full transcripts in local review records. Also publish [test results](../open-pr/test-results.md) in the PR description.

If a run stops early after substantive review, mark its record incomplete. If no substantive review occurred, report the reason only in the conversation. Missing coverage is not a clean result.

Use harden-pr's `scripts/pr.py review` to post against the commit the review covers. After you push verified repairs, post against the last pushed commit that the review covered. Post against a later commit, such as a CI fix pushed after the review ended, only after a review covers it. When a conflict-free rebase or replay of the reviewed commit produced a commit with an unchanged patch, post against that rebased commit. In that review's body, name the reviewed SHA and the rebased SHA. The helper signs the review and exposes actionable findings to harden-pr.

When a review-pr run stops at its pass limit, start the review body with this pass-limit alert, followed by a blank line:

```markdown
> [!CAUTION]
> Not converged after <n> review-pr passes. Do not merge. Read the review's Direction section before running more passes.
```

Include review-pr's `## Direction` section in that review body. That review is the pass-limited review record.

Put the same alert at the top of the PR description. Add `> Review: <url>` after the alert's text, with the `url` that `pr.py review` returns. If posting fails, omit that line. When a later run stops at its pass limit, replace the alert.

A later run covers the pass-limited run when its diff includes everything the pass-limited run reviewed. A run against the PR's target branch covers it. Keep the alert until a later review-pr or harden-pr run converges and covers the pass-limited run. Before you post that run's record, read the PR description with `gh pr view <n> --json body` and remove the alert.

The helper prefixes reviews, inline comments, and thread replies with `[<model slug>] on behalf of <first name from git config user.name>`. Run it from the reviewed repository so repository-specific Git identity applies. An unset or invalid name stops posting; earlier signed reviews remain recognizable after a name change.

Write a JSON file with this shape:

```json
{
  "body": "Reviewed <sha> against <fixed point>. Outcome, repairs, remaining findings, verification, and coverage.",
  "comments": [
    {
      "path": "src/example.py",
      "line": 42,
      "bucket": "act on",
      "body": "Act on: title. Explain the problem, evidence, and requested change."
    }
  ]
}
```

Put findings requiring a concrete change in `comments` with bucket `act on`. Include the problem, evidence, and requested change. With no remaining findings, use an empty `comments` list. Keep findings open until their fixes are pushed.

When screenshots or recordings demonstrate a finding, follow [open-pr's attachment guidance](../open-pr/SKILL.md).

For a review with separate axes, label each finding with its axis.

GitHub inline comments need a line in the PR diff. For a finding in unchanged code, anchor it on the changed line that causes the problem and name the actual location in the comment. For a finding about the title or description, use a diff line and explicitly label it as a metadata finding. These findings still need entries so the helper can preserve them if inline posting fails.

```bash
python3 "<harden-pr directory>/scripts/pr.py" review --pr <n> --commit <sha to post against> --review-file <file> --model <your model id>
```

The result reports `url`, `inline`, and `folded`. When `folded` is true, the findings went into the review body instead of threads. Report that limitation. Fix a malformed review file before retrying. If the helper reports that the commit is not in the PR, prepare a new snapshot and review it. For other posting failures, retry once, then return the verdict and explain that it could not be posted.

Reply with the review URL, outcome, and any posting or verification limitations.
