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

Cursor's Task schema, cloud-only placement, model names, Graphite authority,
Bun orchestration CLI, and session-store paths are environment dependencies.
The adaptation uses available agent tools, isolated workspaces, Git and forge
state, and a durable task-local store. STATE.md defines the coordinator's single
writer procedure, table fields, event replay, frontier reconstruction, and resume
steps. Pstack's TypeScript runtime and its tests are not imported. This is a
documented procedure, not a replacement orchestration service or background daemon.

Sequential implement sessions and orchestrate use the same STATE.md convention
and the same plan-local store. The core records are the plan, overview, unit
table, verification ledger, and derived status. Handoffs record partial work,
blockers, ownership, and the exact next action. Worker queues and merge-frontier
files are added when needed. Resume reconciles saved state with Git and PRs
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
checks code-fence boundaries and numeric performance thresholds. It cannot judge
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
