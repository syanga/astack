# Post the review

Use `../babysit-pr/scripts/pr.py review` to post against the commit actually reviewed. The helper signs the review and exposes actionable findings to babysit-pr.

The helper prefixes reviews, inline comments, and thread replies with `[<model slug>] RESPONDING ON BEHALF OF <git config user.name>`. Run it from the reviewed repository so repository-specific Git identity applies. An unset or invalid name stops posting; earlier signed reviews remain recognizable after a name change.

Write a JSON file with this shape:

```json
{
  "body": "## Standards\n...\n\n## Spec\n...",
  "comments": [
    {
      "path": "src/example.py",
      "line": 42,
      "bucket": "act on",
      "body": "Act on: title. Explain the impact and evidence."
    }
  ]
}
```

Preserve the Standards and Spec reports as separate sections in the body. Include each act-on or consider finding in `comments`, labeled with its axis. The helper uses `act on` for a blocking finding and `consider` for a suggestion; these transport labels do not replace the two reports or turn a smell into a hard violation. Include source citations, reviewer models, and coverage limits. An empty later round has no comments and explains which commits were compared.

GitHub inline comments need a line in the PR diff. For a finding in unchanged code, anchor it on the changed line that causes the problem and name the actual location in the comment. For a finding about the title or description, use a diff line and explicitly label it as a metadata finding. These findings still need entries so the helper can preserve them if inline posting fails.

```bash
python3 "<this skill's directory>/../babysit-pr/scripts/pr.py" review --pr <n> --commit <reviewed sha> --review-file <file> --model <your model id>
```

The result reports `url`, `inline`, and `folded`. When `folded` is true, the findings went into the review body instead of threads. Report that limitation. Fix a malformed review file before retrying. If the reviewed commit is no longer in the PR, prepare a new snapshot and review it. For other posting failures, retry once, then return the verdict and explain that it could not be posted.

Reply with the review URL, the actionable findings, and any posting or verification limitations.
