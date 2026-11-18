# jobgate reference

`jobgate` admits heavy commands, such as test suites, builds, and renders, so
that concurrent runs from many repositories and agent lanes fit one machine. It
is one Python script, [scripts/jobgate.py](../scripts/jobgate.py), that runs on
macOS and Linux with Python 3.9 or newer and no other dependencies. Runs
coordinate through lock files, with no daemon. To gate a repository's commands,
see [Gate a repository's commands with jobgate](jobgate-setup.md).

## Commands

```sh
jobgate run [--max-workers N] PROFILE -- COMMAND [ARG...]
jobgate status
```

`jobgate run` reads `PROFILE` from the nearest `jobgate.toml` in the current
directory or its parents. It waits until the run fits, sets the profile's
`workers_env` variable to the worker count, runs the command, and prints a
result line. `--max-workers N` lowers the profile's `max_workers` for one run.
A value above `max_workers` has no effect.

`jobgate status` prints the share, the memory and cores that running runs
state, the pressure reading, the kernel cap, and one line per running or
waiting run.

## Repository file

A repository's `jobgate.toml`, at its root, holds one table per profile. Each
profile needs all four keys, and jobgate refuses unknown keys.

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
| `workers_env` | The environment variable through which jobgate passes the worker count to the command. |

A run's stated memory is `base_mem + workers × worker_mem`. jobgate never
learns these numbers from past runs.

## Machine file

`~/.config/jobgate.toml`, or `$XDG_CONFIG_HOME/jobgate.toml`, overrides the
share of physical memory that gated runs get. The share is per user.

```toml
memory_share = 0.6
```

`memory_share` is a number above 0 and at most 1. Without the file, the share
is 0.5. Inside a container, physical memory is the host's, so set a share that
fits the container's memory limit.

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
process's CPU affinity on Linux and the logical CPU count on macOS.

A run whose single worker exceeds the share can never start, so jobgate refuses
it with exit code 125 instead of waiting.

A waiting run has no time limit. It prints its reason when it starts waiting
and once a minute after that, such as:

```text
jobgate: test waits for memory: 1 worker needs 2.0G, and 1.0G of the 8.0G share is free; waited 1m 00s
jobgate: test waits for a core: gated workers use all 16 cores; waited 0s
jobgate: test waits for memory pressure to ease: full avg10=23.40% (holds at 10%); waited 2m 00s
jobgate: test waits behind 2 earlier runs; waited 0s
```

To bound the wait, use the caller's own timeout.

### Nested calls

A command that runs under jobgate and calls `jobgate run` again runs inside the
outer run's reservation and never waits. The nested command gets the smaller of
the outer run's worker count and its own profile's limit. jobgate marks the
outer run with `JOBGATE_RUN` and `JOBGATE_WORKERS`. When the run that
`JOBGATE_RUN` names has ended, a nested call goes through admission like any
other run.

### Pressure thresholds

| System | Reading | New runs wait while |
| --- | --- | --- |
| Linux | `full avg10` in `/proc/pressure/memory` | `full avg10` is 10% or more |
| macOS | `sysctl kern.memorystatus_vm_pressure_level` | the level is 4, critical |

When the reading is unavailable, such as on a kernel without PSI, nothing
holds runs, and `jobgate status` shows the reading as unknown.

The Linux threshold comes from these facts:

- `full` is the share of time in which all non-idle tasks wait on memory at
  once, and the kernel calls a workload that stays there thrashing
  ([PSI documentation](https://docs.kernel.org/accounting/psi.html)). `some`
  counts any single stalled task, so page cache reclaim on a busy machine
  raises it. jobgate ignores `some`.
- systemd-oomd kills a cgroup after its `full avg10` stays above 60% for 30
  seconds by default
  ([oomd.conf](https://www.freedesktop.org/software/systemd/man/latest/oomd.conf.html)).
  Android's lmkd kills at 700 ms of full stall per second
  ([lmkd](https://source.android.com/docs/core/perf/lmkd)). A hold at 10% acts
  well before either.
- On a 16-core, 22 GiB Ubuntu 26.04 machine running several agent lanes, 643
  samples over 21 minutes, with the load average up to 27.5 and `MemAvailable`
  down to 542 MiB, peaked at `full avg10=1.49`. A hold at 10% leaves that
  machine room.

The macOS threshold comes from XNU's
[memorystatus_notify.md](https://github.com/apple-oss-distributions/xnu/blob/main/doc/vm/memorystatus_notify.md).
The level turns to warn, 2, when uncompressed available memory falls below half
of all available memory, which is routine on a Mac that compresses memory. It
turns critical, 4, below 29%, where the compressor starts swapping. Holding at
warn would keep a busy but healthy Mac from starting runs for hours.

## Enforcement and priority

On Linux with a systemd user session, all gated runs share `jobgate.slice`.
Each run sets the slice's `MemoryMax` to the share and its `CPUWeight` to 30,
the weight systemd gives its own `background.slice`, through
`systemctl --user set-property --runtime`. Each run is a scope,
`jobgate-<run id>.scope`, inside that slice. There is no per-run cap, so one run
can exceed its stated memory and keep running while all runs together fit the
slice. On systemd 253 or newer, the scope has `OOMPolicy=continue`, so a kernel
OOM kill inside the slice ends one process instead of the whole run. Check the
slice with:

```sh
systemctl --user show jobgate.slice -p MemoryMax -p CPUWeight -p MemoryCurrent
```

On Linux without a systemd user session, such as in containers and some WSL
setups, jobgate prints one line saying the kernel memory cap is off, then
applies admission and the pressure hold without it.

macOS has no cgroups, so it has no kernel cap, and the pressure hold is the
backstop. A run's processes get background priority, `PRIO_DARWIN_BG`, while
someone uses the Mac. After 2 minutes without keyboard or mouse input, read
from `ioreg`'s `HIDIdleTime`, they get normal priority. jobgate checks every 2
seconds and prints a line when the priority changes.

jobgate passes SIGTERM and SIGHUP on to the command and waits for it. Ctrl-C
reaches the command from the terminal, and jobgate waits for the command to
exit.

## Results

jobgate measures each run's peak memory:

- On Linux in a scope, it reads the scope's `memory.peak`. On kernels before
  5.19, which lack `memory.peak`, it samples `memory.current` every 2 seconds.
- Elsewhere, it adds up the resident memory of the command's process tree every
  2 seconds. The sum counts shared pages once per process, and it misses
  processes that leave the tree.

When the peak exceeds the stated memory, jobgate prints a warning with the
`worker_mem` value that would have covered the peak:

```text
jobgate: peak 5.2G is above the 4.0G that [test] states for 4 workers; raise worker_mem to "1076M" in /src/app/jobgate.toml
```

At the end of each run, jobgate prints one result line and appends it to
`~/.cache/jobgate/results.log`, or `$XDG_CACHE_HOME/jobgate/results.log`:

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
| 128+N | jobgate received signal N while the run waited, such as 130 for Ctrl-C. |

## Environment

| Variable | Use |
| --- | --- |
| The profile's `workers_env` | jobgate sets it to the worker count. |
| `JOBGATE_WORKERS` | jobgate sets it to the worker count. |
| `JOBGATE_RUN` | jobgate sets it to the run id, which marks nested calls. |
| `XDG_CACHE_HOME` | Holds `jobgate/`, when set to an absolute path. |
| `XDG_CONFIG_HOME` | Holds the machine file, when set to an absolute path. |
| `JOBGATE_MACHINE_FILE` | Tests only. A JSON file with `memory`, `cores`, and `psi` or `darwin_level` readings that replace the OS readings. jobgate rereads it on every check. |

## Files

| Path | Content |
| --- | --- |
| `~/.cache/jobgate/queue.lock` | The lock that runs hold while they read or change the queue |
| `~/.cache/jobgate/runs/<run id>.json` | One file per waiting or running run. The run holds a `flock` on it, so the OS releases it when the run's process dies, and the next reader deletes it. |
| `~/.cache/jobgate/runs/<run id>.scope` | The scope's cgroup and peak memory, which the scope writes before it ends |
| `~/.cache/jobgate/results.log` | Result lines |

## Install and remove

`./install.sh` installs jobgate by default, as the `jobgate` target. It writes
`~/.local/bin/jobgate`, a shell wrapper that runs `python3` on the script in
`~/.local/share/astack/jobgate/`. When `~/.local/bin` is not on `PATH`, it adds
a `PATH` line to the shell startup file. `./uninstall.sh` removes those files
and that line. It leaves `~/.cache/jobgate/`, the machine file, and the
runtime slice settings, which last until reboot.
