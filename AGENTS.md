# astack repository instructions

Edit global instructions in `instructions/`. Root `AGENTS.md` and `CLAUDE.md`
apply only to this repository.

## Skills

Before adding or editing a skill, read
[writing-for-agents](skills/writing-for-agents/SKILL.md) and follow its pointers.
Validate skill changes with `./install.sh --dry-run`.

Start upstream imports from the original. Preserve clear wording, structure,
and examples. Make targeted changes for compatibility or a specific demonstrated
problem, and compare each change against the original for clarity and meaning.

When adapting an upstream skill, record its repository, path, and commit in
`upstream/manifest.json`. Keep its license in the skill directory.
To review upstream changes, clone the source and diff from the pinned commit.

## Verification

Run `python3 -m unittest discover -s tests -v`. Keep tests offline and confined
to temporary directories. Use Python 3.10+, the standard library, and Git for
scripts, with support for macOS and Linux.

The directly imported orchestration runtime under `skills/orchestrate/scripts/`
retains upstream Bun/TypeScript to preserve its implementation and tests. This is
the exception to the Python script convention. Follow the verification steps in
[CLI.md](skills/orchestrate/CLI.md) to run its tests offline in a temporary copy.
