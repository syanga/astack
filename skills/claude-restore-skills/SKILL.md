---
name: claude-restore-skills
description: Claude Code hook that restores the text of user-invoked skills after a compaction or resume drops it from context.
disable-model-invocation: true
---

# Restore user-invoked skills after compaction or resume

`scripts/hook.py` is a Claude Code `SessionStart` hook for the `resume` and
`compact` sources. It reads the session transcript and finds each skill the user
invoked with a slash command. For each one whose text is no longer in context,
it adds the skill's current `SKILL.md` text and the invocation's arguments. When
the file is gone, it uses the text recorded at invocation. It skips skills the
model invoked through the Skill tool, because the model can invoke those again.

The hook also skips invocations on branches abandoned by a rewind. When the
first entry after a rewind has a parent missing from the transcript, the hook
cannot tell the abandoned branch from lost entries, and it can restore that
branch's skills.

The hook splits its output into parts. The comment on `PARTS` in
`scripts/hook.py` says why. When the skills do not fit in all parts together,
the hook leaves out the oldest skills until the rest fit and lists their files
and arguments. If the newest skill alone does not fit, the hook cuts it and
points to its file.

After a compaction, Claude Code sometimes attaches its own copy of a skill, cut
at 20,000 characters. It attaches that copy after the hook runs, so the skill
arrives twice. On a later resume, the hook skips a skill whose copy in context
is complete.
