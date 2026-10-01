---
name: multi-phase-plan
description: Write an executable plan for work spanning several PRs, with dependencies, per-PR evidence, and delivery gates.
disable-model-invocation: true
---

# Multi-phase or multi-PR plan

**You own the plan, not the code. The plan is a checklist an owner runs box by box and the operator audits from the evidence.** The plan is the deliverable. Do not implement.

1. When the change is one or two files with an obvious approach, skip the plan. Say so and stop.
2. Settle open questions before you write. Use [prototype](../prototype/SKILL.md) for questions an experiment can answer, [architect](../architect/SKILL.md) for code structure, and [wayfinder](../wayfinder/SKILL.md) when decisions span sessions. Reuse existing evidence. Keep the branch, the SHA, and the artifacts for Appendix A. Ask the operator only about a product or preference call that no run can settle.
3. Explore independent areas in subagents when available and permitted, within the harness's capacity. Each returns file pointers, conventions, test commands, and entry points. No inlined dumps. Explore directly when delegation is unavailable or adds no value.
4. Copy the skeleton below into the plan file and fill every placeholder. Unless the operator names a path, write `.scratch/<program>/plan.md`. Keep every heading and every sub-block in the order shown. One section per PR. One PR is one change with its own evidence, per [Sequence Verifiable Units](../principles/sequence-verifiable-units.md). Name [orchestrate](../orchestrate/SKILL.md) as the execution skill. It uses the [shared execution-state convention](../orchestrate/STATE.md) and its [show-me-your-work](../show-me-your-work/SKILL.md) decision trail. Read the state convention and record the absolute store path in the plan. Record execution and merge authorization separately.
5. Write under [technical-writing](../technical-writing/SKILL.md) in full, then [unslop](../unslop/SKILL.md). The body is one Diátaxis mode, how-to. Appendices hold explanation and reference. Each heading states the task or the finding. No long dashes. No mid-sentence colons.
6. Run `python3 <this skill's directory>/scripts/check_plan.py <plan.md>` and fix every problem it prints. The checker validates structure, evidence fields, and probe references. Review the dependencies and the adequacy of the verification yourself.
7. Hand back. Post the plan path and the script's output, then stop. Execution starts under the named skill when the user authorizes it. Honor authorization already given for execution after planning. For a requested transfer to another session, use [handoff](../handoff/SKILL.md) to link the plan and record remaining approvals. Planning alone does not initialize an execution store.

**Verification.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence. Every verification block opens with that rule.

Give each verification command or tool procedure a named code fence, such as `sh probe=PR1.unit` or `text probe=PR1.live.1`. Use the PR section's ID, followed by `.unit`, `.live.<lane number>`, or `.perf`. For several unit probes, append a stable name, such as `.unit.errors`. Reference each fence from its verification box with ``Probe `PR1.unit` ``. Keep the commands beside their boxes or in an appendix. Plans without named probes retain their existing checks.

The live block is mandatory and exercises the real changed behavior. Use as many independent scenarios as the risks require, with an artifact and a pass predicate for each. One is the **Regression lane against trunk.** It runs the same load-bearing scenario on trunk and head. If trunk does not have the feature, record that fact and gate the behavior the diff adds plus the end state the user waits for.

For performance-sensitive changes, both trunk and head must produce the named metric. If trunk lacks the feature, isolate the added work and set an absolute budget for that work plus the end-to-end state. Do not claim a ratio between unlike scenarios. Name the metric, interleaved probe, trunk baseline measured first, and numeric failure rule. Otherwise write `Not applicable. <specific reason>` in the perf block.

Record human review gates required by the user or repository, including UI selection before real component edits. Evidence must be ready before requesting the remaining approval. A PR with no remaining human review gate writes `**Review gate.** None. <specific reason>` and no boxes under it.

**Control skill.** Pick available tools by what the PR changes. Use [t3-preview](../t3-preview/SKILL.md) for HTML previews and browser checks, terminal tools for CLIs, and the repository's simulator tools for mobile. A PR that touches two interfaces exercises both. For documentation or configuration, run a representative consumer workflow. An unavailable verification environment is a risk in Appendix C and a blocked check, never a pass.

````markdown
# <Program> plan

<Under ten lines. What changes, for whom, the rule the program enforces, and the PR ids in order.>

## How to read this

One box is one unit of work. Every box names the evidence that checks it. A nested box is a sub-step of the box above it. Check a box only when its evidence exists. Evidence from a verification command must also meet the Accept verification section of `<installed skills directory>/orchestrate/STATE.md`. The body is a how-to. The appendices explain and record.

The program runs `<installed skills directory>/orchestrate/SKILL.md`. <Who merges, and which PR ids are the operator's items that stop at merge-ready.>

Shared execution state lives at `<absolute store path>`, using `<installed skills directory>/orchestrate/STATE.md`. Use that same store for sequential and delegated execution, and link it in every session handoff.

Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.

Select a probe with `python3 <installed skills directory>/multi-phase-plan/scripts/check_plan.py <absolute plan path> --probe <id>`. The command validates the plan and prints that probe without executing it.

## Program checklist

### Arm the program

- [ ] Record the protocol, this plan, execution authorization, and merge authorization. Start when execution is authorized. A request to plan alone ends with this document.
- [ ] Initialize or resume the shared execution store under its STATE.md procedure. Record the plan path, stable PR unit IDs, the verification rule, who merges, and the done condition. Open the decision trail through the installed show-me-your-work skill. Create a harness goal only if the user explicitly requests one.
- [ ] Read the installed execution, verification, open-pr, and ship-pr skills used by this program. Record their paths and re-read them on resume or when they change.
- [ ] Record a supported audit cadence. If background scheduling is unavailable, audit at each completion drain and before each human report. Checkpoint unit states and receipts as work progresses. Before ending the session, append the current worktree, partial work, blockers, and exact next action to the store's overview and regenerate its status summary.
- [ ] At each audit, probe active owners using read-only status and their artifacts. Reconcile stalled work before replacing its owner. Post a status message: the drain message or Reply that the execution skill assigns to that moment.
- [ ] On the operator's hold or stand-down, apply the requested scope through the installed handoff skill's PAUSE.md procedure. Record the hold and release condition when checkpoint writes are allowed.

### Spawn owners

- [ ] Assign one owner per PR with the full lifecycle the execution skill names. Spawn within available capacity, or run the units sequentially.
- [ ] Follow this dependency graph. Start dependent work only after its parent merges, or base it on the parent branch when the plan uses a stack.
  - [ ] <PR id> and <PR id> are independent and first. Both branch from `main`.
  - [ ] <PR id> after <PR id>.
- [ ] Hold the file boundaries. <PR id or class> touches only `<glob>`.
- [ ] Hold the review gate. <PR ids and the existing user or repository requirements, including UI selection before real components. Record which approvals remain.>

### PR mechanics, for every PR

- [ ] Follow the installed open-pr skill. Independent PRs target the default branch. A stack child targets its parent branch.
- [ ] Run required repository checks and push with hooks on.
- [ ] Apply technical-writing and unslop to prose, and no-comments before review.
- [ ] Address review findings within scope. Use harden-pr when the user requests hardening.
- [ ] After any base update, verify the resulting head before reporting merge-ready.

### Verdict and merge, for every PR

- [ ] At the merge-ready head SHA, run the unit, live, and applicable perf checks below. Use independent verification for costly or judgment-heavy checks.
- [ ] Clean only when every applicable check passes. Findings go back to the owner. A new head gets fresh verification and a fresh verdict.
- [ ] Follow ship-pr when merging is authorized. Otherwise stop at merge-ready with the exact verified head and remaining gates.

### Boot recipe, for every live lane

Each lane uses an isolated workspace at the PR head when it writes files or state. Name the actual tools that drive it.

- [ ] Fetch and check out the recorded head SHA in the lane's workspace.
- [ ] <Start the required services and interface. Wait for ready, or record why no service is needed.>
- [ ] <Name the commands or browser actions and the read-only diagnostics.>
- [ ] Save artifacts under `<evidence directory>/<pr-id>/lane-<n>/` and return durable links with the report.

## <Task as a verb phrase> (<PR id>)

**Depends on.** <PR id, or None.>

**Files.**

- [ ] Edit `<path>`.
- [ ] Create `<path>`.
- [ ] Delete `<path>`.

**Build.**

- [ ] <One change. Name the symbol and the file.>

**You see.**

- [ ] <One observable result, with the exact log line or screen state.>

**Verify, unit.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.

- [ ] <Test file and the case it gains.> Probe `<PR id>.unit`.

```sh probe=<PR id>.unit
<test command>
```

**Verify, live.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence. Run at the PR head, per the boot recipe.

- [ ] Lane 1. Regression lane against trunk. Probe `<PR id>.live.1`. Run <the same load-bearing scenario> at trunk and head. If trunk lacks the feature, record that and gate <the behavior the diff adds plus the end state the user waits for>. Save `<artifact path>`. Pass when <predicate>.
- [ ] Lane 2. <Scenario.> Probe `<PR id>.live.2`. Save `<artifact path>`. Pass when <predicate>.

**Verify, perf.** Tests alone are not sufficient verification. A PR is verified only when its applicable unit, live, and perf boxes are checked with evidence.

- [ ] Metric. <What is measured at both trunk and head. If trunk lacks the feature, also name the diff-added work and the end-to-end state the user waits for.>
- [ ] Probe. `<PR id>.perf`. <Run at trunk and head, interleaved. Both sides must produce the metric.>
- [ ] Baseline. Record the trunk <value> first.
- [ ] Rule. <Head against trunk, with the number that fails. If the scenarios differ, add absolute budgets for the diff-added work and the user-visible end state instead of an invalid ratio.>

**Review gate.** <Remaining human approval required by the user or repository, or None with a specific reason.>

- [ ] <Publish the review artifact, screenshots or video when applicable.>
- [ ] <Record the operator's decision before the gated action. Reuse prior approval when it still covers this result.>

**Merge.**

- [ ] Root's clean verdict at the exact head SHA.
- [ ] Required checks and review findings are resolved.
- [ ] Any base update happened before the final verdict.
- [ ] <The authorized owner follows ship-pr, or records the PR as merge-ready for the operator.>

## Close the program

- [ ] Every box above is checked with its evidence.
- [ ] Audit the decision trail through show-me-your-work, including its independent review.
- [ ] When program cleanup is authorized, follow `<installed skills directory>/worktree-cleanup/PROGRAM.md`. Otherwise retain the plan, store, and evidence for later closeout.
- [ ] Reply to the operator with the report the execution skill names and the trail's Attention findings.

## Appendix A. Prototype evidence

<Each open question a prototype answered, with the branch, the SHA, and the artifact links. Each question that stays unproven.>

## Appendix B. Alternatives rejected

<Each approach weighed and why it lost.>

## Appendix C. Risks

<Each risk with the PR it lands in and what the owner watches.>

## Appendix D. Links and reading list

<Docs to read before editing. Which PRs need how, grilling, or architect. The decision trail follows `<installed skills directory>/show-me-your-work/SKILL.md`. Link the trail, decision map, and execution store when present.>
````

**Reply:** the plan path, the PR ids with their dependencies and the review-gated set, what the prototypes proved and what stays unproven, and the check script's output.
