# jobgate reference

`jobgate` admits heavy commands, such as test suites, builds, and renders. It
makes concurrent runs from many repositories and agent lanes fit one machine. It
is one Python script, [scripts/jobgate.py](../scripts/jobgate.py), that runs on
macOS and Linux with Python 3.9 or newer. Before Python 3.11, it reads TOML with
the copy of the standard library's `tomllib` in `scripts/_vendor/`. Runs
coordinate through lock files, with no daemon. To gate a repository's commands,
see [Gate a repository's commands with jobgate](jobgate-setup.md).

## Commands

```sh
jobgate run [--max-workers N] [--local] PROFILE -- COMMAND [ARG...]
jobgate status
```

`jobgate run` reads `PROFILE` from the `jobgate.toml` at the root of the Git
repository that contains the current directory. Outside a Git repository, it
reads `jobgate.toml` in the current directory. It waits until the run fits, sets
the profile's `workers_env` variable to the worker count, and runs the command.
Then it prints a result line. `--max-workers N` lowers the profile's
`max_workers` for one run. A value above `max_workers` has no effect.

When `jobgate.toml` has a `[boat]` table, `jobgate run` runs the command on a
boat box instead, as [Boat](#boat) describes. `--local` runs it on this machine.

`jobgate status` prints six lines first. They give the share, the stated memory
of running runs, free memory, the workers that running runs use, the pressure
reading, and the kernel memory cap. The free memory line gives the available
memory and what running runs may still use. Then it prints one line per running
or waiting run. A running run's line gives the memory it uses, and a waiting
run's line gives why it waits.

## Repository file

A repository's `jobgate.toml` contains one table per profile. Each profile needs
all four keys, and jobgate refuses unknown keys. The `[boat]` table is not a
profile. [Boat](#boat) describes it.

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

A run's stated memory is `base_mem + workers × worker_mem`. jobgate never learns
these numbers from past runs.

## Machine settings

The share is the memory that all of a user's gated runs may state together,
under every `HOME`. It is `memory_share` times the machine's physical memory.
`memory_share` comes from `~/.config/jobgate.toml`, where `~` is the account's
home directory from the password database:

```toml
memory_share = 0.6
```

`memory_share` is a number above 0 and at most 1. Without the file, it is 0.5.
jobgate reads `memory_share` when a run arrives, about once a second while the
run waits, and before it sets the kernel memory cap. A change therefore also
applies to runs that already wait. jobgate exits with code 125 when the file is
invalid at arrival. When the file turns invalid while a run waits, jobgate
prints one warning and keeps the last valid value. A run that starts while the
file is invalid leaves the kernel memory cap as it is. Inside a container,
jobgate reads the host's physical memory, not the container's memory limit.

## Admission

A run is admitted when all of these are true:

- No earlier run waits. Runs start in arrival order, so a small run waits behind
  a large one that arrived first.
- At least one worker fits. The stated memory of all running runs, plus this
  run's `base_mem + 1 × worker_mem`, is at most the share. The workers of all
  running runs, plus one, are at most the core count.
- At least one worker fits in free memory. This run's
  `base_mem + 1 × worker_mem` is at most the free memory that
  [Free memory](#free-memory) defines.
- The pressure reading is under its threshold in
  [Pressure thresholds](#pressure-thresholds). While it is not, new runs wait.
  This wait is the pressure hold. Runs that already started keep going under
  pressure.

An admitted run takes as many workers as fit the share, free memory, and the
free cores, up to `max_workers` or `--max-workers`. It keeps that count until it
ends. Its stated memory and its workers are its reservation until then. The core
count is the process's CPU affinity on Linux and the logical CPU count on macOS.

All of a user's runs share one queue, one share, and the cores, under every
`HOME`.

When a run's `base_mem + 1 × worker_mem` exceeds the share, jobgate exits with
code 125 instead of waiting. jobgate makes the same check at arrival and about
once a second while the run waits. A waiting run therefore also exits when the
share drops below its need.

A waiting run has no time limit. The caller's own timeout ends the wait, and so
do SIGINT from Ctrl-C, SIGTERM, and SIGHUP. A waiting run prints its reason when
it starts waiting and once a minute after that:

```text
jobgate: test waits for memory: 1 worker needs 1.6G, and 1.0G of the 8.0G share is free; waited 1m 00s
jobgate: test waits for free memory: 1 worker needs 1.6G, and the machine has 1.2G available, of which running runs may still use 512M; waited 0s
jobgate: test waits for a core: gated workers use all 16 cores; waited 0s
jobgate: test waits for memory pressure to ease: full avg10=23.40% (holds at 10%); waited 2m 00s
jobgate: test waits behind 2 earlier runs; waited 0s
```

### Nested calls

A command that runs under jobgate and calls `jobgate run` again runs inside the
outer run's reservation and never waits. The nested call gets the smaller of the
outer run's worker count and its own profile's limit. It prints no result line
of its own. jobgate marks the outer run with `JOBGATE_RUN` and
`JOBGATE_WORKERS`. When the run that `JOBGATE_RUN` names has ended, a nested
call goes through admission like any other run.

### Free memory

| System | Available memory |
| --- | --- |
| Linux | `MemAvailable` in `/proc/meminfo`, the kernel's estimate of the memory that new work can get without swapping |
| macOS | The free, file-backed, and purgeable pages that `vm_stat` reports, times its page size |

A new process can get the memory in the macOS sum without compressing or
swapping memory in use. jobgate does not read `kern.memorystatus_level`, which
XNU computes from the active, inactive, free, and speculative pages. That level
counts memory in active use as available.

At each admission attempt, jobgate computes free memory: the available memory,
less S − M for each running run. S is the run's stated memory, and M is the
memory it uses. A run whose M exceeds S subtracts nothing. jobgate measures M
just before and just after it reads the available memory, and it keeps the
smaller measurement. A failed measurement counts as 0.

| Run | How jobgate measures M |
| --- | --- |
| Linux, in a scope | The scope's `memory.current`, less the page cache and reclaimable kernel memory that `memory.stat` lists as `active_file`, `inactive_file`, and `slab_reclaimable` |
| Linux, without a scope | The sum of `Pss_Anon` and `Pss_Shmem` from `/proc/<pid>/smaps_rollup` over the tree of the run's jobgate process. For a process that does not show those fields, jobgate uses `RssAnon` and `RssShmem` from `/proc/<pid>/status`. |
| macOS | The sum of `phys_footprint` over the tree of the run's jobgate process |

Memory that ungated commands use is missing from the available memory, so
ungated work also delays new runs.

Free memory is an estimate, read one file or process at a time. Runs that
allocate or free memory during the check, shared pages, and page cache that
grows or shrinks can each make it wrong in either direction. On macOS, M counts
compressed memory at its size before compression, so free memory can be too high
by what the compressor saves.

When the estimate is too high, jobgate can admit a run that does not fit. On
Linux with the kernel memory cap on, the `MemoryMax` of `jobgate.slice` still
caps all gated runs together.
[Enforcement and priority](#enforcement-and-priority) describes that cap.
Otherwise, as on macOS, only the pressure hold and the OS respond.

When the reading is unavailable, no run waits for free memory, and
`jobgate status` shows free memory as unknown.

### Pressure thresholds

| System | Reading | New runs wait while |
| --- | --- | --- |
| Linux | `full avg10` in `/proc/pressure/memory` | `full avg10` is 10% or more |
| macOS | `sysctl kern.memorystatus_vm_pressure_level` | the level is 4, critical |

jobgate ignores the Linux `some` line and the macOS warn level, 2. When the
reading is unavailable, such as on a kernel without pressure stall information
(PSI), no run waits for pressure. `jobgate status` then shows the reading as
unknown. [Why jobgate holds at these pressure levels](jobgate-thresholds.md)
gives the evidence for both thresholds.

## Enforcement and priority

On Linux with a systemd user session, all of a user's gated runs run in one
slice, `jobgate.slice`. Through `systemctl --user set-property --runtime`, each
run sets that slice's `MemoryMax` to the share. That `MemoryMax` is the kernel
memory cap. Each run also sets the slice to `CPUWeight=30`, the weight systemd
gives its own `background.slice`. Slices compete for CPU only against their
siblings. `jobgate.slice` and `app.slice` are siblings, and `app.slice` has
`CPUWeight=100`. When both are busy, gated runs therefore get less CPU time than
`app.slice`.

Each run is a scope, `jobgate-<run id>.scope`, inside `jobgate.slice`. There is
no per-run cap. One run can exceed its stated memory and keep running while all
of the user's runs together fit the cap. When they reach the cap on a machine
without swap, the kernel reclaims page cache and then OOM-kills a process in
`jobgate.slice`. Its victim can belong to any of the user's runs, not only to
the run that went over. With swap, the slice can swap first, because jobgate
does not set `MemorySwapMax`. On systemd 253 or newer, each scope has
`OOMPolicy=continue`. When the kernel OOM-kills a process inside
`jobgate.slice`, systemd does not stop the rest of that process's run. jobgate
exits with code 137 when the kernel kills either the command or the
`jobgate.py _scope` process that waits for it. The command, or processes that it
started, can keep running in the run's scope. On systemd 254 or newer, jobgate
passes `--expand-environment=no`, so the command receives `$` in its arguments
unchanged. Earlier versions never expand a scope's arguments. `jobgate status`
prints the `MemoryMax`, `MemoryCurrent`, and `CPUWeight` of `jobgate.slice`.

On Linux without a systemd user session, as in containers and some WSL setups,
jobgate prints one line saying the kernel memory cap is off. It then applies
admission and the pressure hold without the cap. It does the same when the run's
scope has no memory controller, as on hosts that still use cgroup v1.

macOS has no cgroups, so it has no kernel memory cap. A run can use more memory
than it states. The free-memory check and the pressure hold respond only by
delaying new runs. jobgate starts the command under `taskpolicy -c utility`,
which sets a quality-of-service (QoS) clamp. The clamp keeps the command and
every process it starts in the utility QoS class for the whole run. A running
process cannot leave the clamp. When `/usr/sbin/taskpolicy` is missing, jobgate
prints one line and runs the command without the clamp.
[Why jobgate runs macOS commands at utility QoS](jobgate-macos-priority.md)
gives the measurements.

jobgate passes SIGTERM and SIGHUP on to the command and waits for it. Ctrl-C
reaches the command from the terminal, and jobgate waits for the command to
exit.

When jobgate alone is killed with SIGKILL, the OS releases its reservation, and
its command keeps running. Later runs no longer count its reservation. On Linux
with the kernel memory cap on, the command stays in `jobgate.slice`, whose
`MemoryMax` still caps the command together with later runs. Its memory is still
missing from the available memory, so the free-memory check delays new runs
while the command uses memory. `jobgate status` no longer lists the command. On
Linux with the kernel memory cap on,
`systemctl --user list-units 'jobgate-*.scope'` lists the scope that still
contains it. Otherwise, find it by its command line, for example with
`pgrep -fl`.

## Boat

A repository whose `jobgate.toml` has a `[boat]` table runs `jobgate run` on a
box: a Linux virtual machine from [boat.dev](https://boat.dev). A boat run does
not go through admission on this machine, so it uses none of the share.
`jobgate run --local` runs the command on this machine. A nested call runs inside
its outer run, as [Nested calls](#nested-calls) describes.

```toml
[boat]
setup = "scripts/boat-setup.sh"
setup_inputs = ["requirements-dev.txt", "package-lock.json"]
exclude = ["documents"]
type = "large"
```

| Key | Value |
| --- | --- |
| `setup` | Required. The bash script that installs what the command needs on a new box. |
| `setup_inputs` | Files that the setup script reads, such as lockfiles. Default: none. |
| `exclude` | Files and directories whose contents never leave this machine. Default: none. |
| `type` | The box size: `small`, `default`, `large`, or `xlarge`. Default: `default`. |

Each path is relative to the repository root. A path cannot contain `*`, `?`,
`[`, `]`, `!`, `#`, `\`, a newline, or a NUL character, and cannot step outside
the repository. `exclude` cannot cover `jobgate.toml`, the `setup` script, or a
`setup_inputs` file, because the box needs them.

A boat run needs the `boat` CLI on `PATH`, signed in, and Git 2.31 or newer.
With `exclude`, it needs Git 2.36 or newer, which can filter a bundle.

### What the box gets

jobgate sends a Git bundle, which holds commits with their history. Commands
that read Git history therefore work on the box.

- With a clean worktree, the box checks out `HEAD`.
- With a dirty worktree, jobgate commits the worktree's tracked files and the
  untracked files that `.gitignore` does not hide. It makes this commit in a
  scratch repository, so the repository's index, refs, and objects do not
  change. The result line says `dirty=yes`.
- jobgate leaves out untracked files whose names match `.env`, `.env.*`,
  `*.env`, `.envrc`, `*~`, `*.pem`, `*.key`, `*.p12`, `*.pfx`, `.netrc`,
  `.npmrc`, `.pypirc`, `.yarnrc.yml`, `.pgpass`, `.git-credentials`,
  `credentials`, `credentials.json`, `application_default_credentials.json`,
  `*.tfvars`, `*.tfstate`, `*.tfstate.*`, `*.jks`, `*.keystore`, `*.ppk`,
  `id_rsa*`, `id_dsa*`, `id_ecdsa*`, or `id_ed25519*`. It prints their paths.
- With `exclude`, the bundle leaves out the contents of every file under the
  excluded paths, in every commit of the history. The box gets the file names,
  but its checkout leaves the files out. A Git command on the box that reads
  their contents fails.
- Before it starts a box, jobgate also looks for copies of private content at
  other paths, in the history and in the uncommitted changes that the box
  would get. Private content is what any commit ever had under an excluded
  path, and every file under an excluded path now. That includes files that
  `.gitignore` hides and files that a symbolic link leads to. Examples are a
  file that a commit moved into an excluded directory, and an untracked copy of
  an excluded file. jobgate reads each file under the excluded paths on every
  run.
  If it finds a copy, the command runs on this machine. jobgate prints up to 5
  such paths to add to `exclude`, and the number of the others. It names one
  path for each such content. After you add those paths, the next run can name
  another path with the same content. Empty files and submodules do not count.
  When jobgate cannot read a file or directory under an excluded path, the
  command also runs on this machine.
- The copy check is a best effort. It finds a copy only in one of three forms:
  the same bytes, the bytes with each CRLF line ending turned into LF, or the
  bytes with each LF turned into CRLF. Git's line-ending conversion stores and
  checks out files in these forms. A copy in any other form reaches the box.
  Examples are an edited copy, and a copy that `working-tree-encoding` or a
  clean filter changes.
- When the box starts from a snapshot, the bundle holds only the commits that
  the snapshot lacks.
- The box checks out no submodules. A submodule's directory is empty on the
  box.

The box gets none of this machine's environment variables, and none of the
secrets that the boat account stores, because jobgate creates it with
`boat new --no-env`. Entries of this machine's `PATH` that are inside the
repository, such as `node_modules/.bin`, go to the front of the box's `PATH`.
The command starts in the same subdirectory of the checkout as here. A command
that names an absolute path on this machine fails on the box. Files that the
command writes stay on the box. To keep output files, such as a coverage
report, run with `--local`.

### A boat run, step by step

1. jobgate creates a box with
   `boat new --type <type> --no-env --fail-fast --ttl 3600`, from the
   repository's snapshot when one exists.
2. It prints the size of the upload. Then it copies the bundle, its own
   `jobgate.py`, and the run's settings to the box with `boat scp`.
3. On the box, it fetches the bundle into `~/repo` and checks out the commit,
   on a branch with the same name as the branch here. Then it writes
   `memory_share = 0.9` to the box's `~/.config/jobgate.toml` and checks that
   the profile fits the box.
4. On a box without a snapshot, it runs `bash <setup>` in `~/repo`. When the
   script exits with 0, jobgate saves the box as a snapshot.
5. It runs `jobgate run --local` on the box, with the same profile,
   `--max-workers`, and command. The `memory_share` from step 3 makes the
   worker count fit the box. The command runs
   under `setpriv --ambient-caps=-all --inh-caps=-all`, without Linux
   capabilities.
6. jobgate prints the command's output as it arrives. Then it reads the result
   of jobgate on the box, prints a result line, and deletes the box.

The box runs each step as a background process (`boat exec --detach`). When
this machine sleeps or loses its network, the step keeps running. jobgate then
attaches again and prints the output it missed. When `boat exec --attach`
prints nothing for 60 seconds, jobgate checks the step's status and attaches
again. It prints only the output that it has not printed yet. While jobgate
follows a box, it keeps the box's auto-stop an hour away. When jobgate dies, the
box stops within an hour.

Ctrl-C, SIGTERM, and SIGHUP delete the box. jobgate tries the delete up to 3
times. A Ctrl-C, SIGTERM, or SIGHUP that arrives during the delete waits until
the delete ends, and then jobgate stops. When one of the signals arrives before
`boat new` prints the new box's id, jobgate cannot delete the box, and the box
stops within an hour.

### Snapshots

A snapshot is a saved copy of a box's disk. It holds the checkout, its history,
and what the setup script installed. Its name is `jobgate-R-K`. `R` is the
first 12 hex digits of the repository's root commit. `K` is the first 12 hex
digits of the setup key, a SHA-256 hash of the setup script, the
`setup_inputs` files, and the `exclude` paths in the commit that the box checks
out. When the setup script or a `setup_inputs` file is a symbolic link to a file
in the repository, the hash covers that file.

A run whose snapshot exists starts from it and skips the setup script. A run
whose snapshot is missing sets up a new box and saves it under the new name
before the command starts. jobgate waits up to 15 minutes for the save. Then it
removes the repository's other `jobgate-R-` snapshots. During the boat free
trial, boat refuses to save snapshots, so every run sets up a new box.

The setup script runs once per snapshot. Later runs check out other commits on
top of its work. Put what the script makes outside the checkout or in paths that
`.gitignore` hides.

### When the command runs here instead

jobgate runs the command on this machine, through admission, when any of these
is true:

- The `boat` CLI is not on `PATH` (`no_cli`).
- boat refuses to start a box. Examples: no login (`unauthorized`), a full start
  limit (`rate_limited`), no ready machine (`no_ready_machine`), or a box type
  that the plan lacks (`trial_machine_class_not_allowed`). The `fallback` code is
  boat's error code. It is `boat_failed` when the `boat` CLI exits with an error
  and no code, and `no_box` when `boat new` prints no box id.
- A step fails after boat creates the box, without the box dying. jobgate
  prints `BOAT RUN FAILED` with the step, the box, and a log, as in
  [When a box dies partway](#when-a-box-dies-partway), and deletes the box. It
  does not retry on a new box. These failures, with their `fallback` codes,
  are:
  - Git cannot build the bundle (`git`).
  - jobgate on this machine hits a file error, such as a full disk
    (`local_error`).
  - jobgate on the box refuses the profile (`box_refused`), and the box prints
    why. An example is a profile that needs more memory for 1 worker than 90%
    of the box's memory.
- Content under an excluded path also lives at another path, in the history or
  in the uncommitted changes (`excluded_elsewhere`).
- boat refused a start for payment less than an hour ago. After an HTTP 402
  error, such as `billing_required`, or after `trial_compute_limit_reached`,
  jobgate skips boat for an hour. It keeps the end time in
  `~/.cache/jobgate/boat-off.json`. The `fallback` code is the code of that
  refusal, or `off` when it had none.
- Before jobgate starts a box, one of these fails:
  - Git cannot read the repository (`git`).
  - `exclude` is set, and this machine's Git is older than 2.36, which cannot
    filter a bundle (`git`).
  - The repository has no commit (`no_commit`).
  - The commit has no setup script (`no_setup`).
  - The box would not get `jobgate.toml` or the directory where the command
    starts (`missing_on_box`). The commit lacks their files, or `exclude`
    leaves the files out. Submodules do not count.
  - jobgate on this machine hits a file error (`local_error`).
- The run died partway on two boxes in a row (`died`).

jobgate prints why. When it tried to start a box, it also prints a `boat log`
line with the path of that attempt's log. The result line says `where=local`
and gives the reason in its `fallback` field.

### When a box dies partway

A box dies partway when one of these happens:

- boat creates the box but reports an error before the box is ready. When boat
  refuses the start, such as for a full start limit or a payment error, jobgate
  runs the command here. [When the command runs here instead](#when-the-command-runs-here-instead)
  lists those refusals.
- The copy to the box fails.
- The checkout or the setup script fails.
- The box disappears.
- boat kills a step's process, or the box runs out of memory during the setup
  step.
- jobgate cannot reach the box for 10 minutes.

jobgate then prints the step, the box, the reason, and a log:

```text
jobgate: BOAT RUN FAILED during setup on box bx_1a2b3c4d: the setup script scripts/boat-setup.sh failed
jobgate: boat log: /home/me/.cache/jobgate/boat/2026-11-25T23-10-00Z-1a2b3c.log
jobgate: test retries once on a new box
```

It deletes the box and tries once more on a new box. When the second box also
dies partway, the command runs on this machine. A command that exits with an
error on the box has not died. jobgate returns its exit code and does not run the
command again. Sometimes boat reports that the command ended, and then the box
disappears before jobgate gets the rest of the output. jobgate then returns the
exit code that boat reported. It prints that it may have missed output, and the
path of the boat log.

When the box runs out of memory during the run step, jobgate prints the
`BOAT RUN FAILED` and `boat log` lines, deletes the box, and exits with 137. It
does not retry the command and does not run it on this machine, because the
command would use the same memory here. jobgate does the same when boat kills
the run step with SIGKILL and boat does not say whether the box ran out of
memory. One case is a box that disappears after the kill. Another is a box that
jobgate cannot reach for 10 minutes after the kill.

Each log holds everything that the box printed during one attempt, and
jobgate's boat messages. It also holds the arguments, exit code, and output of
each `boat` call that failed. It leaves out the two failures that jobgate
expects: a missing snapshot, and the delete of a box that is already gone.
`~/.cache/jobgate/boat/` keeps the last 20 logs.

## Results

jobgate measures each run's peak memory:

- On Linux in a scope, it reads the scope's `memory.peak`. On kernels before
  5.19, which lack `memory.peak`, it samples `memory.current` every 2 seconds.
  Both include the page cache and the tmpfs files that the run writes. A run
  that writes many files can therefore show a high peak and a `worker_mem`
  warning.
- On macOS, it adds up the `phys_footprint` of each process in the command's
  tree every 2 seconds, read through `proc_pid_rusage`. `phys_footprint` is the
  memory Activity Monitor shows, and it includes compressed memory.
- On Linux without a scope, it adds up the resident memory of the command's
  process tree every 2 seconds.

After a run, its page cache stays charged to `jobgate.slice` and counts toward
the cap, but the kernel can reclaim it. Files that the run leaves in a tmpfs,
such as a `/tmp` mounted as tmpfs, stay charged and cannot be reclaimed without
swap.

When several processes map one page, the macOS and Linux peak sums count that
page once per process. The sums skip a process they cannot read, and they miss
processes that leave the tree.

When the peak exceeds the stated memory, jobgate prints a warning with the
`worker_mem` value that would have covered the peak:

```text
jobgate: peak 5.2G is above the 3.3G that [test] states for 4 workers; raise worker_mem to "1076M" in /src/app/jobgate.toml
```

At the end of each run, jobgate prints one result line and appends it to
`~/.cache/jobgate/results.log` in the account's home directory:

```text
time=2026-11-17T18:00:00Z profile=test where=local repo=/src/app commit=4f0c2d9e1b7a5c3e8d6f4a2b0c9e7d5f3a1b8c6e dirty=yes workers=4 wait=12s peak=3172M stated=3424M exit=0
```

| Field | Value |
| --- | --- |
| `time` | When the run ended, in UTC |
| `profile` | The profile name |
| `where` | Where the run ran: `local` or `boat` |
| `box` | On a boat run, the box type |
| `fallback` | On a run that went to this machine instead of boat, the reason: a boat error code, `boat_failed` when the `boat` CLI exited with an error and no code, `no_box` when `boat new` printed no box id, `no_cli`, `git`, `no_commit`, `no_setup`, `missing_on_box`, `excluded_elsewhere`, `local_error`, `box_refused`, `died`, or `off` when the payment refusal that turned boat off had no code |
| `repo` | The directory that contains `jobgate.toml` |
| `commit` | `git rev-parse HEAD` when the run started, or `none` |
| `dirty` | `yes` when `git status --porcelain --untracked-files=normal` printed anything when the run started, `no` when it printed nothing, `unknown` outside Git |
| `workers` | The worker count |
| `wait` | Seconds from arrival to start. On a boat run, the time to create, copy to, and set up the box. `unknown` when the box ran out of memory during the run step |
| `peak` | Peak memory in MiB, or `unknown` when jobgate could not measure it, such as for a command that ended before the first sample |
| `stated` | `base_mem + workers × worker_mem` in MiB |
| `oom_kills` | On Linux in a scope, the number of the run's processes that the kernel OOM-killed, from the scope's `memory.events`. jobgate writes this field only when the number is above 0. |
| `exit` | The exit code jobgate returns |

Values that are empty or contain whitespace, a double quote, or `=` are JSON
strings. A byte that is not valid UTF-8, such as in a directory name, appears as
`\udcXX`, where `XX` is the byte in hex. All of a user's runs append their lines
to one log in the order they end. On this machine, jobgate runs dirty worktrees
like clean ones. On a boat run, the box gets the uncommitted changes as a
temporary commit. The `commit` field names `HEAD`, so a line with `dirty=yes`
does not describe the code that ran.

On a boat run, `workers`, `peak`, `stated`, and `oom_kills` come from jobgate
on the box. That jobgate writes its result to `~/.jobgate/run/result.json` on
the box instead of printing it. After the run step, jobgate reads the file with
`boat exec` and prints its own line with `where=boat`. When it cannot read the
file, `workers`, `peak`, and `stated` are `unknown`, and the line has no
`oom_kills`.

## Exit codes

| Code | Meaning |
| --- | --- |
| The command's code | The command exited. A command killed by signal N gives 128+N. |
| 125 | jobgate refused the run: a usage error, a configuration error, a run that can never fit, or a home directory in which jobgate cannot create `.cache/jobgate/`, its `runs/` directory, or its `queue.lock`. |
| 126 | The command is not executable. |
| 127 | The command was not found. |
| 128+N | jobgate received signal N, such as SIGTERM, SIGHUP, or SIGINT from Ctrl-C, before the command started. |

On Linux in a scope, the `jobgate.py _scope` process starts the command and
waits for it. SIGTERM, SIGHUP, and SIGINT do not kill the `jobgate.py _scope`
process during that wait. When another signal N, such as SIGKILL, kills it
during that wait, jobgate also returns 128+N. The command can keep running in
the run's scope. When the kernel OOM-kills the `jobgate.py _scope` process
during that wait, jobgate returns 137, because the kernel sends SIGKILL.

On macOS, `taskpolicy` starts the command. When it cannot start an executable
file, such as a script whose interpreter is missing, jobgate returns the exit
code of `taskpolicy`.

## Environment

| Variable | Use |
| --- | --- |
| The profile's `workers_env` | jobgate sets it to the worker count. |
| `JOBGATE_WORKERS` | jobgate sets it to the worker count. |
| `JOBGATE_RUN` | jobgate sets it to the path of the run's file. A nested call reads it to find the outer run. |
| `HOME` | jobgate takes `~` from the password database instead, so runs under every `HOME` share one configuration, queue, and results log. It reads `HOME` only when the account has no entry there. jobgate also ignores `XDG_CACHE_HOME` and `XDG_CONFIG_HOME`. |
| `JOBGATE_HOME` | Tests only. Replaces the account's home directory, which holds the configuration, the queue, and the results log. With a real systemd user session, a run that sets it still sets the real `jobgate.slice` cap from the `memory_share` it finds there. That cap applies to all of the user's runs, so set this variable only with a stand-in `systemctl` on `PATH`, as jobgate's tests do. |
| `JOBGATE_RESULT_FILE` | Internal. On a box, jobgate sets it for the box's `jobgate run --local`. That run removes the variable before it starts the command. It writes its result to the named file as JSON instead of printing a result line. |
| `JOBGATE_MACHINE_FILE` | Tests only. A JSON file with readings that replace the OS readings: `memory`, `cores`, `psi` or `darwin_level`, and `meminfo` or `vm_stat` text. jobgate reads `memory` and `cores` once per run. It reads the pressure and available-memory readings before each admission attempt. A `cgroup` value names a directory that stands in for the scope's cgroup. A `boat_silent_seconds` value replaces the 60 seconds that jobgate waits for output from `boat exec --attach`. A `boat_lost_seconds` value replaces the 10 minutes after which jobgate stops trying to reach a box. With a real systemd user session, a run that uses this file still sets the real `jobgate.slice` cap from its `memory` reading. That cap applies to all of the user's runs, so use this file only with a stand-in `systemctl` on `PATH`, as jobgate's tests do. |

## Files

`~` is the account's home directory from the password database, so one user's
runs share these files under every `HOME`.

| Path | Content |
| --- | --- |
| `~/.cache/jobgate/queue.lock` | The lock that runs take while they read or change the queue |
| `~/.cache/jobgate/runs/<run id>.json` | One file per waiting or running run, which the run locks with `flock`. It holds the run's admission and, while the run waits, why it waits. The OS releases the lock when the run's process dies. The next run that reads the queue then deletes the file. |
| `~/.cache/jobgate/runs/<run id>.scope` | Written by jobgate's helper inside the scope: the scope's cgroup and peak memory, or why the kernel memory cap is off. The next run that reads the queue deletes it after the run's `.json` file is gone. |
| `~/.cache/jobgate/results.log` | Result lines |
| `~/.cache/jobgate/boat/*.log` | The logs of the last 20 boat attempts |
| `~/.cache/jobgate/boat-off.json` | After a boat payment error, the time until which jobgate skips boat |

## Install and remove

`./install.sh` installs jobgate by default, as the `jobgate` target. The
astack-update skill installs it on every update, even after
`./uninstall.sh --target jobgate`. The update leaves jobgate out when the user
names targets without it, or when the home's manifest has no jobgate and a file
that astack does not manage is at one of jobgate's paths. The installer needs
Python 3.10 or newer as `python3`, while jobgate itself runs on Python 3.9. A
stock Mac's `python3` is 3.9, which cannot run the installer.

The installer writes `~/.local/bin/jobgate`, a shell wrapper that runs `python3`
on the script in `~/.local/share/astack/jobgate/`. When `XDG_DATA_HOME` is set,
the script goes to `$XDG_DATA_HOME/astack/jobgate/` instead.

When `~/.local/bin` is not on the installer's own `PATH`, the installer appends
a marked `PATH` line to the shell's startup files. It does not read the startup
files for other `PATH` lines. The startup files depend on `SHELL`:

| Shell | Startup files |
| --- | --- |
| zsh | `~/.zshrc`, which interactive shells read, and `~/.zprofile`, which login shells read. With `ZDOTDIR` set, both files are in `$ZDOTDIR`, resolved to an absolute path at install. |
| bash | `~/.bashrc` and the bash login file. Interactive non-login shells read `~/.bashrc`. Login shells read only the first of `~/.bash_profile`, `~/.bash_login`, and `~/.profile` that exists, so the installer writes that file. When none of the three exists, it creates `~/.profile`. |
| other | None. The installer prints the line to add instead. |

Before it writes a line, the installer records the line and the number of copies
of it that the file already contains. It also records the directory that
contains the script.

`./uninstall.sh` removes the installed files and the marked lines. In each
startup file, it removes the last marked line, the one the installer wrote, and
leaves any earlier copy. When the file contains no more copies than before the
install, it removes none. So when the user deletes an earlier copy after the
install, jobgate's marked line stays in the file. When `./uninstall.sh` cannot
rewrite a startup file, it keeps the record of the line in that file. The next
`./uninstall.sh` then removes the line. `./uninstall.sh` also removes the
recorded directory and its parent, when they are empty. A reinstall that moves
the script, such as to another `XDG_DATA_HOME`, removes the old directory the
same way. `./uninstall.sh` leaves `~/.cache/jobgate/`, `~/.config/jobgate.toml`,
and the runtime settings of `jobgate.slice`. Those settings last until the
user's systemd manager stops: at reboot, or at the user's last logout when
lingering is off. With `Linger=yes`, they last until reboot.

Earlier jobgate versions put each `HOME` in its own `jobgate-<key>.slice` inside
`jobgate.slice`. Those empty slices stay just as long. To remove them sooner,
run `systemctl --user stop 'jobgate-*.slice'` while no jobgate run is active.
