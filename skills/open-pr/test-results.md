# Run and share PR tests

The base branch is the PR's target, which is the parent branch for a stack child. Fetch it before any check below. The change is the file list from `git diff --name-only --no-renames origin/<base branch>...HEAD`. The covering suites are the test suites that cover the change, chosen under [Pick the suites](#pick-the-suites). A file affects a covering suite when it is in that suite's component or in a component the suite depends on, directly or transitively. A file affects every covering suite when it is repository-wide build or test configuration, or code whose dependents you cannot trace. A result's base commit is its `base` field.

## Reuse a published result

When the PR exists, list its published results before running tests, with `python3 "<harden-pr directory>/scripts/pr.py" tests --pr <n>`. The output's `latest_by_suite` gives each suite's newest result with its tested and base commits. Check whether the results still cover the current code. List the files changed since a result ran:

- On the branch: `git diff --name-only --no-renames <tested commit> HEAD`
- On the base, when HEAD does not contain `origin/<base branch>`: `git diff --name-only --no-renames <result's base commit> origin/<base branch>`

A covering suite keeps a `pass` or `fail` result when that result includes it and no listed file affects it. Rerun a suite whose newest result is `incomplete`. Run the other covering suites after [Rebase before the published run](#rebase-before-the-published-run). When the tested commit cannot be fetched, rebuild the tested commit by merging the result's `heads` onto the result's base. When the result lists no `heads`, run every covering suite.

## Rebase before the published run

Before a run you publish, list the files the base changed with `git diff --name-only --no-renames HEAD...origin/<base branch>`. Rebase onto `origin/<base branch>` when any of them affects a covering suite, following [pr-work's push rules](../harden-pr/pr-work.md#push-and-wait). When an orchestrate brief forbids rebasing, run on the current branch and say so in the published result.

## Pick the suites

Run the covering suites on the final code. Run each through its own command or the test runner's filter. Map each suite to the code it covers using the repository's instructions, CI configuration, and build files. Read them on both HEAD and `origin/<base branch>`, so new dependents on the base count. In a repository with one suite, run it whole. In a repository of independent components with their own suites, run the suite of each changed component. Also run the suite of each component that depends on a changed component, directly or transitively.

Run the full suite when no command or filter selects the covering suites. Also run it when the change touches any of these:

- code that no component's suite covers
- repository-wide build or test configuration
- code whose dependents you cannot trace

Run the other checks that repository instructions require, such as lint or typecheck. When the instructions name a repository-wide test command, scope it to the covering suites. When the instructions explicitly require a broader run, such as the full suite before every PR, run it.

## Publish the result

When you or a delegated reviewer runs tests for a PR, publish each result with the PR helper. It posts a PR comment and sets the `astack/tests` commit status on the PR commit you name:

```bash
python3 "<harden-pr directory>/scripts/pr.py" post-tests --pr <n> --commit <PR head SHA> --result-file <file> --model <your model id>
```

Write the result file as JSON:

```json
{
  "tested": "<full SHA the suites ran on>",
  "base": "<full SHA from git merge-base>",
  "command": "<the command you ran>",
  "suites": {"packages/api": "pass", "packages/web": "fail"},
  "summary": "<counts, including failures and skipped tests>",
  "scope": "<how you scoped the run and why>",
  "limits": "<coverage limits, when any>"
}
```

- Set `base` to `git merge-base <tested commit> origin/<base branch>`.
- Name each suite by the path or package its command runs, so later runs match it.
- State the scope and why, such as `packages/api` and its dependents because no shared configuration changed.
- Mark a covering suite that could not run as `incomplete`. It is a coverage limit, not a pass.
- Omit `limits` when there are none.
- When the PR head moved and some suites kept their result, post only those suites for the new head, with their original `tested`, `base`, and `command`. Post rerun suites as a separate result.
- When the tested commit is a scratch merge, such as a batch or a merge-time check, add `"heads"` with every head SHA merged onto the base.

When a covering suite fails, fix the cause within the task's scope and publish the new result. If you cannot fix it, publish the failure and report it.

`post-tests` needs push access. If it stops before posting, put the full result in the description. Report why it stopped. If it reports `status_error`, the comment is posted but other agents ignore it, because it has no matching status. Report the missing status without posting the result again.

Then summarize the results under `## Verification` in the PR description and link each result comment. A merge-time run from [pr-work](../harden-pr/pr-work.md#finish-or-merge) needs no description update. When no test ran, say so there and give the reason. Describe runs on uncommitted changes there too, marked as uncommitted. Confirm the results are visible on the PR before finishing.

When the user limited publication, such as to the conversation only, report results there instead.
