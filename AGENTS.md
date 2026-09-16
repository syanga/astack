# astack repository instructions

Global instruction sources live in `instructions/`. Reusable skills live in
`skills/<skill-name>/SKILL.md`; `templates/skill/` is an uninstalled starter.
This file and root `CLAUDE.md` govern work on astack and are not installed globally.

## Adding a skill

Read `skills/writing-for-agents/SKILL.md` and its `SKILL-MECHANICS.md` first;
they are the single source of truth for how a skill is written. The decisions
they own: user-invoked or model-invoked, a description that is a trigger rather
than a summary, sibling files behind pointers for material only some runs need,
and an unslop pass over the prose. For an adaptation of an upstream skill, record the source repository, path,
and commit in `upstream/manifest.json` and keep the upstream LICENSE in the skill
directory; to see what changed upstream since, clone the source and diff from
the pinned commit. Finish with
`./install.sh --dry-run`, which enforces the structural rules: the name matches
the directory, the description fits 200 characters, relative links resolve, and
any shipped Codex policy file agrees with `disable-model-invocation`.

## Verification

Run `python3 -m unittest discover -s tests -v`. Tests use temporary directories
and must not depend on network access. Keep scripts compatible
with Python 3.10+, macOS, and Linux using the standard library and Git.
