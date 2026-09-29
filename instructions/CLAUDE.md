## Claude Code preferences

* Run a job that may outlast the Bash tool's foreground timeout, or a watcher, as a Bash `run_in_background` command through the helper in the `job-deadline` skill from the installed skills. The Bash tool's `timeout` parameter does not stop a background command.
* When you replace a background job or watcher or no longer need it, stop it with TaskStop.
