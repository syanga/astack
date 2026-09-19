# Debugging skill adaptations

The new entry points start from pstack's original playbooks at
[032be146](https://github.com/cursor/plugins/tree/032be146865d973682535de75f2287da438550bf/pstack/skills/poteto-mode/playbooks).
The existing diagnosis skill retains Matt Pocock's implementation at
[c55ee460](https://github.com/mattpocock/skills/tree/c55ee46073ed923f86ce59a5eb3b6d895095d1b7/skills/engineering/diagnosing-bugs).
[manifest.json](manifest.json) records the exact source-to-destination mappings.
Each destination retains its source's MIT license.

## Entry points and shared methods

| Entry point | Responsibility | Shared methods |
| --- | --- | --- |
| [investigation](../skills/investigation/SKILL.md) | Answer a technical question with evidence and a judgment | how, why, diagnosis, or trace analysis |
| [bug-fix](../skills/bug-fix/SKILL.md) | Deliver a verified fix for a reported defect | diagnosing-bugs, tdd, architect, review-pr |
| [perf-issue](../skills/perf-issue/SKILL.md) | Improve a measured performance problem | profiling, trace analysis, how, architect, review-pr |
| [diagnosing-bugs](../skills/diagnosing-bugs/SKILL.md) | Establish the mechanism behind a failure | Reproduction loop and conditional forensics references |

The three new entry points require explicit invocation, like implement.
Diagnosing-bugs remains model-invoked and gains a captured-artifact trigger.
The installer supplies explicit-invocation metadata for the new skills.
Implementation workflows call the shared methods directly, without routing
through investigation. Existing caller evidence carries forward.

Matt's triage is excluded. Its tracker labels, state transitions, and rejected
request records are a separate issue-management workflow. Its clarification and
implementation-brief responsibilities already have homes in grilling and to-spec.
Trace and runtime forensics remain supporting documents rather than catalog entries.

## Investigation

The original playbook routes read-only questions through how and why, then
returns an explanation or recommendation. Astack preserves that workflow and
adds the agreed routes for suspected failures and captured artifacts. Evidence
and confidence remain part of the answer. A read-only question does not authorize
instrumentation, a prototype, or implementation.

The Poteto Mode throughput marker is removed because astack has no corresponding
requirement for read-only questions. Existing handoff preserves scope and evidence
for another session. An implementation request routes to the appropriate existing
workflow with the findings attached.

## Bug fix

The original six-step sequence remains: reproduce, establish the cause, plan and
implement, verify the original failure, preserve ordered evidence, and deliver.
The detailed loop comes from diagnosing-bugs, and practical regression checks
come from tdd/BUG-FIX.md. This avoids a second definition of those procedures.

Cursor control skills, fixed models, and the Cursor loop command become available
runtime and delegation tools. When delegation is unavailable, the owner performs
the work and a separate review pass. Code understanding and relevant regression
history still use how and why. Missing runtime access leaves reproduction
unverified; it does not prevent useful evidence gathering.

The original requires the failing reproduction to land before the fix in Git
history. Astack requires observed failing-before and passing-after evidence,
without mandating a separate failing commit. Existing sequence-verifiable-units
guidance governs commit boundaries. Review and PR delivery use the existing skills
and preserve local-only requests.

## Performance

The original measurement-first sequence and all eight optimization families
remain. New instructions record the workload, revision, configuration, warmup,
trial count, and variation. Before and after measurements use the same method,
and correctness checks accompany the performance comparison.

Forensics references supply capture and analysis guidance. Inconclusive results
remain inconclusive. Rejected experiments and temporary probes have explicit
cleanup. The link to Hillclimb is omitted because sustained optimization was not
selected for import. The one-off workflow remains complete without it.

## Forensics and diagnosis

Trace forensics retains format selection, a queryable representation, call-tree
and retainer analysis, source attribution, paired captures, and a cited diagnosis.
Native analysis queries can replace a SQLite conversion. Source mapping uses the
captured revision, and missing symbols limit attribution. A retained object alone
does not establish a leak, and paired captures alone do not prove causality.

Runtime forensics retains capture, reduction, mechanism experiments, and source
attribution. The original calls the workflow read-only while recommending live
instrumentation and runtime patches. Astack distinguishes diagnosis-only scope
from no-writes scope and requires authorization for production or daily-driver
processes. Temporary probes and patches have cleanup requirements.

Matt's runnable-failure loop remains intact. Existing captures take the forensics
branch without a new reproduction gate. The early code-reading prohibition is
narrowed so an agent can understand enough code to build the loop. Early theories
remain provisional. Diagnosis-only completion reports findings and cleans up
probes; it does not require fixing the bug or writing a commit message.

## Durable state and verification

Bug-fix and perf-issue use the existing execution store when working within a
multi-phase plan. Workers return evidence to its owner. Standalone work does not
initialize an orchestration store. Session transfers use handoff with the commands,
hypotheses, captures, and cleanup state needed to resume.

Installer validation covers names, descriptions, relative links, and invocation
metadata. Repository tests cover installation and existing tooling. These checks
do not establish debugging effectiveness against real failures or profiles.
