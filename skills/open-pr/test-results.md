# Run and share PR tests

Before running tests, check the PR for results that still cover the current code and required checks.

Run the covering suites, which are the suites that cover the change. Run each through its own command or the test runner's filter. Map each suite to the code it covers using the repository's instructions, CI configuration, and build files. In a repository with one suite, run it whole. In a repository of independent components with their own suites, run the suite of each changed component and of each component that depends on it, directly or transitively.

Run the full suite when no command or filter selects the covering suites. Also run it when the change touches any of these:

- code that no component's suite covers
- repository-wide build or test configuration
- code whose dependents you cannot trace

Run the other checks that repository instructions require, such as lint or typecheck. When the instructions name a repository-wide test command, scope it to the covering suites. When the instructions explicitly require a broader run, such as the full suite before every PR, run it.

When you or a delegated reviewer runs tests, publish the command, tested commit, outcome, and relevant coverage limits in the PR description. State how you scoped the run and why, such as `packages/api` and its dependents because no shared configuration changed. When no test ran, state that and why. A covering suite that could not run is a coverage limit, not a pass. Include failures and skipped tests. Distinguish results from uncommitted changes. Confirm the results are visible on the PR before finishing.
