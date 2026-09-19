---
name: prototype
description: Build a throwaway prototype to resolve a UI, state-model, or behavioral design question before implementation.
---

# Prototype

Name the decision the prototype must resolve. Use the prompt and current
discussion to choose its shape:

- For layout, interaction, or copy choices, follow [UI.md](UI.md).
- For state transitions, business rules, or data shape, follow [LOGIC.md](LOGIC.md).
- For a behavioral or timing comparison, use the smallest isolated script that
  measures the relevant output or timing under comparable inputs.

State an assumption if the question is ambiguous and a useful experiment can
proceed. Ask when choosing the wrong branch would waste the work.

Build in a clearly named scratch directory, separate from production source.
Use in-memory sample data by default. If persistence is the subject of the
experiment, use an isolated scratch store within the user's authorized scope.

Keep the prototype cheap to discard. Add only what makes the decision
observable. Verify it by running the scenario or inspecting the rendered
variants, without building a production test suite around it.

Deliver HTML through [t3-preview](../t3-preview/SKILL.md). Report the decision,
alternatives, observed evidence, recommendation, and artifact link. Distinguish
checks you ran from those left for the user. State that the artifact is throwaway.

For a visual choice, stop for the user's selection before editing real
components. Once a decision is made, capture its rationale and artifact pointer
in the spec or task record. Use [implement](../implement/SKILL.md) for authorized
production work. Carry forward validated behavior with normal verification.
Archive prototype branches or update external issues only when that delivery is
part of the task.
