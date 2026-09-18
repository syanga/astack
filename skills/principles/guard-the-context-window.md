# Guard the context window

Apply when context is filling up with large outputs, long files, repeated reads, or plans for parallel work.

The context window is finite. Load the material needed for the current decision.

**Why.** Context overflow degrades reasoning quality, creates compression artifacts, and halts progress.

## Pattern

- **Isolate large payloads.** Route verbose outputs, screenshots, and large documents to subagents. The main context gets summaries, not raw data.
- **Don't read what you won't use.** Read selectively based on relevance. If a file isn't needed for the current task, skip it.
- **Keep shared steps inline.** Keep instructions every invocation needs in the skill file. Put material only some runs need behind a pointer, following [writing-for-agents](../writing-for-agents/SKILL.md).
- **Size phases and cap scope.** Limit files per phase, set turn budgets, account for mechanism costs.
