---
name: orchestrate
description: Coordinate a multi-session program with multiple owners, dependent PRs, and durable verification records.
disable-model-invocation: true
---

# Orchestrate

**You own the program, never the code. Author briefs, drain the queue, keep the frontier green, decide.** For a whole project handed to one standing coordinator chat: multi-day, many stacked PRs, dozens to hundreds of subagents, the human checking in twice a day instead of every five minutes. Use [implement](../implement/SKILL.md) for work one agent can finish in a session. Use [multi-phase-plan](../multi-phase-plan/SKILL.md) first when the PR sequence still needs a plan. Route here when the work outlives any single agent. Work one agent could finish inside the session's budget is not a program.

Ceremony must scale with the program. On cheap near-identical units, collapse it as each section directs.

Three rules carry the rest.

- Completions are queue events, not interrupts.
- Every spawn and every resume carries the standing orders verbatim.
- The brief is the product. A vague brief fails quietly, because a worker cannot ask you a question.

## Roles and placement

- **Coordinator (this chat).** Local. Frames, authors briefs, drains the inbox, owns the human report, makes judgment calls. It never authors or edits code. Conflicted merges, restacks, and code changes are always tasks. The coordinator may integrate a verified unit when local Git is cheap. Any integration that changes the head needs verification at the resulting SHA. Follow [ship-pr](../ship-pr/SKILL.md) for authorized merges. Queueing finished work behind an idle stacker is how a deadline harvests nothing. The loop is agentic end to end. Use the active harness's agent tools for dispatch, status, messages, and completion events. Read [STATE.md](STATE.md) before creating the store and [CLI.md](CLI.md) before running its bookkeeping commands. If agent tools are unavailable, leave the executable plan and a concrete handoff instead of simulating workers.
- **Sub-coordinator.** Always local, durable, one per track, and only when the program exceeds what one coordinator's drains can manage. A track the coordinator can drain itself needs no middle layer. Each nested layer re-pays a full orientation preamble, and a blocking sub-coordinator hides its children while the parent idles. Owns its track's units and boards, authors its workers' briefs, spawns its own workers and verifiers (only within the active harness's nesting and concurrency limits). Rolls up aggregates at wave boundaries. Never forwards raw child reports. Cap in-flight children at what one drain can process, up to the available agent slots, as a rolling window. Never as blocking batches, which cost the slowest child of every batch.
- **Worker / verifier.** Use an isolated worktree or remote workspace when available. Keep work local when it needs local files, browser state, simulators, or authentication. Remote agents cannot read the local store, so their briefs inline what they need or point at accessible repo paths. Prefer fewer, broader workers. One writer per worktree or branch, per [Separate Before Serializing Shared State](../principles/separate-before-serializing-shared-state.md). Use a different model family for independent verification when available and permitted by the session's model policy.

Depth stays at coordinator, track, worker. Author the track decomposition per project (build, landing, and verification are common cuts, not a required shape). Hard-coded swarm trees were tried and parked as too rigid.

## Store layout

Reuse the plan's execution store, `.scratch/<program>/` by default, including any progress from sequential implement sessions. The execution owner writes shared records through the imported orchestration CLI. Owners publish facts, readers aggregate at read time. [STATE.md](STATE.md) defines the shared files, checkpoint and resume procedure, and additional coordination files. Record the absolute path in the plan and handoffs. Keep the store outside disposable worker workspaces.

- `preferences.md` is the standing-orders register: numbered lines, one constraint each (model policy, stack shape and count, verification bar, forbidden paths, escalation policy). Paste it verbatim into every spawn and every resume. Directives decay across resumes, and each dropped one costs a human turn. When you catch yourself restating an instruction, append the line before you act ([Encode Lessons in Structure](../principles/encode-lessons-in-structure.md)).
- `overview.md` is the durable PR and issue DB. Append. Never rewrite wholesale per event.
- `units.tsv` has one row per unit: id, track, state, agent, branch, PR, head SHA, brief path. Update rows in place.
- `frontier.json` is the computed merge frontier, per Stack safety.
- `ledger.tsv` is the verification ledger, per Verification.
- `inbox/` holds completion pointers. `gates.md` parks human gates (question, options, blocked action, and work that can continue; silence is not approval).
- `decisions.tsv` records the question, evidence, decision, and affected units.
- `status.md` is derived from the unit and verification tables, gates, and latest overview handoff at each checkpoint, never hand-maintained. Regenerate it instead of narrating events into it.

## The brief

Your prompts to agents are your only product, and a sloppy brief compounds into slop across the whole tree. Every spawn carries all of it. A field you cannot fill is a unit you have not scoped yet.

```
GOAL         one sentence, the outcome, executable by a stranger with no chat access
SCOPE        paths this unit may write; paths it may not; its exclusive worktree or branch
CONTEXT      pointers to files and PRs; upstream reports pasted in full when this unit
             depends on them, because workers cannot see siblings
ACCEPTANCE   checkable criteria, one per line
VERIFY       exact commands or the control-skill path, plus known gotchas
TIMEBOX      rough cap on runtime; on expiry, return partial findings and stop rather than run on
FORBIDDEN    no stack mutation, no rebase, no force-push, no fixes outside scope, plus unit-specific bans
REPORT       status, branch, head SHA, PRs, verdict, what you actually ran, deviations,
             suggested follow-ups
STANDING     <preferences.md pasted verbatim>
```

Size the brief to the unit. A one-command unit gets the template collapsed to a paragraph that still names goal, scope, the verify command, and the report shape. A 4KB scaffold around a two-line edit costs more to write and obey than the edit. Local spawns may reference the standing-orders file by store path. Verbatim paste is for cloud spawns and every resume.

A sub-coordinator brief adds its track boundary and unit list, its spawn budget and workspace requirements, the drain protocol, and the rollup format (per child: name, status, PR, head SHA, verdict, one line, plus track status and frontier delta).

A dependency is a context relay, not just ordering. Undeclared upstream context makes the worker guess. Missing fields are a refuse-to-spawn condition. Audit one sampled worker brief per sub-coordinator per wave, concurrently with the wave it samples, never as a gate in front of it. A failing brief stops that track and fixes the sub-coordinator's instructions, not just the worker, because brief quality decays late in a run. Never resume-chain a brief. Respawn fresh with consolidated scope.

## Steps

1. **Frame.** State the done predicate as something countable ("all 126 units merged, each ledger-verified `unit-test-verified` or better"). Quantify scope: units, rough effort, expected stacks, and the wall-clock budget. If one agent could finish inside that budget, stop here and use [implement](../implement/SKILL.md) instead. Collapsing must not depend on another document being present. It means do the work directly in this session, plain workers where they help, verification inline, landing as you go, and no new coordination machinery. When following an existing multi-phase plan, keep its shared store and checkpoint under [STATE.md](STATE.md). Schedule landing against the budget. By roughly 70% of it, stop spawning and land what is verified. Name the tracks per project. Settle a contested decomposition through [architect](../architect/SKILL.md) before the pilot. Use [wayfinder](../wayfinder/SKILL.md) when unresolved decisions need several sessions. Present the framing once. Reversible prep proceeds without waiting.
2. **Initialize or resume the store.** Follow [STATE.md](STATE.md), reconciling existing progress before assigning work. Write the standing orders before any spawn, record the execution and merge authorization, and seed `frontier.json` from existing PRs and their current heads.
3. **Pilot.** Push one unit through the whole path: brief, worker, verification, stack entry, ledger row, and merge when authorized. Otherwise the pilot ends at merge-ready. The pilot exists to falsify the brief template, the verify recipe, and the unit size while that costs one agent instead of fifty. Fix the contract from pilot evidence before any fan-out. Scale the pilot to the unit. On programs of near-identical cheap units, the first unit is the pilot, run as a normal unit with its verify command inline, and fan-out starts the moment it completes the authorized delivery step. The dedicated pilot pipeline (separate verifier agent, audit gate) is for expensive or novel unit shapes, not for clone-units where a serialized pilot has nothing to falsify.
4. **Scale.** Spawn a rolling window of workers up to the in-flight cap, refilling as children finish. Blocking batches pay the slowest child of every batch. Spawn track sub-coordinators only past the one-drain threshold in Roles. Recompute ready work after each drain. Relay upstream reports into downstream briefs. Keep sibling communication upward only. The sampled brief audit runs alongside the wave it samples and stops the next refill on failure, not the current one.
5. **Drain.** Run the queue discipline below at every drain point.
6. **Land.** Within the user's merge authorization, landing is continuous, never a terminal phase. Otherwise keep verified units merge-ready. Integration starts with the first verified unit and runs alongside the remaining waves. On heavy repos the stacker is a standing role from wave one, integrating as units verify. On repos where local git is cheap, the coordinator lands verified units itself per Roles. Keep the frontier green before upper-stack work. Stack safety governs. Recompute `frontier.json` on merge, stack mutation, or reported new head SHAs.
7. **Close.** Drain the final inbox, reconcile every spawned agent to a terminal row (done, abandoned, zombie-reconciled), or a merge-ready handoff when merging is outside scope, confirm the predicate on the real artifact, confirm every landed PR has a verdict for its current head SHA, audit the decisions and verification receipts, using an independent reviewer when available, encode recurring corrections into `preferences.md` or the brief template. Leave the store intact. It is the postmortem.

## Queue and drain

- On a completion notification, record a pointer with `orch inbox push` and return to what you were doing. Never deep-review inline. A completion that needs review becomes a verifier unit. Never review a diff inside a drain.
- Drain in batches at the end of a critical section, a track rollup, a scheduled wake when supported, and before a human report. Snapshot the pending inbox at the start of each drain. Arrivals during a drain wait for the next one. Use the harness's waiting tools while the session is active. Claim a background watcher only when an actual scheduling facility has been configured.
- Critical sections you finish first: authoring a brief, a stack operation, a conflict decision, writing a gate, updating ledger or frontier.
- Each drain classifies every pointer (landed, needs-verify, failed, zombie, noise), updates the unit rows and verification ledger, regenerates `status.md` from those tables, then spawns the next wave. The CLI retains each drained batch before returning it; record acceptance in the overview after its updates are saved.
- Account for every spawned child at its track's rollup: arrived, respawned, or its scope explicitly absorbed. Silently redoing a missing child's work hides both the wasted spend and the coverage gap its result existed to close.
- A drain turn ends with three lines derived from the tables: counts against the states, what changed, gates open. Detail lives in `status.md`. The full reply contract applies at checkpoints and close.

## Stack safety

- The frontier is a computed object, never narrative. Recompute `frontier.json` after every merge and stack mutation from current Git refs, PR base and head SHAs, and the recorded dependency graph. Record the ordered PR list, branch names, head SHAs, a generation number, and the lowest unmerged PR in each stack. During a restack, wait for the stacker to confirm the complete graph before admitting work against that generation. Conflicting or missing state blocks landing instead of guessing.
- Exactly one stacker per stack may mutate its branches. Record the holder in the standing orders. Use an isolated worktree or remote workspace for restacks. Git and the repository's forge tools are sufficient; Graphite is optional.
- Workers never rebase or change the stack. When hardening is requested, assign [harden-pr](../harden-pr/SKILL.md) to one owner per stack, scoped to one immutable frontier generation. Reviewers report conflicts to the stacker instead of restacking.
- PR closes and retargets go through the stacker only. Closing a base PR orphans every chain above it. Merges and stack surgery are units with briefs like any other.
- One retro watcher follows merged PRs for reverts, post-merge CI breaks, and orphaned follow-ups.

## Verification

Scale verification to the unit. When VERIFY is a single cheap command, the worker runs it and reports the output, and the coordinator spot-checks receipts. A dedicated verifier agent (on another permitted model family when available) is for units whose verification is expensive, judgment-laden, or high-blast-radius. A verifier agent whose entire product would be rerunning one command is ceremony, not verification.

Record each verdict and evidence with `orch ledger record`; use `orch --json ledger check` for the effective verdict at a PR and SHA. Before accepting it, fetch the PR's current head and compare the SHA with the row. `ledger.tsv` has one row per verdict, keyed by PR number plus head SHA, using the verdicts defined in [STATE.md](STATE.md). CI green is an input to a verdict, not a verdict. Behavioral work needs better than `type-check-only`. `verifier-blocked` is not a pass. Respawn when the environment heals. `verifier-failed` gets a fix unit, not a re-verify. A worker may self-report. An independent verifier overrides it on the same key. Keep earlier rows as evidence; the latest verifier verdict controls that key, and a later worker claim cannot override it. A new head SHA voids the row, so re-verify after restack. The ledger answers "was this verified", not memory and not the transcript.

A unit is not done until its output is externalized the moment it lands, never batched to the end of the run. A worker pushes its branch, the coordinator records the verifier's ledger row, receipts land in the store. Work that exists only on one VM when that VM dies was never done.

## Liveness and failure

- Never resume an agent to check on it. A resume restarts an idle agent. Probe read-only: the ledger, `units.tsv`, `gh`, pushed branches, the active harness's agent status. Transcript mtime is not liveness.
- A silent death gets a synthetic postmortem row in the inbox (unit, failure mode, last evidence, options). Replan on evidence as it arrives. Never wait for full quiescence.
- Retry by mode: cap-hit or oom, respawn with smaller scope. Network-drop, retry as-is. Tool-error, change the tool path or use another permitted model. Unknown, retry once. Two retries, then abandon the unit and replan around it.
- A zombie that returns hours late reconciles against the current frontier and ledger before anything is accepted. Salvage unique findings through a fresh unit, never a blind merge.
- When continued spawning would produce garbage tree-wide (bad upstream output, broken acceptance, dead infra), write a stop line at the top of the standing orders, let in-flight work finish, fix the cause, clear it.
- Bound your own infra retries the same way you bound a child's. After a few consecutive tool aborts, stop retrying. Write a terminal handoff to durable state (what is done, where it lives, the exact command to resume) and end the run.
- Before ending a session, checkpoint under [STATE.md](STATE.md), including active workers and the next action. After a restart, follow its shared resume procedure before dispatching anything. Reconnect surviving work by PR and branch, reconcile each child before respawning its scope, then drain.

## Escalation

Honor authorization already given. Batch unresolved gates into the status page rather than asking per item. These include unauthorized irreversible actions (force-push to shared branches, deploys, deletions, closing someone else's PR), genuine product or preference calls no experiment settles, a standing order that contradicts observed reality, a program-level dead end that survived a replan. Park each as a `gates.md` entry before asking, and route work around it.

Never reaches the human: frontier nudges, restack mechanics, retries, CI flake triage, review-thread triage, format fixes, scope the brief already forbids (refuse and continue), and "should I keep going". Act and log within the authorized scope.

Mid-run discoveries fix only what blocks the frontier. Everything else parks in follow-ups. At this fan-out a small scope leak multiplies into PRs nobody asked for.

**Reply:** at checkpoints and close: the predicate and the count against it from `units.tsv` and `ledger.tsv`, tracks and what each landed, the frontier (PR list plus SHAs), verdicts summary, what was abandoned and why, gates awaiting the human (the only asks), the store path, and the decisions path. Numbers from the tables, not narrative. Include PR links.
