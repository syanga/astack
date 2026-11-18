# Gate a repository's commands with jobgate

To keep a repository's test suite or build from overloading a machine that runs
many of them at once, give the repository a `jobgate.toml` and call
`jobgate run` from its commands. With the fallback patterns below, the command
runs directly with 2 workers on a machine without jobgate. For every option, see
the [jobgate reference](jobgate.md).

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

2. Run the command once under jobgate and read `workers` and `peak` from the
   result line:

   ```sh
   jobgate run test -- pytest -n auto
   ```

3. Set `worker_mem` to about `(peak - base_mem) / workers`. When a later peak
   exceeds `base_mem + workers × worker_mem`, jobgate prints the `worker_mem`
   value that would have covered it.

## Pass the worker count to the tool

jobgate passes the count only through the variable that `workers_env` names.
Point it at a variable the tool reads, or expand it in `sh -c`:

| Tool | `workers_env` | Command |
| --- | --- | --- |
| pytest with pytest-xdist 3.0.2 or newer | `PYTEST_XDIST_AUTO_NUM_WORKERS` | `pytest -n auto` |
| cargo | `CARGO_BUILD_JOBS` | `cargo build` |
| jest | `TEST_WORKERS` | `sh -c 'jest --maxWorkers="$TEST_WORKERS"'` |
| vitest | `TEST_WORKERS` | `sh -c 'vitest run --maxWorkers="$TEST_WORKERS"'` |

Write `"$TEST_WORKERS"` inside the single quotes. Outside them, the calling
shell expands it before jobgate sets it.

## Fall back when jobgate is missing

Copy this pattern into a Makefile. Recipe lines start with a tab:

```make
test:
	@if command -v jobgate >/dev/null 2>&1; then \
		jobgate run test -- pytest -n auto; \
	else \
		echo "warning: jobgate is not installed; running with 2 workers" >&2; \
		PYTEST_XDIST_AUTO_NUM_WORKERS=2 pytest -n auto; \
	fi
```

Copy this pattern into `package.json`:

```json
{
  "scripts": {
    "test": "if command -v jobgate >/dev/null 2>&1; then jobgate run test -- sh -c 'vitest run --maxWorkers=\"$TEST_WORKERS\"'; else echo 'warning: jobgate is not installed; running with 2 workers' >&2; vitest run --maxWorkers=2; fi"
  }
}
```

npm appends the arguments of `npm test -- ARGS` after `fi`, where the shell
rejects them, so add a separate script for each filtered run.

In both patterns, the shell exits with the command's exit code, with or without
jobgate. make reports a nonzero code as `Error N`.

## Check a run

While a run waits or runs, `jobgate status` lists it with its worker count and
stated memory. After it ends, its result line is the last line of
`~/.cache/jobgate/results.log`.
