---
name: diagnosing-bugs
description: Diagnose failures and performance regressions, or analyze captured profiles, traces, and heap snapshots.
---

# Diagnosing bugs

A discipline for hard bugs. Skip phases only when explicitly justified. A diagnosis-only request ends with evidence and a proposed fix.

Read [Fix Root Causes](../principles/fix-root-causes.md). For regression tests, follow [tdd](../tdd/SKILL.md) and [Test Behavior, Not Implementation](../principles/test-behavior-not-implementation.md).

For an existing capture, follow [trace forensics](TRACE-FORENSICS.md) instead of requiring a new reproduction loop. For live profiling, follow [runtime forensics](RUNTIME-FORENSICS.md). Those paths may return a provisional diagnosis; they do not establish that a fix works.

The phases below apply to a runnable failure. Reuse reproduction evidence already gathered by the caller. Honor its scope: a read-only inquiry permits existing checks and evidence analysis, not code edits or instrumentation. When a needed experiment exceeds that scope, return the gap and proposed experiment. For a requested fix, [bug-fix](../bug-fix/SKILL.md) owns delivery; for optimization, [perf-issue](../perf-issue/SKILL.md) owns the measurement comparison. Continue the current diagnosis without recursively restarting those workflows.

For a session transfer, read [handoff](../handoff/SKILL.md). Include the reproduction command or capture, hypotheses tested, remaining probes, and cleanup state.

When exploring the codebase, read `CONTEXT.md` (if it exists) to get a clear mental model of the relevant modules, and check ADRs in the area you're touching.

## Redact

This skill has you show commands, outputs and captured artifacts. **Redact every secret first**: write `<REDACTED>` in its place. Build loops against env vars, so the credential stays in the environment rather than in what you show. Captured artifacts carry auth headers: quote only the lines that carry the signal.

If the redacted output is not enough to diagnose the bug, say so and ask the user.

## Phase 1: Build a feedback loop

**This is the skill.** Everything else is mechanical. If you have a **tight** pass/fail signal for the bug (one that goes red on _this_ bug), you will find the cause; bisection, hypothesis-testing, and instrumentation all just consume it. If you don't have one, no amount of staring at code will save you.

Spend disproportionate effort here. **Be aggressive. Be creative. Refuse to give up.**

### Ways to construct one, in roughly this order

1. **Failing test** at whatever seam reaches the bug: unit, integration, e2e.
2. **Curl / HTTP script** against a running dev server.
3. **CLI invocation** with a fixture input, diffing stdout against a known-good snapshot.
4. **Headless browser script** (Playwright / Puppeteer) that drives the UI and asserts on DOM/console/network.
5. **Replay a captured trace.** Save a real network request / payload / event log to disk; replay it through the code path in isolation.
6. **Throwaway harness.** Spin up a minimal subset of the system (one service, mocked deps) that exercises the bug code path with a single function call.
7. **Property / fuzz loop.** If the bug is "sometimes wrong output", run 1000 random inputs and look for the failure mode.
8. **Bisection harness.** If the bug appeared between two known states (commit, dataset, version), automate "boot at state X, check, repeat" so you can `git bisect run` it.
9. **Differential loop.** Run the same input through old-version vs new-version (or two configs) and diff outputs.
10. **HITL bash script.** Last resort. If a human must click, drive _them_ with [scripts/hitl-loop.template.sh](scripts/hitl-loop.template.sh) so the loop is still structured. Copy the template and edit its steps for the authorized environment before running it. Capture observations, and leave signing in to the user as a `step`. Captured output feeds back to you.

Build a loop that reaches the actual failure before choosing a fix.

### Tighten the loop

Treat the loop as a product. Once you have _a_ loop, **tighten** it:

- Can I make it faster? (Cache setup, skip unrelated init, narrow the test scope.)
- Can I make the signal sharper? (Assert on the specific symptom, not "didn't crash".)
- Can I make it more deterministic? (Pin time, seed RNG, isolate filesystem, freeze network.)

A 30-second flaky loop is barely better than no loop; a 2-second deterministic one is tight, a debugging superpower.

### Non-deterministic bugs

The goal is not a clean repro but a **higher reproduction rate**. Loop the trigger 100×, parallelise, add stress, narrow timing windows, inject sleeps. Measure the reproduction rate and record the workload, seed, and trial count. Raise the rate without changing the failure being investigated.

### When you genuinely cannot build a loop

Say so explicitly and list what you tried. Ask for the missing environment access or a redacted captured artifact (HAR file, log dump, core dump, or screen recording with timestamps). Continue useful read-only investigation, but label its hypotheses unverified. Production instrumentation requires explicit authorization. Do **not** treat the reproduction gate as passed without a loop.

### Completion criterion: a tight loop that goes red

Phase 1 is done when the loop is **tight** and **red-capable**: you can name **one command** (a script path, a test invocation, a curl) that you have **already run at least once** (show the invocation and its output, redacted), and that is:

- [ ] **Red-capable**: it drives the actual bug code path and asserts the **user's exact symptom**, so it can go red on this bug and green once fixed. Not "runs without erroring"; it must be able to _catch this specific bug_.
- [ ] **Deterministic**: same verdict every run (flaky bugs: a pinned, high reproduction rate, per above).
- [ ] **Fast**: seconds, not minutes.
- [ ] **Agent-runnable**: you can run it unattended; a human in the loop only via `scripts/hitl-loop.template.sh`.

Read enough code to construct the loop. Treat early theories as provisional until runtime evidence supports them. Proceed to Phase 2 only with the red-capable command; otherwise use the missing-loop procedure above.

## Phase 2: Reproduce + minimise

Run the loop. Watch it go red as the bug appears.

Confirm:

- [ ] The loop produces the failure mode the **user** described, not a different failure that happens to be nearby. Wrong bug = wrong fix.
- [ ] The failure is reproducible across multiple runs (or, for non-deterministic bugs, reproducible at a high enough rate to debug against).
- [ ] You have captured the exact symptom (error message, wrong output, slow timing) so later phases can verify the fix actually addresses it.

### Minimise

Once it's red, shrink the repro to the **smallest scenario that still goes red**. Cut inputs, callers, config, data, and steps **one at a time**, re-running the loop after each cut, and keep only what's load-bearing for the failure.

Why bother: a minimal repro shrinks the hypothesis space in Phase 3 (fewer moving parts left to suspect) and becomes the clean regression test in Phase 5.

Done when **every remaining element is load-bearing**: removing any one of them makes the loop go green.

Do not proceed until you have reproduced **and** minimised.

## Phase 3: Hypothesise

Generate competing ranked hypotheses before testing any of them. Aim for **3–5** when the evidence supports distinct explanations; do not invent alternatives to meet a quota. Single-hypothesis generation anchors on the first plausible idea.

Each hypothesis must be **falsifiable**: state the prediction it makes.

> Format: "If <X> is the cause, then <changing Y> will make the bug disappear / <changing Z> will make it worse."

If you cannot state the prediction, the hypothesis is a vibe: discard or sharpen it.

**Show the ranked list to the user before testing.** They often have domain knowledge that re-ranks instantly ("we just deployed a change to #3"), or know hypotheses they've already ruled out. Cheap checkpoint, big time saver. Don't block on it; proceed with your ranking if the user is AFK.

## Phase 4: Instrument

Each probe must map to a specific prediction from Phase 3. **Change one variable at a time.**

Tool preference:

1. **Debugger / REPL inspection** if the env supports it. One breakpoint beats ten logs.
2. **Targeted logs** at the boundaries that distinguish hypotheses.
3. Never "log everything and grep".

**Tag every debug log** with a unique prefix, e.g. `[DEBUG-a4f2]`. Cleanup at the end becomes a single grep. Untagged logs survive; tagged logs die.

**Perf branch.** For performance regressions, logs are usually wrong. Instead: establish a baseline measurement (timing harness, `performance.now()`, profiler, query plan), then bisect. Measure first, fix second.

## Phase 5: Fix + regression test

Continue into implementation only when it is authorized. For diagnosis-only work, report the supported cause, evidence, unresolved hypotheses, and proposed fix or next experiment. Then perform the cleanup below that applies to your probes.

Write the regression test **before the fix**, but only if there is a **correct seam** for it.

A correct seam is one where the test exercises the **real bug pattern** as it occurs at the call site. If the only available seam is too shallow (single-caller test when the bug needs multiple callers, unit test that can't replicate the chain that triggered the bug), a regression test there gives false confidence.

**If no correct seam exists, that itself is the finding.** Note it. The codebase architecture is preventing the bug from being locked down. Flag this for the next phase.

If a correct seam exists:

1. Turn the minimised repro into a failing test at that seam.
2. Watch it fail.
3. Apply the fix.
4. Watch it pass.
5. Re-run the Phase 1 feedback loop against the original (un-minimised) scenario.

## Phase 6: Cleanup

After a fix:

- [ ] Original repro no longer reproduces (re-run the Phase 1 loop). For intermittent bugs, compare before/after failure rates and trial counts; zero observed failures alone does not prove impossibility.
- [ ] Regression test passes (or absence of seam is documented)
- [ ] The hypothesis that turned out correct is stated in the commit or PR message, so the next debugger learns

For diagnosis and fixes alike:

- [ ] Temporary instrumentation and runtime patches you introduced are removed, preserving unrelated work. Search for your `[DEBUG-...]` prefix.
- [ ] Retain evidence needed to reproduce or explain the finding. Remove disposable experiments, and report retained artifact paths or any cleanup still pending.
