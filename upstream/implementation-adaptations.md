# Implementation skill adaptations

This collection combines pstack's design and feature workflows with Matt
Pocock's implementation skills. Source revisions and retained MIT licenses are
recorded in [manifest.json](manifest.json).

The source checkouts used for this comparison are:

- [pstack at 032be146](https://github.com/cursor/plugins/tree/032be146865d973682535de75f2287da438550bf/pstack).
- [Matt Pocock at c55ee460](https://github.com/mattpocock/skills/tree/c55ee46073ed923f86ce59a5eb3b6d895095d1b7).

## Collection

| Skill | Sources | Responsibility |
| --- | --- | --- |
| [architect](../skills/architect/SKILL.md) | pstack architect | Ground the problem, compare caller-first shapes, and choose a design |
| [implement](../skills/implement/SKILL.md) | pstack Feature playbook and Matt implement | Carry a feature, spec, or tickets through verified slices |
| [tdd](../skills/tdd/SKILL.md) | Both TDD skills | Establish failing-before evidence and implement one behavior at a time |
| [to-spec](../skills/to-spec/SKILL.md) | Matt to-spec | Synthesize agreed behavior, decisions, and open questions |
| [prototype](../skills/prototype/SKILL.md) | Both prototype workflows | Resolve design questions through disposable experiments |
| [to-tickets](../skills/to-tickets/SKILL.md) | Matt to-tickets | Divide work into verifiable slices with explicit dependencies |
| [diagnosing-bugs](../skills/diagnosing-bugs/SKILL.md) | Matt diagnosing-bugs | Establish a failure signal and test competing causes |

`feature` is a playbook inside pstack's `poteto-mode`, rather than a standalone
skill. Its implementation responsibilities belong in `implement` here. Keeping
both names would create two entry points with the same job.

`architect`, `implement`, `to-spec`, and `to-tickets` are explicit entry points.
`tdd`, `prototype`, and `diagnosing-bugs` remain model-invoked with bounded
triggers. Callers can read any of them through sibling paths. The installer
generates the corresponding Codex invocation policy.

## Shared responsibilities already in astack

| Existing skill or reference | Integration |
| --- | --- |
| how and why | Ground the affected system and recover design constraints |
| grilling and domain-modeling | Resolve requirements and maintain domain vocabulary when that work is requested |
| Deep Modules and Design It Twice | Supply shared interface vocabulary and independent design exploration |
| improve-codebase-architecture | Find refactoring candidates across an existing codebase; architect handles a particular proposed change |
| Test Behavior, Not Implementation | Own test-quality rules for both implementation and diagnosis |
| review-pr and harden-pr | Keep review and requested hardening in their existing workflows |
| open-pr and ship-pr | Keep PR preparation and merging separate from implementation mechanics |
| create-verification-skill | Maintain project-specific verification knowledge when a reusable verification skill is needed |
| t3-preview | Deliver prototypes and record ownership for cleanup |

## Reconciliation choices

### Architecture and implementation

Architect retains pstack's grounding, caller usage before types, distinct
alternatives, comparison by interface depth, and redesign when implementation
repeatedly contradicts the sketch. Its rationale format is shorter and the
shared design vocabulary stays in Deep Modules. Arena, Cursor model lists, and
duplicated principle bodies are replaced by local references and available
delegation. Scratch sketches avoid leaving unfinished bodies in production code.

Implement combines pstack's design ownership, decomposition, and artifact
verification with Matt's incremental implementation and regular focused checks.
Delegation depends on useful independent work, and delivery follows the user's
scope. A small local edit does not require arena, a full review workflow, or a
published PR. Design-only requests remain read-only with respect to product code.

### Testing and diagnosis

Matt's feature TDD and pstack's focused bug regression workflow share one loop.
The local testing principle owns assertion quality. The mocking reference adds
external dependency control and the limits of fake integrations.

Testing boundaries already established in the spec are reused. Choosing an
ordinary existing boundary does not require another approval. A new boundary
that changes the design remains a decision to resolve.

Matt's current TDD body excludes refactoring from the implementation loop even
though its description mentions red-green-refactor. The combined skill permits
concrete simplification after green, with affected checks rerun. Pstack's
practical fallback remains: a weak or disproportionate test gives way to an
explicit executable check and a reported coverage limit.

Diagnosing-bugs keeps reproduction, minimization, predictions, targeted probes,
and rerunning the original scenario after the fix. Read-only investigation can
continue without a reproduction, but its conclusions remain provisional. Fixed
hypothesis counts, fixed flake-rate thresholds, and a mandatory human-loop shell
template are omitted. Performance and intermittent failures require measured
comparisons rather than a claim that one passing run proves the fix.

### Specs and tickets

To-spec synthesizes the conversation without starting another interview. Its
format distinguishes acceptance criteria, decisions, scope, and open questions.
Prototype fragments remain useful when they express a decision more precisely
than prose. Story quotas and a blanket ban on concrete paths are removed.

To-tickets retains vertical slices and dependency edges. Wide refactors follow
the existing migration principle when callers can change together. Expand-contract
is reserved for compatibility or delivery constraints, with removal explicitly
planned. Coupled batches are not presented as independently verified tickets.

Both workflows work without Matt's setup skill, fixed labels, or external
tracker access. They honor existing authorization and publish only within the
task's delivery scope.

### Prototypes

Pstack contributes isolation and measurement on the relevant surface. Matt
contributes structurally different UI alternatives and logic demos with visible
state, free play, reset, and guided scenarios.

Matt's real-route edits conflict with the local static-mocks-first workflow.
UI prototypes therefore live in scratch HTML, use the user's design constraints,
and stop for a selection before production edits. Automatic code promotion,
archive branches, and issue updates are replaced by scoped handoffs. The logic
model stays separate from the page, but incorporation into production still
requires normal verification.

## Other upstream candidates

The inventory also surfaced these adjacent workflows. They remain candidates
for later work rather than additional entry points in this import.

| Candidate | Current disposition |
| --- | --- |
| Matt wayfinder | Planning across sessions through a tracker-backed graph of decisions; broader than implementation slicing |
| Matt research | Primary-source investigation and captured findings; a separate research workflow |
| Matt grill-with-docs | Mostly composition of grilling and domain-modeling already present |
| Matt triage and wizard | Issue lifecycle and human-only setup, outside this implementation collection |
| pstack blast-radius | Cross-boundary impact investigation; compare with existing review and hardening coverage before importing |
| pstack interrogate | Independent adversarial review already covered by harden-pr |
| pstack arena and swarm | Multi-agent orchestration with host-specific configuration; useful ideas do not require importing the runtime |
| pstack figure-it-out and show-me-your-work | Large unattended workflows and durable decision trails; broader than the initial collection |

## Verification

Installer validation checks skill names, description limits, references, and
invocation metadata. The repository test suite checks installation behavior.
These checks establish packaging correctness, not the effectiveness of every
workflow in a live coding session. Behavioral changes should follow observed
use rather than tests that pin prompt wording.
