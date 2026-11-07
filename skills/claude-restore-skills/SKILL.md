---
name: claude-restore-skills
description: Claude Code hook that restores the text of user-invoked skills after a compaction or resume drops it from context.
disable-model-invocation: true
---

# Restore user-invoked skills after compaction or resume

`scripts/hook.py` is a Claude Code `SessionStart` hook for the `resume` and
`compact` sources. It reads the session transcript and finds each skill the user
invoked with a slash command. For each one whose invocation is no longer in
context, it adds the skill's current `SKILL.md` text and the invocation's
arguments. When the file is gone, it uses the text recorded at invocation. It
skips invocations on branches abandoned by a rewind, and skills the model
invoked through the Skill tool, because the model can invoke those again.

A compaction drops every earlier invocation. Claude Code sometimes attaches a
copy, and the hook skips a skill whose copy is complete. A resume rebuilds
context from the newest entry's parent chain, back to the last compaction or to
a parent missing from the transcript.

Claude Code saves hook context over 10,000 characters to a file and shows the
model a 2 KB preview, and agents do not reliably read the file.
`settings/claude.json` registers the hook once per part (`PARTS` in the
script), and each run prints one part under that limit. Claude Code joins hook
output in the order the processes exit, so each part waits until the previous
part's process has exited. When the skills need more than every part, the hook
leaves out the oldest skills and lists their files and arguments. A single
skill too large for every part is cut, with a pointer to its file.
