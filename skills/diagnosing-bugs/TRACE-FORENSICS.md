# Trace forensics

**You own the diagnosis from the artifact. Load it, shape it, narrow to the cause, attribute to source.**

Here the capture already exists. Analyze that fixed dataset. For a new capture or live mechanism check, use [runtime forensics](RUNTIME-FORENSICS.md) within the task's authorization. Use tools suited to the format: DevTools or a trace parser for cpuprofile and `.json.gz`, a text editor for a spindump, heap tooling for a heapsnapshot. Redact secrets and personal data from excerpts and shared artifacts.

1. Identify the format, capture conditions, and code revision when available. Load it with the right tool. For large artifacts, delegate parsing with the available subagent tools and keep the reduced finding in the main thread. If delegation is unavailable, query the artifact directly and limit output to the relevant records.
2. Reach a queryable representation before drawing conclusions. Use the tool's native queries, or transform a copy into SQLite with sample, frame, or node records. Preserve timestamps, units, call-tree edges, and heap references. Keep the original intact. A strict no-writes request requires in-memory analysis or an existing query tool.
3. Narrow to the cause. Query for the frames that hold the most time and walk the call tree to the hot path. Distinguish self time from inclusive time. For a leak, follow the retainer chain from the suspected object to a GC root. Retention alone does not prove a leak. Compare it with the object's expected lifetime. For a spindump, find the thread on-CPU or blocked and its wait reason.
4. Attribute to source. Map the relevant frame to file, symbol, and line using symbols or source maps for the captured revision. Resolve missing symbols when possible. Otherwise report the unresolved frames and limit source attribution accordingly.
5. Compare against a paired capture when available. Check that workload and capture settings are comparable before interpreting the difference. A correlation between the captures supports a hypothesis. Confirmation needs evidence that distinguishes the proposed mechanism from competing causes. Without that evidence, label the causal conclusion provisional.
6. Hand back a cited diagnosis. When implementation is requested, route to [bug-fix](../bug-fix/SKILL.md) or [perf-issue](../perf-issue/SKILL.md). Carry the evidence forward. A capture alone does not satisfy their reproduction or fix-verification gates.

**Reply:** artifact and format, reduced finding with query or record identifiers, source location and revision, artifact paths, comparison results, and what remains unconfirmed.
