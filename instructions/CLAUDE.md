## Claude Code preferences

* Run a long finite job, such as a slow test suite or build, as the Bash `run_in_background` command. Claude Code wakes you when it exits, whether it succeeds or fails, so it needs no separate watcher.
* To wait for a condition, such as a ready line or a CI result, still start any local process with `run_in_background`, and have the watcher only read its output. Make the watcher exit on every outcome, not only success, and give it a deadline. The Bash tool's `timeout` parameter does not stop a `run_in_background` command. Use Monitor with `timeout_ms` for deadlines up to 30 minutes, or a Bash `run_in_background` loop that exits when `$SECONDS` passes the deadline.
* Have the watcher print its first check to stderr before it sleeps, and confirm in its output file that the check ran without errors before you end the turn. Stop a watcher you replace or no longer need with TaskStop.
