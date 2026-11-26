# Gate a repository's commands with jobgate

jobgate keeps many concurrent test suites and builds from overloading one
machine. To gate a repository's command, give the repository a `jobgate.toml`.
Then call `jobgate run` from the repository's commands. With the fallback
patterns below, the command runs directly with 2 workers on a machine without
jobgate. For every option, see the [jobgate reference](jobgate.md).

## Add a profile

1. Create `jobgate.toml` at the repository root, with one table per heavy
   command:

   ```toml
   [test]
   base_mem = "1G"
   worker_mem = "500M"
   max_workers = 8
   workers_env = "PYTEST_XDIST_AUTO_NUM_WORKERS"

   [build]
   base_mem = "500M"
   worker_mem = "1G"
   max_workers = 16
   workers_env = "CARGO_BUILD_JOBS"
   ```

2. Run the command once under jobgate:

   ```sh
   jobgate run test -- pytest -n auto
   ```

3. Read `workers` and `peak` from the result line that jobgate prints at the
   end of the run.

4. Set `worker_mem` to about `(peak - base_mem) / workers`.

When a later peak exceeds `base_mem + workers × worker_mem`, jobgate prints the
`worker_mem` value that would have covered it.

## Pass the worker count to the tool

jobgate passes the count to the command through the environment, never as an
argument. Set `workers_env` to a variable the tool reads. For a tool that takes
the count as a flag, expand the variable in `sh -c`:

| Tool | `workers_env` | Command |
| --- | --- | --- |
| pytest with pytest-xdist 3.0.2 or newer | `PYTEST_XDIST_AUTO_NUM_WORKERS` | `pytest -n auto` |
| cargo | `CARGO_BUILD_JOBS` | `cargo build` |
| jest | `TEST_WORKERS` | `sh -c 'jest --maxWorkers="$TEST_WORKERS"'` |
| vitest 4.0.0 or newer | `VITEST_MAX_WORKERS` | `vitest run` |

Write `"$TEST_WORKERS"` inside the single quotes. Outside them, the calling
shell expands it before jobgate sets it.

vitest 4.0.0 is the first stable release that reads `VITEST_MAX_WORKERS`. It
applies the variable after its command-line options and its config file, so a
`--maxWorkers` flag cannot override jobgate's count. Releases 3.x and older do
not read it. For those, use `TEST_WORKERS` and
`sh -c 'vitest run --maxWorkers="$TEST_WORKERS"'`.

## Fall back when jobgate is missing

For a command in a Makefile, copy this pattern into the Makefile. Recipe lines
start with a tab:

```make
test:
	@if command -v jobgate >/dev/null 2>&1; then \
		jobgate run test -- pytest -n auto; \
	else \
		echo "warning: jobgate is not installed; running with 2 workers" >&2; \
		PYTEST_XDIST_AUTO_NUM_WORKERS=2 pytest -n auto; \
	fi
```

For a command in an npm script that runs vitest 4.0.0 or newer, first set
`workers_env = "VITEST_MAX_WORKERS"` in the `[test]` profile of `jobgate.toml`.
Then copy this pattern into `package.json`:

```json
{
  "scripts": {
    "test": "if command -v jobgate >/dev/null 2>&1; then jobgate run test -- vitest run; else echo 'warning: jobgate is not installed; running with 2 workers' >&2; VITEST_MAX_WORKERS=2 vitest run; fi"
  }
}
```

npm appends the arguments of `npm test -- ARGS` after `fi`, where the shell
rejects them. Add a separate script for each filtered run.

In both patterns, the shell exits with the command's exit code, with or without
jobgate. make reports a nonzero code as `Error N`.

## Run heavy commands on boat

A repository with a `[boat]` table in `jobgate.toml` runs each `jobgate run` on
a Linux box from [boat.dev](https://boat.dev). When boat cannot run it, the
command runs on this machine. The [Boat section of the reference](jobgate.md#boat)
covers what the box gets and each step of a boat run.

1. Install the `boat` CLI from [boat.dev](https://boat.dev).

2. Sign in with `boat login`. To sign in with an API key, keep the key in a file
   and pass it on stdin:

   ```sh
   boat login --key-stdin < path/to/key-file
   ```

3. Commit a bash script that installs what the command needs on a new box, such
   as the language runtime and the project's dependencies. The script runs in
   `~/repo` on the box, once per snapshot.

4. Add a `[boat]` table to `jobgate.toml`:

   ```toml
   [boat]
   setup = "scripts/boat-setup.sh"
   setup_inputs = ["requirements-dev.txt"]
   exclude = ["documents"]
   type = "large"
   ```

   List the lockfiles that the setup script reads in `setup_inputs`. A change to
   one of them makes the next run set up a new box. List in `exclude` each path
   whose contents must never leave this machine, such as private data.

5. Run the command once and read the result line. A boat run says `where=boat`.
   A run that fell back to this machine says `where=local` and gives the reason
   in its `fallback` field.

6. To keep a run on this machine, pass `--local`. Do this for a run that names
   specific test files, and for a command whose output files you need here:

   ```sh
   jobgate run --local test -- pytest tests/test_parser.py
   ```

7. Tell agents to send heavy commands through jobgate. Add these lines to the
   repository's `AGENTS.md`:

   ```markdown
   Run test suites, builds, and type checks through `jobgate run`. Pass
   `--local` when you name specific test files or need the files that the
   command writes. When jobgate prints `BOAT RUN FAILED`, tell the user the
   step, the box, and the `boat log` path from that output.
   ```

The first run uploads the repository's history. Later runs that start from the
snapshot upload only the new commits. During the boat free trial, boat does not
save snapshots, so every run uploads the history.

## Debug a boat run that failed partway

When a box dies partway, jobgate prints a `BOAT RUN FAILED` line with the step,
the box, and the reason, and a `boat log` line with a path. It retries once on a
new box. After a second failure, the command runs on this machine.

1. Open the file that the `boat log` line names. It holds jobgate's messages
   with their times, and everything that the box printed during that attempt.
2. If the reason says the setup script failed, fix the script and run the
   command again. A changed script makes the next run set up a new box.
3. If the reason says the box ran out of memory, set a larger `type` in
   `[boat]`, or a larger `worker_mem` in the profile.

## Follow a run

1. Run `jobgate status`. A waiting run shows how long it has waited and why. A
   running run shows its worker count, its stated memory, the memory it uses,
   and how long it has run.
2. Read the result line that jobgate prints when the run ends.
3. To find that line later, search `~/.cache/jobgate/results.log` in the
   account's home directory for its `time` and `repo` fields. Other runs' lines
   can follow it there.
