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
For imported examples that claim to exclude invalid states, check a counterexample
under the stated compiler or runtime assumptions.

When adapting an upstream skill, record its repository, path, commit, and any
source-to-destination mappings in `upstream/manifest.json`. Keep its license in
the skill directory. Limit manifest notes to rationale the diff cannot explain.
To review upstream changes, clone the source and diff from the pinned commit.

Put reusable lessons in the applicable skill or principle, and change history in
the PR. Keep adaptation records in the manifest and PR instead of separate
adaptation reports. Discover skills through `astack-help` and installed metadata;
do not maintain skill inventories in the README or other overview documents.

## Verification

Run `python3 -m unittest discover -s tests -v`. Keep tests offline and confined
to temporary directories. Use Python 3.10+, the standard library, and Git for
scripts, with support for macOS and Linux.

The directly imported orchestration runtime retains Bun/TypeScript, and
`skills/show-me-your-work/scripts/log.sh` retains Bash. These upstream imports
are exceptions to the Python script convention. Follow the verification steps in
[CLI.md](skills/orchestrate/CLI.md) to run the orchestration tests offline in a
temporary copy. The Python suite also tests the imported decision-log helper.
