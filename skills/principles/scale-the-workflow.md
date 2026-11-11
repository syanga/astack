# Scale the workflow

Apply when a fixing workflow, such as bug-fix or perf-issue, decides whether to delegate, run architect, or start its own review.

A request to fix something asks for the investigation, design, and review its workflow runs. Spend that effort where it changes the result.

- **Delegation.** Delegate a part of the work when it is large or splits into independent parts, such as competing hypotheses to test in parallel. Make a small change yourself, then review your diff as a separate pass.
- **Design.** When a fix changes an interface between modules, run [architect](../architect/SKILL.md). When it crosses a function boundary inside one module, sketch the changed signatures before writing the code.
- **Delegated runs.** When a coordinator's brief assigns you the work, its review and delivery steps replace the workflow's own. Start subagents only when the brief says to. Report anything that needs the user instead of waiting for an answer, and return your evidence to the coordinator.
