# Scale the workflow

Apply in bug-fix and perf-issue when deciding whether to delegate, run architect, or run review and delivery.

- **Delegation.** Delegate when the work splits into independent parts, such as competing hypotheses, or when its reads would crowd your context. Otherwise make the change yourself, then review your diff in a separate pass.
- **Design.** When a fix changes an interface between modules, run [architect](../architect/SKILL.md) and pass it your code reading as its grounding. When a fix crosses a function boundary inside one module, sketch the changed signatures before writing the code.
- **Delivery.** Without a request for commits or a PR, the delivery scope is local edits.
- **Delegated runs.** When another agent dispatched you, its prompt is your brief. Start from the evidence, mechanism, and design it supplies, and skip the steps they cover. Run the review and delivery the brief names in place of the workflow's own, and end at verification when it names none. When the brief asks for a PR but names no review, open the PR with [open-pr](../open-pr/SKILL.md) and skip open-pr's closing review-pr. Start subagents or outside-model runs, including through how, why, architect, or review-pr, only when the brief says to. Return your evidence, and any question for the user, to that agent.
