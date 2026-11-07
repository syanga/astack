# Run and share PR tests

The base branch is the PR's target, which is the parent branch for a stack child. Fetch it before any check below. The change is the file list from `git diff --name-only --no-renames origin/<base branch>...HEAD`. The covering suites are the test suites that cover the change, chosen under [Pick the suites](#pick-the-suites). A file affects a covering suite when it is in that suite's component or in a component the suite depends on, directly or transitively. A file affects every covering suite when it is repository-wide build or test configuration, or code whose dependents you cannot trace. A result's base commit is the base it records.

## Reuse a published result

Before running tests, list the PR's published results with `python3 "<harden-pr directory>/scripts/pr.py" tests --pr <n>`. It drops results from accounts without write access and counts them as `ignored`. Its `latest_by_suite` gives each suite's newest result with its tested and base commits. Check whether the results still cover the current code and required checks. List the files changed since a result ran:

- On the branch: `git diff --name-only --no-renames <tested commit> HEAD`
- On the base, when HEAD does not contain `origin/<base branch>`: `git diff --name-only --no-renames <result's base commit> origin/<base branch>`

A covering suite keeps its result when that result includes it and no listed file affects it. Run the other covering suites after [Rebase before the published run](#rebase-before-the-published-run). When the tested commit cannot be fetched, rebuild it by merging the result's `heads` onto its base. When it lists no `heads`, run every covering suite.

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
  "base": "<full SHA of the result's base commit>",
  "command": "<the command you ran>",
  "suites": {"packages/api": "pass", "packages/web": "fail"},
  "summary": "<counts, including failures and skipped tests>",
  "scope": "<how you scoped the run and why>",
  "limits": "<coverage limits, when any>"
}
```

- Name each suite by the path or package its command runs, so later runs match it.
- State the scope and why, such as `packages/api` and its dependents because no shared configuration changed.
- Mark a covering suite that could not run as `incomplete`. It is a coverage limit, not a pass.
- Omit `limits` when there are none.
- When the PR head moved and a suite kept its result, post that result for the new head with its original tested commit. Post rerun suites as a separate result.
- When the tested commit is a scratch merge, as for a batch, add `"heads"` with every head SHA merged onto the base.

The status on a commit follows the newest result for each suite on that commit. When `post-tests` reports `"trusted": false`, your account lacks write access, so other agents will ignore the result. Report that.

Publish failures too. Then fix the failing suite within the task's scope and publish the new result, or report the failure.

Then summarize the results under `## Verification` in the PR description and link each result comment. When no test ran, say so there and give the reason. Describe runs on uncommitted changes there too, marked as uncommitted. If the helper fails, put the full result in the description and report the failure. Confirm the results are visible on the PR before finishing.
