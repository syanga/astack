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

vitest 4.0.0 is the first release that reads `VITEST_MAX_WORKERS`. vitest
applies it after its command-line options and its config file, so a
`--maxWorkers` flag cannot override jobgate's count. vitest 3 and older do not
read it. For those, use `TEST_WORKERS` and
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

For a command in an npm script, first set `workers_env = "VITEST_MAX_WORKERS"`
in the `[test]` profile of `jobgate.toml`. Then copy this pattern into
`package.json`:

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

## Follow a run

1. Run `jobgate status`. A waiting run shows how long it has waited. A running
   run shows its worker count, its stated memory, and how long it has run.
2. Read the result line that jobgate prints when the run ends.
3. To find that line later, search `~/.cache/jobgate/results.log` for its
   `time` and `repo` fields. Other runs' lines can follow it there.
