---
name: orchestrate
description: Execute a multi-phase plan or coordinate a multi-owner program with dependent PRs and durable verification records.
disable-model-invocation: true
---

# Orchestrate

**You own the program.** Author briefs, drain completions, maintain the merge frontier, and make coordination decisions. Use this workflow for executing every multi-phase plan and for any project with multiple owners and dependent PRs. Use [implement](../implement/SKILL.md) for a single change without a plan. Use [multi-phase-plan](../multi-phase-plan/SKILL.md) first when the PR sequence still needs a plan.

Scale coordination to the work. For small, similar units, use the simpler procedures noted below.

Follow these rules.

- Completions are queue events, not interrupts.
- Every spawn and every resume carries the standing orders verbatim.
- Give each worker a brief it can execute without relying on a follow-up exchange.

## Roles and placement

- **Coordinator (this chat).** The local coordinator defines the program, writes briefs, drains the inbox, reports progress, and makes coordination decisions. Outside a sequential run, it delegates code changes, conflicted merges, and restacks as tasks. The coordinator may integrate a verified unit when local Git is cheap. Any integration that changes the head needs verification at the resulting SHA. Follow [ship-pr](../ship-pr/SKILL.md) for authorized merges. Integrate verified work as it becomes ready. Use the active harness's agent tools for dispatch, status, messages, and completion events. Read [STATE.md](STATE.md) before creating the store and [CLI.md](CLI.md) before running its bookkeeping commands. If agent tools are unavailable and the program is too large for a sequential run, leave the executable plan and a concrete handoff instead of simulating workers.
- **Sub-coordinator.** Always local, durable, one per track, and only when the program exceeds what one coordinator's drains can manage. A track the coordinator can drain itself needs no middle layer. Each layer needs its own context. A sub-coordinator must report progress while its children run. It owns its track's units, writes briefs, and spawns workers and verifiers within the available nesting and concurrency limits. It reports aggregates at wave boundaries instead of forwarding raw child reports. Cap in-flight children at what one drain can process, up to the available agent slots, as a rolling window. Refill slots as workers finish instead of waiting for a whole batch.
- **Worker / verifier.** Use an isolated worktree or remote workspace when available. Keep work local when it needs local files, browser state, simulators, or authentication. Remote agents cannot read the local store, so their briefs inline what they need or point at accessible repo paths. Prefer fewer, broader workers. One writer per worktree or branch, per [Separate Before Serializing Shared State](../principles/separate-before-serializing-shared-state.md).

Keep at most three levels: coordinator, track coordinator, and worker. Choose tracks for the project. Build, landing, and verification are common choices.

## Store layout

Reuse the plan's execution store, `.scratch/<program>/` by default, including progress from earlier sessions. The execution owner maintains the store through the CLI and the decision-log helper. Owners publish facts, readers aggregate at read time. [STATE.md](STATE.md) defines the shared files, checkpoint and resume procedure, and additional coordination files. Record the absolute path in the plan and handoffs. Keep the store outside disposable worker workspaces.

For a requested pause or transfer to another session, read [handoff](../handoff/SKILL.md). Keep the handoff in this store and reconcile active owners before transferring their work.

- `preferences.md` holds numbered standing orders for model policy, stack ownership, verification, scope, and escalation. Carry them into spawns and resumes as specified under The brief. When you catch yourself restating an instruction, append the line before you act ([Encode Lessons in Structure](../principles/encode-lessons-in-structure.md)).
- `overview.md` records the program and session handoffs. Append. Never rewrite wholesale per event.
- `units.tsv` tracks each unit. Update it through the CLI.
- `frontier.json` is the computed merge frontier, per Stack safety.
- `ledger.tsv` is the verification ledger, per Verification.
- `inbox/` holds completion pointers. Use `orch gate park` to record human decisions in `gates.md`. Include the blocked action and work that can continue in the question. Silence is not approval.
- `decisions.tsv` is the trail maintained through [show-me-your-work](../show-me-your-work/SKILL.md).
- `status.md` is derived from the unit and verification tables, gates, and latest overview handoff at each checkpoint, never hand-maintained. Regenerate it instead of narrating events into it.

## The brief

Every spawn carries a complete brief. Resolve missing fields before dispatch. For sub-coordinators, the brief must also define how to brief their workers.

```
GOAL         one sentence, the outcome, executable by a stranger with no chat access
SCOPE        paths this unit may write; paths it may not; its exclusive worktree or branch
CONTEXT      pointers to files and PRs; upstream reports pasted in full when this unit
             depends on them, because workers cannot see siblings
ACCEPTANCE   checkable criteria, one per line
VERIFY       exact commands or the control-skill path, plus known gotchas
TIMEBOX      rough cap on runtime; on expiry, return partial findings and stop rather than run on
FORBIDDEN    no stack mutation, no rebase, no force-push, no fixes outside scope, plus unit-specific bans
REPORT       status, branch, head SHA, PRs, verdict, what you actually ran, review-pr
             result per axis and reviewed SHA, deviations, suggested follow-ups
STANDING     <preferences.md pasted verbatim>
```

Size the brief to the unit. A one-command unit gets the template collapsed to a paragraph that still names goal, scope, the verify command, and the report shape. Local spawns may reference the standing-orders file by store path. Verbatim paste is for cloud spawns and every resume. Point each PR owner at [implement's unit rules](../implement/SKILL.md#work-a-unit-under-orchestrate) in CONTEXT, alongside the unit's bug-fix, perf-issue, refactoring, or hillclimb workflow when it has one.

A sub-coordinator brief adds its track boundary and unit list, its spawn budget and workspace requirements, the drain protocol, and the rollup format (per child: name, status, PR, head SHA, verdict, one line, plus track status and frontier delta).

Pass upstream findings to dependent workers before dispatch. Audit one sampled worker brief per sub-coordinator per wave, concurrently with the wave it samples, never as a gate in front of it. If a brief fails the audit, pause new dispatch for that track and correct the sub-coordinator's instructions. Start a fresh worker with consolidated scope when earlier briefs have become fragmented.

## Steps

1. **Frame.** State the done predicate as something countable ("all 126 units merged, each ledger-verified `unit-test-verified` or better"). A request for specific plan units sets the done predicate to those units. Report their unmet dependencies as gates instead of working on them. At Close, leave the program store in place. Quantify scope: units, rough effort, expected stacks, and the wall-clock budget. If one agent could finish inside that budget, use a sequential run, described after these steps. When following an existing multi-phase plan, keep its shared store and checkpoint under [STATE.md](STATE.md). Schedule landing against the budget. By roughly 70% of it, stop spawning and land what is verified. Name the tracks per project. Settle a contested decomposition through [architect](../architect/SKILL.md) before the pilot. Use [wayfinder](../wayfinder/SKILL.md) when unresolved decisions need several sessions. Present the framing once. Reversible prep proceeds without waiting.
2. **Initialize or resume the store.** Follow [STATE.md](STATE.md), reconciling existing progress before assigning work. Open the trail through [show-me-your-work](../show-me-your-work/SKILL.md). Write the standing orders before any spawn, record the execution and merge authorization, and seed `frontier.json` from existing PRs and their current heads.
3. **Pilot.** Push one unit through the whole path: brief, worker, verification, stack entry, ledger row, and merge when authorized. Otherwise the pilot ends at merge-ready. Use the pilot to test the brief, verification procedure, and unit size. Correct them before dispatching more workers. Scale the pilot to the unit. On programs of near-identical cheap units, the first unit is the pilot, run as a normal unit with its verify command inline, and fan-out starts the moment it completes the authorized delivery step. The dedicated pilot pipeline (separate verifier agent, audit gate) is for expensive or novel unit shapes. Use the simpler pilot for repeated, well-understood units.
4. **Scale.** Spawn a rolling window of workers up to the in-flight cap, refilling as children finish. Spawn track sub-coordinators only past the one-drain threshold in Roles. Recompute ready work after each drain. Relay upstream reports into downstream briefs. Keep sibling communication upward only. The sampled brief audit runs alongside the wave it samples and stops the next refill on failure, not the current one.
5. **Drain.** Run the queue discipline below at every drain point.
6. **Land.** Within the user's merge authorization, landing is continuous, never a terminal phase. Otherwise keep verified units merge-ready. Integration starts with the first verified unit and runs alongside the remaining waves. On heavy repos the stacker is a standing role from wave one, integrating as units verify. On repos where local git is cheap, the coordinator lands verified units itself per Roles. Resolve blockers at the lowest unmerged PR before starting upper-stack work. Stack safety governs. Recompute `frontier.json` on merge, stack mutation, or reported new head SHAs.
7. **Close.** Drain the final inbox. Reconcile every spawned agent to done, abandoned, or zombie-reconciled, or leave a merge-ready handoff when merging is outside scope. Confirm the done predicate on the real artifact and each landed PR's verdict at its final head SHA. Audit the trail through [show-me-your-work](../show-me-your-work/SKILL.md), including its independent review. Encode recurring corrections into `preferences.md` or the brief template. Follow [STATE.md's close procedure](STATE.md#close) for record retention and authorized cleanup.

**Sequential run.** Work each unit in this session under [implement's unit rules](../implement/SKILL.md#work-a-unit-under-orchestrate), with its plan section or framing as the brief. This session is also the stacker and store owner, so it writes code, restacks, re-verifies at the new head, and records receipts directly. It needs no agent tools, but uses them when available for review-pr's reviewers and dedicated verifiers. Give a dedicated verifier this session's review-pr result per axis and reviewed SHA. Skip Pilot, Scale, and Drain. Follow Initialize or resume the store, Land, and Close. If the Frame estimate proves wrong, switch modes and keep the store. Drain and reconcile every in-flight worker before switching to a sequential run. Switching to workers continues from Pilot.

## Queue and drain

- On a completion notification, record a pointer with `orch inbox push` and return to what you were doing. Never deep-review inline. A completion that needs review becomes a verifier unit. Never review a diff inside a drain.
- Drain in batches at the end of a critical section, a track rollup, a scheduled wake when supported, and before a human report. Snapshot the pending inbox at the start of each drain. Arrivals during a drain wait for the next one. Use the harness's waiting tools while the session is active. Claim a background watcher only when an actual scheduling facility has been configured.
- Finish the current brief, stack operation, conflict decision, gate entry, or ledger or frontier update before draining completions.
- Each drain classifies every pointer (landed, needs-verify, failed, zombie, noise), updates the unit rows and verification ledger, appends the acceptance handoff to `overview.md`, and regenerates `status.md` before spawning the next wave. The CLI retains each drained batch before returning it.
- At each track rollup, record whether every spawned child returned, was respawned, or had its scope reassigned. Identify any missing findings before reassigning work.
- A drain turn ends with three lines derived from the tables: counts against the states, what changed, gates open. Detail lives in `status.md`. The full reply contract applies at checkpoints and close.

## Stack safety

- Recompute the frontier after each merge or stack mutation using [STATE.md](STATE.md#recompute-the-merge-frontier). Each store represents one ordered frontier. Track coordinators with separate stores return rollups. During a restack, wait for the stacker to confirm the complete graph before admitting work against that generation. Conflicting or missing state blocks landing instead of guessing.
- Exactly one stacker per stack may mutate its branches. Record the holder in the standing orders. Use an isolated worktree or remote workspace for restacks. Git and the repository's forge tools are sufficient; Graphite is optional.
- Workers never rebase or change the stack. When hardening is requested, assign [harden-pr](../harden-pr/SKILL.md) to one owner per stack, scoped to one immutable frontier generation. Reviewers report conflicts to the stacker instead of restacking.
- PR closes and retargets go through the stacker only. Closing a base PR orphans every chain above it. Merges and stack surgery are units with briefs like any other.
- One retro watcher follows merged PRs for reverts, post-merge CI breaks, and orphaned follow-ups.

## Verification

Scale verification to the unit. When VERIFY is a single cheap command, the worker runs it and reports the output, and the coordinator spot-checks receipts. Use a dedicated verifier for expensive checks, judgments that need independent review, or changes with broad effects. It checks acceptance at the merge-ready SHA and runs review-pr only in report-only mode, returning findings to the owner. When the report omits the review-pr result or its reviewed SHA does not resolve, the verifier runs review-pr. Otherwise, for expensive checks or broad effects, it reuses the reported result when the reviewed SHA matches the merge-ready SHA. When the SHAs differ, it runs `git range-diff` between them and reviews only the patches that differ. Run dedicated verifiers as native agents on the session's model. A judgment that needs independent review gets a fresh review-pr run from the verifier, plus a review from Codex when hosted in Claude or from Claude when hosted in Codex, following [provider execution](../arena/PROVIDERS.md). Treat the other provider's findings as evidence for acceptance, not as a ledger receipt.

Follow [STATE.md's verification procedure](STATE.md#accept-verification) to record and accept receipts. It defines verdicts, verifier precedence, evidence checks, and verification after a head change.

Publish each completed unit promptly. The worker pushes its branch, and the coordinator saves its verification receipt and ledger entry. Preserve the artifacts outside a disposable worker workspace.

## Liveness and failure

- Check liveness through read-only agent status, the ledger, `units.tsv`, `gh`, and pushed branches. Resuming an idle agent starts work and is not a status probe. Transcript mtime is not liveness.
- For a worker that exits without reporting, save a failure report with the unit, failure mode, last evidence, and recovery options. Push its path into the inbox. Replan as evidence arrives while other workers continue.
- Choose the retry by failure mode. For context or memory exhaustion, respawn with smaller scope. For a network failure, retry the same task. For a tool failure, change the tool path. Retry an unknown failure once. After two retries, abandon the unit and replan its remaining scope.
- Reconcile a late worker result against the current frontier and ledger before accepting it. Assign a fresh unit to incorporate useful findings that remain applicable.
- When bad upstream output, broken acceptance criteria, or unavailable infrastructure would invalidate more work, pause new dispatch. Follow the numbered pause and resume orders in [CLI.md](CLI.md#completions-and-recovery). Let in-flight work finish, fix the cause, then record the resumption.
- Bound the coordinator's infrastructure retries as well as the workers'. After a few consecutive tool aborts, stop retrying. Write a terminal handoff to durable state (what is done, where it lives, the exact command to resume) and end the run.
- Before ending a session, checkpoint under [STATE.md](STATE.md), including active workers and the next action. After a restart, follow its shared resume procedure before dispatching anything. Reconnect surviving work by PR and branch, reconcile each child before respawning its scope, then drain.

## Escalation

Honor authorization already given. Batch unresolved gates into the status page rather than asking per item. These include unauthorized irreversible actions (force-push to shared branches, deploys, deletions, closing someone else's PR), genuine product or preference calls no experiment settles, a standing order that contradicts observed reality, a program-level dead end that survived a replan. Park each as a `gates.md` entry before asking, and route work around it.

Handle routine frontier updates, restacks, retries, CI failures, review threads, and formatting within the authorized scope. Refuse work the brief excludes and continue the assigned work. Record these decisions without asking whether to keep going.

Address mid-run discoveries that block the frontier. Record other findings as follow-ups. Keep each worker within its assigned scope.

**Reply.** Report the done predicate and progress counts from the tables. Include each track's delivered work, the frontier with PR links and SHAs, verification results, abandoned work and reasons, and unresolved human gates. Link the store and decision trail. At handoff or close, append the Attention findings required by show-me-your-work.
