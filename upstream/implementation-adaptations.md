# Implementation skill adaptations

This collection starts from pinned pstack and Matt Pocock originals. Clear
upstream wording, structure, and examples are retained. The changes below
address astack integration, user preferences, or specific correctness issues.
Sources and MIT licenses are recorded in [manifest.json](manifest.json).

- [pstack at 032be146](https://github.com/cursor/plugins/tree/032be146865d973682535de75f2287da438550bf/pstack).
- [Matt Pocock at c55ee460](https://github.com/mattpocock/skills/tree/c55ee46073ed923f86ce59a5eb3b6d895095d1b7).

## Collection and existing responsibilities

| Skill | Base and retained material | Integration with astack |
| --- | --- | --- |
| [architect](../skills/architect/SKILL.md) | pstack's five phases, runner brief, red flags, and rationale template | how, why, Deep Modules, and local principles |
| [implement](../skills/implement/SKILL.md) | pstack Feature plus Matt's implementation checks and review | TDD, prototype, review-pr, and open-pr |
| [tdd](../skills/tdd/SKILL.md) | Matt's main skill, tests and mocking examples; pstack's regression workflow in BUG-FIX.md | Test Behavior, Not Implementation and Deep Modules |
| [to-spec](../skills/to-spec/SKILL.md) | Matt's process and inline spec template | Established domain vocabulary and testing decisions |
| [prototype](../skills/prototype/SKILL.md) | Matt's UI and logic branches; pstack's isolation and observation | Static mocks, t3-preview, and implementation handoff |
| [to-tickets](../skills/to-tickets/SKILL.md) | Matt's vertical slices, dependencies, and ticket templates | Existing internal-API migration principle |
| [diagnosing-bugs](../skills/diagnosing-bugs/SKILL.md) | Matt's six phases, feedback-loop menu, checklists, and human-loop script | Root-cause and behavioral-testing principles |

Pstack's `feature` is a playbook inside `poteto-mode`, not a standalone skill.
Its responsibilities live under `implement` here. Both TDD workflows share one
entry point, with pstack's focused bug-fix procedure reached through a reference.

Architecture discovery remains in `improve-codebase-architecture`; `architect`
handles a particular proposed change. Grilling and domain modeling retain their
existing jobs. Review, hardening, PR delivery, and merging use the existing
skills instead of new copies of their workflows.

Matt's Codex display metadata is retained. Architect, implement, to-spec, and
to-tickets require explicit invocation. TDD, prototype, and diagnosing-bugs
remain model-invoked. Descriptions are bounded by astack's catalog limit.

## Architecture and implementation

Architect retains the upstream phases, two distinct candidate requirement,
caller-first design, screening criteria, synthesis rationale, and redesign
signals. Arena and fixed provider models become portable subagent dispatch,
with a stated direct fallback when delegation is unavailable. Runner guidance
links to local principles and no-comments. The lead owns synthesis and
implementation, so candidate agents do not recursively run the full workflow.

Design-only requests stop at the recommendation. Implementation uses scratch
sketches until work begins and honors the static-mock selection requirement.
Requested adversarial review routes to harden-pr.

Implement retains pstack's design ownership, four-part throughput checkpoint,
delegation, diff inspection, and verifiable units. Matt supplies the focused
checks, TDD, final review, and commit guidance. Tool dispatch and skill links are
adapted to astack. Prior decisions carry forward, and publication follows the
task's delivery scope. A direct fallback records missing review separation when
subagents are unavailable.

## TDD

Matt's main structure and concrete test and mocking examples remain. Pstack's
bug-fix workflow retains its practical fallback and before/after evidence
requirements in BUG-FIX.md, with only skill frontmatter removed. Narration
comments are removed from code examples under no-comments; the examples remain.

Two behavior changes are deliberate:

- Existing testing decisions are reused. Ordinary use of an established public
  interface does not require another approval. A new boundary that changes the
  design remains a decision to resolve.
- Refactoring is allowed after green when it provides a concrete simplification,
  with affected checks rerun. Matt's pinned main body reserves refactoring for
  review even though its display metadata says red-green-refactor.

The existing testing principle remains authoritative for assertion quality.
The examples supplement it instead of replacing it.

## Specs and tickets

To-spec keeps the upstream template inline, including the implementation and
testing decision lists and prototype-fragment exception. Story coverage scales
to the feature instead of following a length quota. Further Notes records
assumptions and unresolved decisions; the handoff identifies blocking questions.

To-tickets retains both local and tracker templates, dependency order, vertical
slices, and the staged wide-refactor explanation. A pointer applies the existing
internal-API migration policy before choosing expand-contract. Tickets include
the verification that demonstrates the delivered behavior.

Both skills use the agreed destination and actual project labels. Matt's setup
skill is not required. Prior agreement is reused, and external publication stays
within the authorized scope. Local tickets remain usable without tracker access.

## Prototypes

Matt's logic scenarios, portable model, visible state, free play, guided
walkthroughs, and UI switcher behavior remain. Pstack supplies scratch isolation
and observation on the relevant surface, including timing experiments.

Real-route edits conflict with the user's static-mocks-first workflow. UI
variants therefore use isolated, self-contained HTML and representative data.
The rendering example uses browser APIs instead of framework components.
Visual defaults follow the user's black background, white text, density, and
animation constraints. HTML delivery and browser inspection use t3-preview.

A visual decision stops for selection before real component edits. The handoff
records the decision and prototype pointer; production work receives normal
verification. Branch archives and issue updates follow the task's delivery scope.

## Diagnosis

The six phases, feedback-loop examples, minimization gate, prediction format,
targeted probes, and cleanup checks remain. The optional human-loop script keeps
its behavior with narration comments removed under no-comments.

Diagnosis-only requests stop before implementation. When reproduction is
unavailable, read-only investigation may continue with unverified hypotheses;
the reproduction gate remains unmet. Production instrumentation requires
explicit authorization. Unsupported percentage claims are replaced with measured
reproduction rates and trial counts. The hypothesis count is guidance, not a
quota. Intermittent fixes require evidence beyond one passing run.

## Other upstream candidates

These remain separate scope decisions:

| Candidate | Existing coverage or distinct purpose |
| --- | --- |
| Matt wayfinder | Planning across sessions through a graph of decisions |
| Matt research | Primary-source investigation and captured findings |
| Matt grill-with-docs | Composition of grilling and domain-modeling |
| Matt triage and wizard | Issue lifecycle and human-only setup |
| pstack blast-radius | Cross-boundary impact investigation; compare with review and hardening |
| pstack interrogate | Independent adversarial review already covered by harden-pr |
| pstack arena and swarm | Provider-specific multi-agent orchestration |
| pstack figure-it-out and show-me-your-work | Large unattended workflows and durable decision trails |

## Verification

Installer checks validate names, descriptions, local references, and invocation
metadata. The repository test suite validates installation behavior. The restored
human-loop script receives syntax and fixture-input checks. These checks do not
establish effectiveness across live implementation tasks. Compare future changes
against the pinned originals and evaluate behavioral changes through actual use.
