---
name: author-skill
description: Create, import, or modify skills in the astack repository. Use for changes to skill instructions, invocation, supporting resources, or callers.
---

# Author an astack skill

1. Identify the requested behavior and the smallest change that achieves it.
   Read the existing skill and its callers before adding another skill.
   Put shared skills in `skills/` and repository-local skills in `.agents/skills/`.
   For upstream material, follow [upstream imports](#upstream-imports).
2. Read [writing-for-agents](../../../skills/writing-for-agents/SKILL.md) and its
   skill-mechanics guidance. Trace changed invocation or actions through the
   caller and linked workflows. Check which prior acceptance results edits can
   invalidate and which external writes become reachable under the caller's scope.
3. Edit the source, reusing existing guidance through pointers. When moving
   guidance, update its callers and preserve the decisions it controls.
   Apply [technical-writing](../../../skills/technical-writing/SKILL.md) and
   [unslop](../../../skills/unslop/SKILL.md) to the diff.
4. For changed decisions, have an independent reviewer exercise a representative
   task and a case where the new behavior must not run. Give the reviewer the
   request, skill, and raw artifacts without suggested conclusions. If delegation
   is unavailable, exercise the cases yourself and report that limitation.
   Fix demonstrated gaps and repeat the affected cases.
5. Run `./install.sh --dry-run` and the repository's
   [verification checks](../../../AGENTS.md#verification). Run an available skill
   validator on touched skills. Check relative links in repository-local skills
   separately, since the installer validates only distributed skills.
6. For approved changes, follow [open-pr](../../../skills/open-pr/SKILL.md) to
   submit a PR unless the request is limited to a proposal or local edits.
   Explain the behavior change, verification, and untested branches. Keep private
   transcript details out of the PR. Report publication blockers with the local
   diff. Merging and reinstalling remain separate actions.

## Upstream imports

Start upstream imports from the original. Preserve clear wording, structure,
and examples. Make targeted changes for compatibility or a specific demonstrated
problem, and compare each change against the original for clarity and meaning.
For imported examples that claim to exclude invalid states, check a counterexample
under the stated compiler or runtime assumptions.

When adapting an upstream skill, record its repository, path, commit, and any
source-to-destination mappings in `upstream/manifest.json`. Keep its license in
the skill directory. Limit manifest notes to rationale the diff cannot explain.
To review upstream changes, clone the source and diff from the pinned commit.
