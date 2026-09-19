---
name: diagnosing-bugs
description: Diagnose hard-to-reproduce bugs and performance regressions by building a repeatable failure signal and testing competing explanations.
---

# Diagnose a bug

For a bug with an obvious local regression test, use [tdd](../tdd/SKILL.md).
Use this workflow when the cause or reproduction is uncertain. A diagnosis-only
request ends with evidence and a proposed fix.

Read relevant project vocabulary and ADRs, then
[Fix Root Causes](../principles/fix-root-causes.md). Keep captured artifacts
within the authorized environment and redact secrets from reported commands,
logs, and traces.

## Build the failure signal

State the user's exact symptom and expected behavior. Find an executable check
that distinguishes them. Read [FEEDBACK-LOOPS.md](FEEDBACK-LOOPS.md) to choose
and tighten the loop.

Run the check before changing the implementation. Confirm that it reaches the
affected path and fails on this symptom. An environment error or a nearby bug
does not establish the reproduction.

Reduce the scenario one input, caller, or setup step at a time, rerunning the
check after each change. Preserve the original reproduction too. Stop reducing
when the remaining complexity explains the failure path and further reduction
would lose relevant behavior or cost more than it clarifies.

If the environment is unavailable, continue useful read-only investigation and
state which hypotheses remain unverified. Request the missing access or
redacted artifact when it blocks progress. Do not present a plausible cause as
confirmed evidence.

## Test explanations

List competing explanations when the evidence supports more than one. For each,
state a prediction that would distinguish it from the others. Share the ranking
and proceed with the highest-value probe within the authorized scope.

Change one variable at a time. Prefer debugger inspection or targeted boundary
logs that distinguish predictions. Give temporary instrumentation a unique
marker so it can be removed. For performance, compare measured baselines under
the same workload and environment, then profile or bisect before choosing a fix.

A cause is established when the evidence explains the original symptom and a
targeted change produces the predicted result. Record contradictory evidence
instead of forcing it into the first explanation.

## Fix and verify

When implementation is authorized, turn the minimized reproduction into a
regression test through [tdd](../tdd/SKILL.md). The test must reach the real bug
pattern, including multiple callers or an integration chain when required.
If no practical boundary can do that, report the coverage gap and retain the
closest useful executable check.

Apply the fix, confirm the regression check passes, and rerun the original
scenario. Run affected adjacent checks. For intermittent failures, compare
before and after reproduction rates and report the trial count and uncertainty.

Remove your temporary instrumentation and identify any retained diagnostic
artifacts. Report the confirmed cause, failing-before and passing-after evidence,
and any remaining limits on the conclusion.
