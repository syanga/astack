# Share test results on the PR

## Reuse existing results

Before running tests for a PR, read its description, reviews, comments, and CI results for existing verification. Reuse results when the tested code, environment, and coverage still apply. Run checks required by repository policy and targeted tests for missing coverage, changed behavior, or suspected failures.

Give delegated reviewers the applicable results and these instructions. Collect any test results they produce with their findings.

## Record each test run

For each run, record:

- The tested commit SHA and any uncommitted changes included in the run.
- The exact command and working directory.
- The outcome, with pass, fail, and skip counts when available. Include the failure summary or reason a run could not finish.
- Relevant environment details and coverage limits, such as a focused subset or unavailable integration services.

Keep the summary readable in the PR. Link bulky logs or artifacts at locations PR readers can access. After code changes, rerun affected tests and identify which results cover the final diff.

## Publish before finishing

When opening a PR, include the test results in its description's verification section. For an existing PR, add new results to the description, a review, or a comment, even when there are no findings. Publish results from delegated reviewers too. If only test results need posting, use a comment.

Label results from local changes as local until those changes are pushed. Keep their tested commit distinct from the PR head. For reused results, cite the existing PR evidence or CI run.

Confirm that the results are visible on the PR before reporting completion. If posting fails or the user requested a local-only review, return the results in the conversation and explain why they are absent from the PR. When no PR exists, retain the results for its description when one is created.
