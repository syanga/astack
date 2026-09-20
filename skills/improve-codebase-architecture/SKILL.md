---
name: improve-codebase-architecture
description: Scan a codebase for deepening opportunities, present them as a visual HTML report, then grill through whichever one you pick.
disable-model-invocation: true
---

# Improve codebase architecture

Surface architectural friction and propose **deepening opportunities**: refactors that turn shallow modules into deep ones. The aim is testability and AI-navigability.

This command is _informed_ by the project's domain model and built on a shared design vocabulary:

- Read [Deep Modules](../principles/deep-modules.md) for the architecture vocabulary (**module**, **interface**, **depth**, **seam**, **adapter**, **leverage**, **locality**) and its principles (the deletion test, "the interface is the test surface", "one adapter = hypothetical seam, two = real"). Use these terms exactly in every suggestion, and don't drift into "component," "service," or "API."
- The project's domain glossary, such as `CONTEXT.md`, gives names to good seams; its ADRs record decisions this command should not re-litigate.

## Process

### 1. Explore

**Scope before you scan: YAGNI.** Deepening a module pays off by making future changes to it easier, so put extra weight on the parts of the codebase that have recently changed. Decide *where* to look before you look:

- If the user named a direction (a module, a subsystem, a pain point), take it, and skip the inference below.
- Otherwise, walk back a good stretch of the commit history (`git log --oneline`) to find the codebase's hot spots, the files and areas that keep coming up, and let those paths pull your attention first. If the changes are scattered with no clear hot spot, widen the net.

Read the project's domain glossary and any ADRs in the area you're touching first, when available.

Then spawn a sub-agent to walk the codebase, with the scope, domain vocabulary, and principle guidance below. Don't follow rigid heuristics; explore organically and note where you experience friction:

- Where does understanding one concept require bouncing between many small modules?
- Where are modules **shallow**, with an interface nearly as complex as the implementation?
- Where have pure functions been extracted just for testability, but the real bugs hide in how they're called (no **locality**)?
- Where do tightly-coupled modules leak across their seams?
- Which parts of the codebase are untested, or hard to test through their current interface?

Apply the deletion test from [Deep Modules](../principles/deep-modules.md) to anything you suspect is shallow.

Read [Minimize Reader Load](../principles/minimize-reader-load.md), [Laziness Protocol](../principles/laziness-protocol.md), and [Subtract Before You Add](../principles/subtract-before-you-add.md) to judge which candidates are worth pursuing.

### 2. Present candidates as an HTML report

Write a self-contained HTML report with before/after visualizations, following [HTML-REPORT.md](HTML-REPORT.md). In T3 Code, deliver it through [t3-preview](../t3-preview/SKILL.md). Elsewhere, open it for the user and report the absolute path.

**ADR conflicts**: if a candidate contradicts an existing ADR, only surface it when the friction is real enough to warrant revisiting the ADR. Mark it clearly in the candidate section (e.g. a warning callout: _"contradicts ADR-0007, but worth reopening because…"_). Don't list every theoretical refactor an ADR forbids.

Do NOT propose interfaces yet. After the file is written, ask the user: "Which of these would you like to explore?"

### 3. Grilling loop

Once the user picks a candidate, follow [grilling](../grilling/SKILL.md) to walk the decision tree with them: constraints, dependencies, the shape of the deepened module, what sits behind the seam, what tests survive.

Read [Model the Domain](../principles/model-the-domain.md) and [Foundational Thinking](../principles/foundational-thinking.md) when deciding what the module owns. For representation and validation choices, read [Boundary Discipline](../principles/boundary-discipline.md) and [Type System Discipline](../principles/type-system-discipline.md). For shared writes or retry behavior, read [Separate Before Serializing Shared State](../principles/separate-before-serializing-shared-state.md) or [Make Operations Idempotent](../principles/make-operations-idempotent.md), respectively.

Follow [domain-modeling](../domain-modeling/SKILL.md) as terms and decisions resolve, passing along any identified glossary and ADR locations. Apply its ADR criteria to rejected candidates as well.

If the user wants to explore alternative interfaces for the deepened module, follow [Design It Twice](DESIGN-IT-TWICE.md).

When the user authorizes implementation of a behavior-preserving change, hand the chosen design to [refactoring](../refactoring/SKILL.md). A change to observable behavior belongs in [implement](../implement/SKILL.md). Reuse the decisions and evidence from this exploration.
