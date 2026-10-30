# Make operations idempotent

Apply when designing commands, lifecycle steps, or processing loops that run amid crashes, restarts, and retries.

Design operations so they converge to the correct state regardless of how many times they run or where the previous run stopped. Every state-mutating operation should answer: "What happens if this runs twice? What happens if the previous run crashed halfway?"

**Why:** Commands, lifecycle operations, and processing loops run where crashes, restarts, and retries are normal. If partial state changes the next run's outcome, every restart becomes a debugging session.

**The idea:** derive each step from the state that exists, not from what the previous run meant to do. Read the current state, compare it to the goal, and apply only the difference. A step written this way is safe to repeat, because a repeat finds nothing left to do.

Anything a run leaves behind, such as a lock, a temporary file, a half-written record, or a claimed job, must be recognizable as stale by the next run. Judge staleness by evidence that stays true wherever the previous run could have stopped, not only in the common case. A marker that the next run cannot judge turns every crash into manual cleanup.

**The pattern:**
- Convergent startup: scan for existing state, clean stale artifacts, adopt live sessions
- Content-based cleanup: compare by content equivalence, not creation order
- Atomic writes: write to a temporary path and rename it into place
- Self-healing locks: create the lock exclusively, for example by linking a fully written temporary file into place, which fails if the lock exists. Record the holder's PID, process start time, and boot ID, since a PID alone can be reused after a crash or reboot
- Idempotent scheduling: failed work respawns cleanly, fresh input regenerated after each cycle

**The test:**
1. What happens if this runs twice in a row?
2. What happens if the previous run crashed at every possible point?
3. Does re-execution converge to the same end state?

If any answer is "it depends on what state was left behind," the operation needs a reconciliation step.
