# Fix root causes

Apply when debugging.

Trace every problem to its root cause and fix it there.

**Why:** Symptom fixes accumulate. Each workaround makes the system harder to reason about, and the real bug remains. Root-cause fixes are slower upfront but reduce total debugging time.

**Pattern:**
- Reproduce first
- Ask "why" until you hit the root cause
- Do not add guards to silence a symptom. Ask whether the state that triggered the symptom is legitimate. If the system may be in that state, the code that broke on it is the root cause, and handling the state there is the fix. If the state should never exist, a check where it broke hides the bug, so find what produced the state. Guards, retries, swallowed errors, clamped values, and discarded state are the usual ways to hide a bug. Validation belongs at the boundary, per [`boundary-discipline.md`](boundary-discipline.md)
- If a workaround for code we control needs a paragraph-long comment to justify it, fix the code. For explanations of constraints imposed by external dependencies, follow [no-comments](../no-comments/SKILL.md).
- Search for the same pattern with grep. Fix every instance inside the change's scope and note the rest
- When stuck, instrument. Don't guess (add logging, read the actual error)
- If the deciding fact took a deep dig to find, propose the cheap, always-on signal that would have shown it at first look

**Restart bugs: suspect state before code**

When something "fails after restart," suspect stale persistent state first: config files, caches, lock files, serialized state. If clearing a state file restores behavior, prioritize state validation as the fix.
