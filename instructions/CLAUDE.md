## Claude Code preferences

* Run a long job or a watcher as a Bash `run_in_background` command through the `job-deadline` skill's helper. Claude Code wakes you when the command exits. The Bash tool's `timeout` parameter does not stop a background command, so the helper's deadline is what ends a hang.
* Start a long-lived process, such as a dev server, with plain `run_in_background`, so its exit wakes you if it crashes.
