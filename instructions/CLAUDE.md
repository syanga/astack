## Claude Code preferences

* When a background watcher is your only wake-up, make it exit on every terminal state of the watched process, including the process exiting. For a local process, start it in a separate foreground Bash call, such as `(cmd; echo $? >exitcode) >log 2>&1 & echo $! >pidfile`. Have the watcher test the PID in `pidfile` with `kill -0`, then read the exit code from `exitcode`. Monitor's expiry kills processes started inside the watcher.
* Give every watcher a deadline. The Bash tool's `timeout` parameter does not stop a `run_in_background` command. For a deadline of up to 30 minutes, use Monitor and set `timeout_ms` to the deadline. For a longer deadline, use a Bash `run_in_background` loop that exits when the shell's `SECONDS` passes the deadline.
* Make the watcher run its first check before its first sleep and print the result to stderr. Before you end the turn, read the watcher's output file and confirm the first check ran without errors.
* When you replace a watcher or no longer need it, stop it with TaskStop.
