# Post the review

Invoking review-pr or harden-pr authorizes publication on an existing PR unless the user restricts it, such as "conversation only" or "do not post". Otherwise, report in the conversation.

At the end of each invocation, post one informational GitHub review with event `COMMENT`, even when no findings remain. Combine internal repair passes in that record. A later invocation adds a new record.

Include the reviewed commit, outcome, repairs, remaining findings, verification, and completed and missing coverage in the body. Preserve review-pr's separate axes. Keep full transcripts in local review records. Also publish [test results](../open-pr/test-results.md) in the PR description.

If a run stops early after substantive review, mark its record incomplete. If no substantive review occurred, report the reason only in the conversation. Missing coverage is not a clean result.

Use harden-pr's `scripts/pr.py review` to post against the commit actually reviewed. The helper signs the review and exposes actionable findings to harden-pr.

The helper prefixes reviews, inline comments, and thread replies with `[<model slug>] on behalf of <first name from git config user.name>`. Run it from the reviewed repository so repository-specific Git identity applies. An unset or invalid name stops posting; earlier signed reviews remain recognizable after a name change.

Write a JSON file with this shape:

```json
{
  "body": "Reviewed <sha>. Outcome, repairs, remaining findings, verification, and coverage.",
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
python3 "<harden-pr directory>/scripts/pr.py" review --pr <n> --commit <reviewed sha> --review-file <file> --model <your model id>
```

The result reports `url`, `inline`, and `folded`. When `folded` is true, the findings went into the review body instead of threads. Report that limitation. Fix a malformed review file before retrying. If the reviewed commit is no longer in the PR, prepare a new snapshot and review it. For other posting failures, retry once, then return the verdict and explain that it could not be posted.

Reply with the review URL, outcome, and any posting or verification limitations.
