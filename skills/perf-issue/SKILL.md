---
name: perf-issue
description: Measure and fix a performance problem with comparable baseline and post-fix evidence.
disable-model-invocation: true
---

# Perf issue

**You own the measurements. Plan, review, verify the numbers.** Tie every fix to a measurement.

For a diagnosis-only request, stop before implementation with the measurements, supported cause, and proposed next experiment. A read-only request also excludes instrumentation and code edits.

1. Reproduce the reported slowness in an authorized environment and capture a baseline through the affected UI, CLI, or API. Name the metric, unit, workload, revision, configuration, and repeatable command. Record warmup, trial count, and variation so later comparisons can distinguish a change from noise. For live capture and mechanism checks, follow [runtime forensics](../diagnosing-bugs/RUNTIME-FORENSICS.md). For an existing capture, follow [trace forensics](../diagnosing-bugs/TRACE-FORENSICS.md). If the baseline does not reproduce the complaint, report the gap before optimizing.
2. Use [how](../how/SKILL.md) to ground hypotheses. Measure the workload before claiming a performance limit.
   Most fixes come from eight strategy families. Use them as hypothesis generators, not a checklist. A family earns an attempt only when the trace shows the signal it names.
   - **Elimination.** Before optimizing the hot path, ask whether it needs to exist: a computation nobody consumes, a feature gate that's always off for this user, a sync that redundantly mirrors state, a legacy path kept "just in case". The trace shows what's slow, never that it's deletable, so this family needs the `how` pass, not the profiler.
   - **Divide and conquer.** The dominant cost scales with input size. Split the work so each piece touches less (chunk, shard, prune the search space) or so independent pieces run in parallel.
   - **Caching.** The same computation or fetch repeats on identical inputs. Store and reuse the result. Name what invalidates it before claiming the win.
   - **Indirection.** The hot path does expensive work a cheaper intermediate could absorb: an index instead of a scan, a queue that shifts work off the interactive thread, a handle that lets a cheaper implementation swap in. Add the hop only when it removes more from the critical path than it adds.
   - **Batching.** Many small operations each pay a fixed overhead (RPC, query, syscall, draw call). Coalesce them to pay the overhead once per batch.
   - **Redundancy.** The wait hangs on one slow instance or attempt. Duplicate the work (replicas, hedged requests, speculative execution) and take the fastest result. The trace has to show the wait dominates and the system has headroom.
   - **Lazy evaluation.** Cost lands on results that are never used or not needed yet (eager init on the boot path, rendering offscreen items). Defer the work until first use.
   - **Scheduling.** The work must happen, but not during the interactive moment. Move it to where nobody is waiting: idle callbacks, a background warmup after boot, precompute before the user arrives, cleanup after the frame commits. The win is perceived latency, so measure the interactive path, not total work done.
3. Plan the fix from the measurements. If it crosses a function boundary, read [architect](../architect/SKILL.md) first. Delegate implementation using the available subagent tools, with the hypothesis, scope, fixed workload, measurement command, and correctness checks. If delegation is unavailable, implement directly and make the separate review pass explicit. Review the diff and capture post-fix measurements.
   Apply [sequence-verifiable-units](../principles/sequence-verifiable-units.md), verifying each attempt before trying the next. Remove rejected experimental changes while preserving unrelated work.
4. Parse and compare the artifacts using the same workload, environment, metric, and measurement method. Change the code revision under test, and record both revisions. Report the absolute and percentage delta with the observed variation. Run correctness checks, using [tdd](../tdd/SKILL.md) when adding a behavioral regression test. An inconclusive measurement or a measurement of a different interaction is not a pass. Remove temporary instrumentation or record why it remains and who owns cleanup.
5. Run [review-pr](../review-pr/SKILL.md). Cite the commands, results, and artifact paths in the PR.
6. Use [open-pr](../open-pr/SKILL.md) within the task's delivery scope. Honor a request for local edits only. Merging is a separate action.

For a unit in a multi-phase plan, follow [the shared execution-state convention](../orchestrate/STATE.md). Reuse its store; delegated workers return evidence to the coordinator. For a session transfer, read [handoff](../handoff/SKILL.md) and include the measurement command, workload, results, and artifact paths.

**Reply:** metric, baseline number, post-fix number, delta, variation, correctness checks, and artifact paths. State when the evidence is inconclusive.
