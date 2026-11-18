# jobgate reference

`jobgate` admits heavy commands, such as test suites, builds, and renders, so
that concurrent runs from many repositories and agent lanes fit one machine. It
is one Python script, [scripts/jobgate.py](../scripts/jobgate.py), that runs on
macOS and Linux with Python 3.9 or newer. Before Python 3.11, it reads TOML with
the copy of the standard library's `tomllib` in `scripts/_vendor/`. Runs
coordinate through lock files, with no daemon. To gate a repository's commands,
see [Gate a repository's commands with jobgate](jobgate-setup.md).

## Commands

```sh
jobgate run [--max-workers N] PROFILE -- COMMAND [ARG...]
jobgate status
```

`jobgate run` reads `PROFILE` from the `jobgate.toml` at the root of the Git
repository that holds the current directory. Outside a Git repository, it reads
`jobgate.toml` in the current directory. It waits until the run fits, sets the
profile's `workers_env` variable to the worker count, runs the command, and
prints a result line. `--max-workers N` lowers the profile's `max_workers` for
one run. A value above `max_workers` has no effect.

`jobgate status` prints the share, the stated memory and workers of running
runs, the pressure reading, the kernel memory cap, and one line per running or
waiting run.

## Repository file

A repository's `jobgate.toml` holds one table per profile. Each profile needs
all four keys, and jobgate refuses unknown keys.

```toml
[test]
base_mem = "1G"
worker_mem = "600M"
max_workers = 8
workers_env = "PYTEST_XDIST_AUTO_NUM_WORKERS"
```

| Key | Value |
| --- | --- |
| `base_mem` | Memory a run needs apart from its workers, as a string with a `K`, `M`, `G`, or `T` suffix. Suffixes are powers of 1024, and `MiB` or `MB` spell the same unit as `M`. |
| `worker_mem` | Memory each worker adds, in the same format. |
| `max_workers` | The most workers a run takes, a whole number of at least 1. |
| `workers_env` | The environment variable through which jobgate passes the worker count to the command. It cannot be `JOBGATE_RUN` or `JOBGATE_WORKERS`, which jobgate sets itself. |

A run's stated memory is `base_mem + workers × worker_mem`. jobgate never
learns these numbers from past runs.

## Machine file

The share is the memory that all gated runs with the same `HOME` may state
together. It is `memory_share` times the machine's physical memory. `memory_share` comes
from `~/.config/jobgate.toml`:

```toml
memory_share = 0.6
```

`memory_share` is a number above 0 and at most 1. Without the file, it is 0.5.
Inside a container, jobgate reads the host's physical memory, not the
container's memory limit.

## Admission

A run is admitted when all of these hold:

- No earlier run waits. Runs start in arrival order, so a small run waits behind
  a large one that arrived first.
- At least one worker fits. The stated memory of all running runs, plus this
  run's `base_mem + 1 × worker_mem`, is at most the share. The workers of all
  running runs, plus one, are at most the core count.
- The OS reports no memory pressure, under the thresholds below. Runs that
  already started keep going under pressure.

An admitted run takes as many workers as fit, up to `max_workers` or
`--max-workers`, and keeps that count until it ends. The core count is the
process's CPU affinity on Linux and the logical CPU count on macOS. The queue
lives under `HOME`, so the share and the core limit apply to all runs with the
same `HOME`.

When a run's `base_mem + 1 × worker_mem` exceeds the share, jobgate exits with
code 125 instead of waiting.

A waiting run has no time limit, so only the caller's own timeout ends it. It
prints its reason when it starts waiting and once a minute after that:

```text
jobgate: test waits for memory: 1 worker needs 2.0G, and 1.0G of the 8.0G share is free; waited 1m 00s
jobgate: test waits for a core: gated workers use all 16 cores; waited 0s
jobgate: test waits for memory pressure to ease: full avg10=23.40% (holds at 10%); waited 2m 00s
jobgate: test waits behind 2 earlier runs; waited 0s
```

### Nested calls

A command that runs under jobgate and calls `jobgate run` again runs inside the
outer run's reservation and never waits. The nested command gets the smaller of
the outer run's worker count and its own profile's limit, and prints no result
line of its own. jobgate marks the outer run with `JOBGATE_RUN` and
`JOBGATE_WORKERS`. When the run that `JOBGATE_RUN` names has ended, a nested
call goes through admission like any other run.

### Pressure thresholds

| System | Reading | New runs wait while |
| --- | --- | --- |
| Linux | `full avg10` in `/proc/pressure/memory` | `full avg10` is 10% or more |
| macOS | `sysctl kern.memorystatus_vm_pressure_level` | the level is 4, critical |

jobgate ignores the Linux `some` line and the macOS warn level, 2. When the
reading is unavailable, such as on a kernel without PSI, no run waits for
pressure, and `jobgate status` shows the reading as unknown.
[Why jobgate holds at these pressure levels](jobgate-thresholds.md) gives the
evidence for both thresholds.

## Enforcement and priority

On Linux with a systemd user session, all gated runs run in `jobgate.slice`.
Each run sets the slice's `MemoryMax` to the share and its `CPUWeight` to 30,
the weight systemd gives its own `background.slice`, through
`systemctl --user set-property --runtime`. Each run is a scope,
`jobgate-<run id>.scope`, inside that slice. There is no per-run cap, so one run
can exceed its stated memory and keep running while all runs together fit the
slice. On systemd 253 or newer, each scope has `OOMPolicy=continue`, so a kernel
OOM kill inside the slice ends one process instead of the whole run.
`systemctl --user show jobgate.slice -p MemoryMax -p CPUWeight -p MemoryCurrent`
prints the slice's settings and use.

On Linux without a systemd user session, such as in containers and some WSL
setups, jobgate prints one line saying the kernel memory cap is off, then
applies admission and the pressure hold without it. It does the same when the
run's scope has no memory controller, as on hosts that still use cgroup v1.

macOS has no cgroups, so it has no kernel memory cap. A run can use more memory
than it states, and only the pressure hold responds, by delaying new runs.
jobgate starts the command under `taskpolicy -c utility`, which clamps the
command and every process it starts to the utility QoS class for the whole run.
On a free Mac, a utility run is as fast as a normal one. Next to the user's own
work, it yields CPU time to that work. A running process cannot leave the clamp.
When `/usr/sbin/taskpolicy` is missing, jobgate prints one line and runs the
command without the clamp.
[Why jobgate runs macOS commands at utility QoS](jobgate-macos-priority.md)
gives the measurements.

jobgate passes SIGTERM and SIGHUP on to the command and waits for it. Ctrl-C
reaches the command from the terminal, and jobgate waits for the command to
exit.

When jobgate alone is killed with SIGKILL, the OS releases its reservation, and
its command keeps running. Later runs are admitted as if that command had
ended. On Linux with the kernel memory cap on, the command stays in
`jobgate.slice`, so `MemoryMax` still caps all gated runs together. On macOS, or
on Linux with the kernel memory cap off, only the pressure hold protects new
runs from the memory that command still uses.

## Results

jobgate measures each run's peak memory:

- On Linux in a scope, it reads the scope's `memory.peak`. On kernels before
  5.19, which lack `memory.peak`, it samples `memory.current` every 2 seconds.
- On macOS, it adds up the `phys_footprint` of each process in the command's
  tree every 2 seconds, read through `proc_pid_rusage`. That is the memory
  Activity Monitor shows, and it includes compressed memory.
- On Linux without a scope, it adds up the resident memory of the command's
  process tree every 2 seconds.

The macOS and Linux sums count shared pages once per process. They skip a
process they cannot read, and they miss processes that leave the tree.

When the peak exceeds the stated memory, jobgate prints a warning with the
`worker_mem` value that would have covered the peak:

```text
jobgate: peak 5.2G is above the 4.0G that [test] states for 4 workers; raise worker_mem to "1076M" in /src/app/jobgate.toml
```

At the end of each run, jobgate prints one result line and appends it to
`~/.cache/jobgate/results.log`:

```text
time=2026-11-17T18:00:00Z profile=test where=local repo=/src/app commit=4f0c2d9e1b7a5c3e8d6f4a2b0c9e7d5f3a1b8c6e dirty=yes workers=4 wait=12s peak=3172M stated=4096M exit=0
```

| Field | Value |
| --- | --- |
| `time` | When the run ended, in UTC |
| `profile` | The profile name |
| `where` | Where the run ran: `local` |
| `repo` | The directory that holds `jobgate.toml` |
| `commit` | `git rev-parse HEAD` when the run started, or `none` |
| `dirty` | `yes` when `git status --porcelain` printed anything when the run started, `no` when it printed nothing, `unknown` outside Git |
| `workers` | The worker count |
| `wait` | Seconds from arrival to start |
| `peak` | Peak memory in MiB, or `unknown` when jobgate could not measure it, such as for a command that ended before the first sample |
| `stated` | `base_mem + workers × worker_mem` in MiB |
| `exit` | The exit code jobgate returns |

Values that contain spaces, quotes, or `=` are JSON strings. jobgate runs dirty
worktrees like clean ones.

## Exit codes

| Code | Meaning |
| --- | --- |
| The command's code | The command exited. A command killed by signal N gives 128+N. |
| 125 | jobgate refused the run: a usage error, a configuration error, or a run that can never fit. |
| 126 | The command is not executable. |
| 127 | The command was not found. |
| 128+N | jobgate received signal N while the run waited or before its command started, such as 130 for Ctrl-C. |

On macOS, `taskpolicy` starts the command. When it cannot start an executable
file, such as a script whose interpreter is missing, jobgate returns the exit
code of `taskpolicy`.

## Environment

| Variable | Use |
| --- | --- |
| The profile's `workers_env` | jobgate sets it to the worker count. |
| `JOBGATE_WORKERS` | jobgate sets it to the worker count. |
| `JOBGATE_RUN` | jobgate sets it to the path of the run's file. A nested call reads it to find the outer run. |
| `HOME` | Holds `.cache/jobgate/` and `.config/jobgate.toml`. jobgate ignores `XDG_CACHE_HOME` and `XDG_CONFIG_HOME`. Processes with the same `HOME` use one queue, and a process with another `HOME` uses a separate queue. |
| `JOBGATE_MACHINE_FILE` | Tests only. A JSON file with `memory`, `cores`, and `psi` or `darwin_level` readings that replace the OS readings. jobgate reads `memory` and `cores` once per run, and the pressure reading before each admission attempt. |

## Files

| Path | Content |
| --- | --- |
| `~/.cache/jobgate/queue.lock` | The lock that runs hold while they read or change the queue |
| `~/.cache/jobgate/runs/<run id>.json` | One file per waiting or running run, which the run holds a `flock` on. The OS releases the lock when the run's process dies. The next run that reads the queue then deletes the file. |
| `~/.cache/jobgate/runs/<run id>.scope` | The scope's cgroup and peak memory, which the scope writes before it ends |
| `~/.cache/jobgate/results.log` | Result lines |

## Install and remove

`./install.sh` installs jobgate by default, as the `jobgate` target. The
installer needs Python 3.10 or newer as `python3`, while jobgate itself runs on
Python 3.9. A stock Mac's `python3` is 3.9, which cannot run the installer.
The installer writes `~/.local/bin/jobgate`, a shell wrapper that runs `python3`
on the script in `~/.local/share/astack/jobgate/`, or in
`$XDG_DATA_HOME/astack/jobgate/` when `XDG_DATA_HOME` is set. When
`~/.local/bin` is not on `PATH` and no `PATH` assignment in the shell startup
file names it, the installer appends a `PATH` line to that file.
`./uninstall.sh` removes those files and that line. When the uninstaller cannot
rewrite the startup file, it keeps a record of the line, and the next uninstall
removes the line. Uninstall leaves `~/.cache/jobgate/`, the machine file, and the runtime
slice settings, which last until reboot.
