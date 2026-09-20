# Post the review

Post review comments only for findings that require a concrete change to the reviewed commit. Keep each comment to the problem, supporting evidence, and requested change. If there are no actionable findings, post no review. Report summaries, clean results, and review coverage in the conversation. Publish [test results](../open-pr/test-results.md) in the PR description.

Use harden-pr's `scripts/pr.py review` to post against the commit actually reviewed. The helper signs the review and exposes actionable findings to harden-pr.

The helper prefixes reviews, inline comments, and thread replies with `[<model slug>] on behalf of <first name from git config user.name>`. Run it from the reviewed repository so repository-specific Git identity applies. An unset or invalid name stops posting; earlier signed reviews remain recognizable after a name change.

Write a JSON file with this shape:

```json
{
  "body": "",
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

Leave `body` empty and put each actionable finding in `comments` with bucket `act on`. Keep findings open on the PR until their fixes are pushed.

When screenshots or recordings demonstrate a finding, follow [open-pr's attachment guidance](../open-pr/SKILL.md).

If fixes remain local, report them in the conversation. After an authorized push, review the fixes and post any remaining actionable findings.

For a two-axis review, label each finding with its axis.

GitHub inline comments need a line in the PR diff. For a finding in unchanged code, anchor it on the changed line that causes the problem and name the actual location in the comment. For a finding about the title or description, use a diff line and explicitly label it as a metadata finding. These findings still need entries so the helper can preserve them if inline posting fails.

```bash
python3 "<harden-pr directory>/scripts/pr.py" review --pr <n> --commit <reviewed sha> --review-file <file> --model <your model id>
```

The result reports `url`, `inline`, and `folded`. When `folded` is true, the findings went into the review body instead of threads. Report that limitation. Fix a malformed review file before retrying. If the reviewed commit is no longer in the PR, prepare a new snapshot and review it. For other posting failures, retry once, then return the verdict and explain that it could not be posted.

Reply with the review URL, the actionable findings, and any posting or verification limitations.
