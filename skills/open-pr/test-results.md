# Run and share PR tests

The base branch is the PR's target, which is the parent branch for a stack child. Fetch it before any check below. The change is the file list from `git diff --name-only --no-renames origin/<base branch>...HEAD`. The covering suites are the test suites that cover the change, chosen under [Pick the suites](#pick-the-suites) from the build files on both HEAD and `origin/<base branch>`, so new dependents on the base count. A file affects a covering suite when it is in that suite's component, in a component the suite depends on, or in repository-wide build or test configuration. A result's base commit is `git merge-base <tested commit> origin/<base branch>`.

## Reuse a published result

Before running tests, check the PR for results that still cover the current code and required checks. List the files changed since a result ran:

- `git diff --name-only --no-renames <tested commit> HEAD`
- when HEAD does not contain `origin/<base branch>`, also `git diff --name-only --no-renames <result's base commit> origin/<base branch>`

A covering suite keeps its result when that result includes it and no listed file affects it. Run the other covering suites after the sync below. When the tested commit cannot be fetched, run every covering suite.

## Sync before the published run

open-pr rebases a new PR before its test step. Before a run you publish on an existing PR, list the files the base changed with `git diff --name-only --no-renames $(git merge-base HEAD origin/<base branch>) origin/<base branch>`. Rebase onto `origin/<base branch>` when any of them affects a covering suite. Follow [pr-work's push rules](../harden-pr/pr-work.md#push-and-wait) for that rebase. When a brief forbids rebasing, run on the current branch and say so in the published result.

## Pick the suites

Run the covering suites on the code you push. Run each through its own command or the test runner's filter. Map each suite to the code it covers using the repository's instructions, CI configuration, and build files. In a repository with one suite, run it whole. In a repository of independent components with their own suites, run the suite of each changed component. Also run the suite of each component that depends on a changed component, directly or transitively.

Run the full suite when no command or filter selects the covering suites. Also run it when the change touches any of these:

- code that no component's suite covers
- repository-wide build or test configuration
- code whose dependents you cannot trace

Run the other checks that repository instructions require, such as lint or typecheck. When the instructions name a repository-wide test command, scope it to the covering suites. When the instructions explicitly require a broader run, such as the full suite before every PR, run it.

## Publish the result

When you or a delegated reviewer runs tests, publish the command, tested commit, base commit, outcome, and relevant coverage limits in the PR description. After a partial rerun, publish each group of suites as its own result. State how you scoped the run and why, such as `packages/api` and its dependents because no shared configuration changed. When no test ran, say so and give the reason. A covering suite that could not run is a coverage limit, not a pass. Include failures and skipped tests. Distinguish results from uncommitted changes. Confirm the results are visible on the PR before finishing.
