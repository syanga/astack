# astack repository instructions

Edit global instructions in `instructions/`. Root `AGENTS.md` and `CLAUDE.md`
apply only to this repository.

## Skills

Before creating, importing, or modifying a skill, follow the repository-local
[author-skill workflow](.agents/skills/author-skill/SKILL.md).

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
