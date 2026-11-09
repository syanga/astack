---
name: claude-full-send-guard
description: Claude Code hook that stops a full-send program from ending the turn while units are unfinished and nothing is running.
disable-model-invocation: true
---

# Keep a full-send program moving

`scripts/hook.py` is a Claude Code `Stop` hook. It acts when the session's history
holds the user's `/full-send` command, including history that a broken resume
dropped from context. After that command, [full-send](../full-send/SKILL.md) posts
the absolute path of the store's `assumed-decisions.md`. The hook reads the
program's store from the first such path in the coordinator's own text, and
ignores paths in tool output. It does nothing for a store without a `units.tsv`.

The hook blocks the end of the turn when all of these hold:

- No unit in `units.tsv` is `running`.
- Some unit is unfinished. A unit is finished when it is `done`, `abandoned`, or
  `zombie-reconciled`. While a gate is open, a `blocked` unit also counts as
  finished.
- The latest dispatch order in `preferences.md` is not `Dispatch paused`.
- Claude Code reports no background task or scheduled wakeup in the session.

The block names the store, the unfinished units, and the open gates. The model
either starts ready work or records in the store why the program waits. Then it
ends the turn. Claude Code marks that second stop with `stop_hook_active`, and
the hook lets it end. A later turn that ends idle is blocked again.

Unit dependencies live in the plan, so the hook cannot tell a ready unit from a
unit that waits on another unit or a gate. The model judges readiness, and it
judges whether "no merge" finishes a `merge-ready` unit. Each of these cases
costs one blocked stop per turn end:

- A gate holds a `needs-verify` or `merge-ready` unit, which cannot be set
  `blocked`.
- A `blocked` unit waits on something without an open gate.
- A PR watch or a T3 Code delegate runs, because Claude Code does not report work
  outside the session. The coordinator avoids the delegate case by setting the
  unit `running`.
- A program under "no merge" has finished.

Gates do not name their units. So while any gate is open, every `blocked` unit
counts as finished, including one whose own gate was answered. A `running` unit
whose worker has stopped also keeps the hook from blocking, and so does a
background task that never exits, such as a dev server. When the hook cannot read
the transcript or the store, it lets the turn end.
