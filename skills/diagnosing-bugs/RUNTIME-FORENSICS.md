# Runtime forensics

**You own the diagnosis. Capture runtime evidence and test the mechanism.** The deliverable is a cited diagnosis.

Use an authorized development or isolated test environment. A diagnosis-only request can permit temporary probes; a read-only request does not authorize instrumentation or code edits. Production and daily-driver processes require explicit authorization. If access or scope prevents a capture, report the gap and analyze existing evidence.

1. Capture the signal through the affected UI, CLI, or API with available tools: a CPU profile for a spinning process, a heap snapshot for a suspected leak, a browser trace for a visual glitch. Record the reproduction steps, workload, revision, configuration, and capture command. Redact secrets and personal data from shared output.
2. Follow [trace forensics](TRACE-FORENSICS.md) to reduce the artifact to a finding: the function on the hot path, the retainer chain from an object to a GC root, or the loop firing without input. Keep reduced evidence and artifact pointers in the main thread.
3. Test the mechanism with a specific prediction. In an environment that permits temporary probes, use a debugger, targeted instrumentation, or a reversible runtime patch. Change one variable at a time and compare the signal. Distinguish a confirmed mechanism from an unresolved hypothesis.
4. Map the finding back to source at the captured revision: file, symbol, and the line that allocates or schedules. State when missing symbols prevent attribution.
5. Remove temporary probes and runtime patches you introduced, preserving unrelated changes. Retain captures needed to support the diagnosis and name their paths. If cleanup is blocked, record exactly what remains and how to remove it.

**Reply:** signal captured, reduced finding, mechanism evidence and its limits, source location, artifact paths, and cleanup status. When implementation is requested, hand off to [bug-fix](../bug-fix/SKILL.md) or [perf-issue](../perf-issue/SKILL.md).
