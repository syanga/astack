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
   skill-mechanics guidance.
3. For a behavior change, take the before runs that writing-for-agents'
   [Test a behavior change](../../../skills/writing-for-agents/SKILL.md#test-a-behavior-change)
   describes, with [run_case.py](scripts/run_case.py). Edit only after one of
   them shows the behavior the request wants changed. If none does, or the runs
   cannot start, report the runs to the user and stop.
4. Edit the source, reusing existing guidance through pointers. When moving
   guidance, update its callers and preserve the decisions it controls.
   Apply [technical-writing](../../../skills/technical-writing/SKILL.md) and
   [unslop](../../../skills/unslop/SKILL.md) to the diff. For a behavior change,
   take the after runs. Revise until the edit works as Test a behavior change
   defines. If no revision works, report the runs to the user and stop.
5. After writing or modifying the skill, run two independent subagent reviews:

   - Writing: apply the linked writing guidance. Check for redundancy across
     skills, unnecessary rules, excessive prose, and misplaced guidance.
   - Behavior: check the request against the skill and its callers. Trace changed
     invocation, stopping conditions, invalidated acceptance results, and external
     writes through linked workflows. For changed behavior, check that the before
     and after runs cover the changed branches. Run any branch they miss with
     run_case.py.

   Give both reviewers the request, diff, repository rules, and referenced files
   without suggested conclusions. Reviewers report concrete findings without
   editing or starting subagents. Assess the findings and fix demonstrated
   problems. After a fix, rerun the after runs it affects. Repeat affected
   reviews until clean or blocked. Report unresolved blockers.
   If subagents are unavailable, perform both reviews yourself and report that
   they were not independent.
6. For approved changes, follow [open-pr](../../../skills/open-pr/SKILL.md) to
   submit a PR unless the request is limited to a proposal or local edits.
   Explain the behavior change, the before and after run counts on the final
   text, and untested branches. Keep private transcript details out of the PR.
   Report publication blockers with the local diff. Merge only when authorized.
   If the PR merges, ask whether to reinstall unless the user already
   authorized it.

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
