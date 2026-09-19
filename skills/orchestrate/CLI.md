# Orchestration CLI

Use the imported pstack runtime to maintain the execution store defined in
[STATE.md](STATE.md). Sequential implement sessions and coordinators use the
same CLI. Read this file before the first command or when recovering a store.

## Setup and invocation

Requires Bun. The first invocation installs dependencies beside the script with
`bun install --frozen-lockfile`; the upstream lockfile is retained. Subsequent
calls reuse that installation until the manifest or lockfile changes. Set
`ORCH_OFFLINE=1` to refuse installation when dependencies are not prepared.
Dependency caches are excluded from astack installation payloads.

Set `ORCH_STORE` to the absolute plan directory and `ORCH_CLI` to the installed
skill's `scripts/orch/orch.ts`. For example, from this repository:

```bash
export ORCH_STORE=/absolute/project/.scratch/program
export ORCH_CLI="$PWD/skills/orchestrate/scripts/orch/orch.ts"
bun "$ORCH_CLI" init
bun "$ORCH_CLI" --help
```

`--store` overrides `ORCH_STORE`. Use `--json` when consuming output: the default
compact display truncates some lists. Each command has `--help` for arguments.
Read-only commands leave the store untouched; `status` writes `status.md`.

| Commands | Durable record |
| --- | --- |
| `init` | Create missing store files, preserve existing data |
| `unit add/set/get/list/counts` | Unit state, branch, PR, SHA, brief, agent |
| `ledger record/check/summary` | Verification receipts for a PR and exact SHA |
| `inbox push/drain/count/history` | Completion pointers and retained drain batches |
| `gate park/list/resolve` | Open questions and recorded answers |
| `frontier set/show` | Ordered PRs, SHAs, states, lowest unmerged PR, generation |
| `standing add/show` | Numbered standing orders |
| `status` | Derived tables, open gates, frontier, latest overview handoff |

A typical checkpoint, after saving the verification receipt:

```bash
bun "$ORCH_CLI" unit add api --track main --brief briefs/api.md
bun "$ORCH_CLI" unit set api --state needs-verify --agent session-2 \
  --branch feature/api --pr 42 --sha "$verified_sha"
bun "$ORCH_CLI" ledger record 42 "$verified_sha" unit-test-verified \
  --evidence reports/api-tests.md --verifier reviewer-1
bun "$ORCH_CLI" --json ledger check 42 "$verified_sha"
bun "$ORCH_CLI" status
```

Set `verified_sha` from the artifact actually tested. Inspect the receipt and
compare it with the current PR head before accepting the result. The CLI stores
the claim; it does not run verification or validate the evidence file. A ledger
check exits 0 for an existing row, including a failed or blocked verdict. Inspect
the returned verdict. Missing units or verdicts exit 2; usage, data, and lock
errors exit 1. Unit states are nonempty labels, not an enforced state machine.

`--verifier` identifies an independent verifier; omit it for a worker claim.
Records append to history. For the same PR and SHA, the latest verifier receipt
controls; otherwise the latest worker receipt controls. A new SHA is unverified
until recorded. Summary counts cover historical PR/SHA keys; the status page
also joins each unit's recorded head to its effective verdict.

## Completions and recovery

```bash
bun "$ORCH_CLI" inbox push worker-1 api done --report reports/api-tests.md
bun "$ORCH_CLI" --json inbox drain --peek
bun "$ORCH_CLI" --json inbox drain
bun "$ORCH_CLI" --json inbox history
```

The coordinator records completion events and reconciles each report before
updating units or verdicts. Drain atomically rotates the queue and retains the
batch under `processed/`. That name means drained, not accepted. History includes
archived batches and batches left at the store root by an interrupted drain.
After interruption, run `init` to recreate missing files, read history, and
reconcile receipts against current state before replaying them. Replay is not
automatically deduplicated. Malformed input remains available for repair.

Writes use the original PID lock and atomic file replacement. Another writer
fails while the lock is held; a later command recovers a dead process's lock.
`--force` retains upstream's lock takeover option. Use it only after confirming
that the former writer has stopped. A command lock is not ownership of a task
or a whole session. Confirm the execution owner's handoff separately. This is a
local store; copying its files between machines does not provide distributed locking.

`gate park --default` records a proposed answer. It neither approves the action
nor schedules a timeout. Resolve the gate only when the required decision exists.

## Merge frontier

Graphite remains the default, using its installed `gt` CLI and local Git refs:

```bash
bun "$ORCH_CLI" frontier set --repo /absolute/project
```

For GitHub without Graphite, supply the complete PR order from the plan and stack
owner, lowest first. This path requires Git and authenticated `gh`:

```bash
bun "$ORCH_CLI" frontier set --source github --repo /absolute/project --prs 42,43
bun "$ORCH_CLI" --json frontier show
```

The GitHub adapter reads PR states, bases, branches, and remote heads. It rejects
cross-repository PRs, local/remote head disagreement, duplicate branches, reversed
parent order, and open children of closed unmerged parents within the supplied
list. Fetch and reconcile local branches first, retaining branches for merged
PRs still in the list. Failed refreshes preserve the previous frontier; treat it
as stale until a refresh succeeds.

Each store holds one ordered frontier. Track coordinators can maintain separate
stores and send rollups. The GitHub list is explicit: the CLI does not discover
missing dependencies outside it. The frontier is an observation, not a merge
approval. Before merging, check plan dependencies, current head verification,
human gates, and authorization, then follow [ship-pr](../ship-pr/SKILL.md).

## Compatibility and boundaries

The runtime reads both upstream's unit and ledger tables and astack's earlier
documented `head_sha`/`role` tables. A write converts the affected table to the
runtime format while retaining its records. Unit ownership adds an optional
`agent` column to upstream's format. Legacy verifier rows receive the name
`legacy-verifier`; their unavailable timestamps remain empty.

`overview.md`, the plan, briefs, reports, and `decisions.tsv` remain authored by
the execution owner. Append handoffs under `## Handoff <date/time>` headings;
`status` includes the last such section. Its compact change summary compares
aggregate counts, frontier, and gate IDs, so consult the full status for head or
handoff changes that leave those aggregates unchanged.

Agent dispatch, scheduling, ownership transfer, active-plan discovery, execution,
and cleanup remain responsibilities of the skills and the active harness.
The CLI runs when invoked; it does not resume work in the background. Preserve
the store after completion. Workspace teardown follows
[worktree-cleanup](../worktree-cleanup/SKILL.md) when authorized.

## Verify changes to the runtime

Prepare dependencies outside the test run in a disposable copy:

```bash
orch_test_dir=$(mktemp -d)
cp -R skills/orchestrate/scripts "$orch_test_dir/runtime"
(cd "$orch_test_dir/runtime" && bun install --frozen-lockfile)
ASTACK_ORCH_NODE_MODULES="$orch_test_dir/runtime/node_modules" \
  python3 -m unittest discover -s tests -v
```

The Python wrapper copies the runtime and prepared dependencies into a temporary
directory, sets offline mode, and runs the upstream and astack Bun tests plus
strict TypeScript checking. Without Bun or the prepared dependency path, that
wrapper reports a skip; a runtime change requires running it, not accepting the
skip. The tests use temporary stores and repositories, with fake forge commands.
