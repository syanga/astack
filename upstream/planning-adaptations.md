# Multi-phase planning adaptations

These imports preserve three distinct jobs. Wayfinder resolves decisions across
sessions. Multi-phase-plan produces an executable PR checklist. Orchestrate owns
the coordination of a program with several agents and dependent PRs.

They are explicit entry points. A settled task still goes directly to implement.
Prototype and architect answer particular design questions; to-spec records a
durable specification. A multi-phase plan can link that specification instead
of repeating it. No standalone to-tickets skill is added.

## Sources

Pstack comes from `cursor/plugins` at
`032be146865d973682535de75f2287da438550bf`. Matt Pocock's skills come from
`mattpocock/skills` at `c55ee46073ed923f86ce59a5eb3b6d895095d1b7`.
The manifest records each source path and retained license.

## Orchestrate

The pstack playbook supplies the coordinator role, brief template, seven-step
workflow, completion queue, stack ownership, SHA-keyed verification ledger,
failure handling, and escalation rules. Those remain the core of the skill.

Cursor's Task schema, cloud-only placement, model names, and session-store paths
are adapted to available agent tools, isolated workspaces, and a plan-local store.
The original Bun/TypeScript CLI, store implementation, tests, bootstrap, package
manifest, and lockfile are imported directly. Graphite frontier support remains;
GitHub support adds an explicit PR order and checks remote heads against local
branches. This runtime is the documented exception to astack's Python script
convention. The upstream lockfile and dependency versions are retained.

Targeted runtime changes preserve the shared-state contract: optional unit agent
ownership, reads of the previously documented table formats, append-only verdict
history with verifier precedence, retained inbox drain batches, and a status
section for current unit verification and the latest handoff. Generated status
explains that missing PR-ledger evidence does not assess local receipts. One original test
now names the second independent verifier, matching the intentional precedence
change. Additional tests cover those changes, lock contention and stale locks,
interrupted drains, and the GitHub adapter. Offline mode refuses dependency
installation during tests. The installer excludes local dependency caches.

STATE.md defines checkpoint and resume ownership and verification acceptance.
CLI.md supplies setup, command usage, recovery, and runtime limits. Shared rules
live in those references instead of being repeated in the playbook. Each store
holds one ordered frontier. Pause and resume orders use the CLI's numbered
standing-order format and retain both entries. The CLI serializes individual writes. It
does not claim tasks, dispatch agents, discover active plans, validate evidence,
schedule work, or authorize merges. Drained events remain available for recovery,
but the coordinator still reconciles replay against current Git and PR state.

Sequential implement sessions and orchestrate use the same STATE.md convention
and the same plan-local store. The core records are the plan, overview, unit
table, verification ledger, and derived status. Handoffs record partial work,
blockers, ownership, and the exact next action. The CLI initializes worker queues
and a merge-frontier file; populate them when coordination requires them. Resume reconciles saved state with Git and PRs
before assigning more work. Switching execution skills requires no state migration.

Planning authorization, execution authorization, and merge authorization remain
distinct. Existing authorization is honored. Without merge authorization,
verified units stop at merge-ready. Integration that changes a head requires
verification of the new SHA, and ship-pr owns authorized merge mechanics.

## Multi-phase-plan

The pstack plan skeleton retains its ordered sections, per-PR dependencies,
file scope, build steps, observable results, verification blocks, review gate,
merge checklist, and appendices. Existing astack skills replace pstack-only
playbook references. Exploration can run directly when delegation adds no value.

Verification retains real behavior checks and a trunk regression scenario.
The fixed ten-lane, fixed-model swarm becomes a risk-sized set of scenarios with
artifact paths and pass predicates. Performance-sensitive changes retain the
metric, interleaved probe, baseline, and numeric failure budget. Other changes
require an explicit reason for a performance exemption. Unavailable live checks
remain blocked. Human review follows the user's and repository's requirements,
including selection of UI mocks before real component edits.

The Node check-plan script is ported to Python's standard library. It checks the
plan structure and evidence fields, accepts reasoned performance exemptions,
and rejects missing live receipts, predicates, or regression scenarios. It also
checks code-fence boundaries and numeric performance thresholds. Named probes
bind commands to unit and lane IDs, and `--probe` selects a block after validation.
Duplicate, missing, and mismatched references fail. Plans without probe IDs retain
their existing checks. The checker cannot judge
whether a scenario proves the intended behavior or whether the dependency graph
is correct; the author still reviews those. Tests exercise the CLI using plans
in temporary directories.

The plan records a supported audit cadence instead of requiring Cursor's loop
and cloud-sleeper. Creating a harness goal requires an explicit user request.
Installed skill paths replace reads from an assumed pstack checkout on trunk.

## Wayfinder

Matt's map, decision tickets, ticket types, fog-of-war distinction, scope rules,
and chart/work modes remain. Research uses available tools and primary sources.
Prototype, grilling, and domain-modeling point to their astack siblings.
Matt's setup skill is replaced by the chosen tracker or a local Markdown fallback.

TRACKER.md adapts the local tracker's wayfinding operations. The single
coordinator serializes claims and shared-map updates, and records session owners.
Resolved, out-of-scope, and superseded tickets have separate states. Canceling
a blocker does not satisfy its dependents. A stale claim requires reconciliation
before reassignment. Shared trackers use their own atomic operations where
available. Local files remain local until shared publication is authorized.

The one-decision-per-session default remains, with an explicit user override.
Invalidated tickets retain their history instead of being deleted. A route is
clear only when its in-scope tickets and fog are resolved, not merely when the
current frontier is empty. Matt's Codex display and invocation metadata is retained.

## Show-me-your-work

Pstack's decision-trail skill is imported as a separate skill, with its original
TSV template, Bash helper, examples, and MIT license. Orchestrate references it
when opening and auditing the trail, as upstream does. Multi-phase plans and
sequential implement sessions use the same trail through their shared store.
The decision-trail skill owns the schema, logging rules, and audit procedure.

The Bash helper retains timestamping, parent-directory creation, single-line
cells, and spreadsheet formula-prefix escaping. It adds a header check to avoid
appending six-column rows to astack's earlier four-column logs. The transition
procedure preserves the earlier file and logs its path without inventing missing
facts. Writes remain serialized by the execution owner; the helper has no lock.

Transcript access uses the active environment's task-scoped tools or documented
paths. When transcripts are unavailable, the audit uses available command records
and artifacts and reports that limit. Independent review remains required when
delegation is available. A different model family is used when available and
permitted; the final Attention findings identify coverage limits. The upstream
instruction to delete incorrect entries conflicted with its append-only rule.
Corrections now append evidence and identify the original row.

The prose pass preserves the imported workflows, examples, and useful terms such
as brief, frontier, and fog of war. It removes figurative phrasing, splits dense
instructions, and replaces repeated state rules with references.

## Handoff

Matt's handoff supplies the short entry point, suggested-skills section, artifact
pointers, redaction rule, and focus on the receiving session. Its explicit-only
invocation policy and Codex metadata remain. Pstack's pause-safely and
session-pickup playbooks supply separate procedures for stopping and resuming.
Both source licenses are retained.

The original temporary-file destination remains for standalone tasks. Planned
execution instead appends to the existing store's overview and regenerates its
status; Wayfinder retains handoffs in its map and ticket records. Workers return
handoffs to the coordinator rather than writing shared state. A planning-only
handoff does not initialize an execution store. Cross-machine transfer is
explicit, and preparing a note alone does not stop workers or transfer ownership.

The pause procedure preserves partial edits and honors existing delivery scope
instead of requiring a WIP commit. It distinguishes immediate stop-writes orders
from a pause at a safe boundary, checks worker status, and retains unconfirmed
ownership. Pickup uses task-scoped artifacts and transcript access instead of
Cursor paths. It reconciles current Git and tracker state, reuses applicable
evidence, and repeats checks when their inputs changed or evidence is missing.
This resolves the source's tension between never repeating verification and
proving inherited claims.

Pauses with an execution store use the CLI's numbered pause and resume orders.
The hold records whether in-flight work may continue and what permits resumption.
An operator hold survives session changes until its release condition is met.
Pickup with an execution store delegates to STATE.md. Other tasks reconcile
ownership and evidence before continuing. Handoff routes decision-trail audits
and Attention findings through show-me-your-work. A stop instruction that prevents
review leaves that review explicitly pending.

Implement, prototype, multi-phase-plan, orchestrate, and Wayfinder point to handoff
at session-transfer boundaries. Routine checkpoints still follow STATE.md, and
ordinary same-session skill transitions need no extra document. Handoff adds no
scheduler, active-plan discovery, or automatic ownership transfer.
