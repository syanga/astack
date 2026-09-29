## Claude Code preferences

* Run every Bash `run_in_background` command through the helper in the `run-job` skill from the installed skills. Give a finite job or a watcher `--timeout` well past its normal duration, and give a long-lived process `--no-timeout`. Read the `run-job` skill before you start a watcher. A hook denies other background commands.
* Before you end the turn with a watcher running, read its first check and confirm it ran without errors.
* When you replace a background job or watcher or no longer need it, stop it with TaskStop.
