## Claude Code preferences

* Run a long job itself with Bash `run_in_background`. The harness wakes you when the job exits, whether it succeeds or fails, so the job needs no separate watcher.
* When you wait for a condition instead, such as a ready line or a CI result, make the watcher exit on failure as well as success, and give it a deadline. The Bash tool's `timeout` parameter does not stop a `run_in_background` command. Use Monitor with `timeout_ms` for deadlines up to 30 minutes, or a loop that exits when `$SECONDS` passes the deadline.
* Have the watcher print its first check before it sleeps, and read that output before you end the turn. Stop a watcher you no longer need with TaskStop.
