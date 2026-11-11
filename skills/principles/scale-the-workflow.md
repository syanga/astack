# Scale the workflow

Apply in a workflow that links this file, when deciding whether to delegate, how to ground a linked skill, or how to review and deliver.

A request to fix or diagnose a problem asks for the work its workflow runs. Cut only the work that repeats or nests.

**Why:** repeated or nested work adds cost without improving the fix.

- **Delegation.** Delegate when the work splits into independent parts, such as competing hypotheses, or when its reads would crowd your context. Otherwise make the change yourself, then review your diff in a separate pass. The closing review still gives the fix an independent reviewer. A delegation brief carries your traced model and any architect design, along with what the calling skill lists.
- **Grounding.** When a linked skill such as architect or hillclimb would run how, pass it the traced model you already hold, so it covers only what that model leaves out.
- **Delivery.** Without a request for commits or a PR, the delivery scope is local edits, and the closing review compares them against `HEAD`.
- **Delegated runs.** When bug-fix, perf-issue, or hillclimb dispatched you to do part of its work, its prompt is your brief. Start from the evidence, mechanism, and design it supplies, and skip the steps they cover. Run the review and delivery the brief names in place of the workflow's own. When the brief names neither, end at verification. Start subagents or outside-model runs, including through how, why, or architect, only when the brief says to. A PR the brief asks for still gets the review-pr run that [open-pr](../open-pr/SKILL.md) ends with. Return your evidence, and any question for the user, to that agent.
