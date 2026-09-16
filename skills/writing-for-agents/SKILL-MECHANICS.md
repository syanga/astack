# Skill mechanics

The skill-specific branch of [`writing-for-agents`](SKILL.md). It covers what changes when the document is a skill: frontmatter, the invocation choice, and router skills. Everything else about writing it is the universal reference in `SKILL.md`.

## Invocation

Two choices, trading the two loads:

- A **model-invoked** skill keeps a `description`, so the agent can fire it autonomously, and other skills can reach it. You can still type its name. Model-invocation always _includes_ user reach; a description only ever adds agent discovery and never removes the human's. The description is the skill's top-level context pointer, forced to stay loaded at all times, so it trades permanent context load for discoverability. A model-invoked skill whose content is all reference is also one home for shared reference. Another skill can invoke it, so reference needed by several skills lives in one place. To make a skill model-invoked, omit `disable-model-invocation` and write a model-facing description carrying the trigger branches (the pointer-writing rules in `SKILL.md` apply in full).
- A **user-invoked** skill strips the description from the agent's reach. Only the human typing its name can invoke it, and no other skill can. It costs zero context load but spends cognitive load, because you are the index that must remember it exists. To make a skill user-invoked, set `disable-model-invocation: true`; each harness spells this differently, see Harnesses below. The `description` then becomes human-facing, a one-line summary with trigger lists stripped.

Pick model-invocation only when the agent must reach the skill on its own, or another skill must. If it only ever fires by hand, make it user-invoked and pay no context load.

A user-invoked skill is out of the skill tool's reach, but not out of the file system's. Skills install as sibling directories under one `skills/` folder in every harness, so any skill can read another by relative path, for example `../unslop/SKILL.md`. Shared reference can therefore live in a user-invoked skill and stay out of the always-loaded catalog. Point at it by relative path from the skills that need it.

## Splitting by invocation

This is the invocation cut of splitting. The sequence cut lives in `SKILL.md`. Split off a model-invoked skill when you have a distinct leading word that should trigger it on its own (a trigger word you actually use in your prompts), or another skill must reach it. You pay context load for the new always-loaded description, so that independent reach has to be worth it.

## Router skills

When user-invoked skills multiply past what you can remember, a **router skill** cures that piled-up cognitive load. It is one user-invoked skill that names the others and when to reach for each, so the human has one skill to remember instead of many. It can only hint, never fire them through the skill tool, because user-invoked skills have no description in the catalog.

## Harnesses

`disable-model-invocation: true` is Claude Code's spelling. The others differ, and two of them have no user-invoked mode at all.

| Harness | User-invoked mechanism | Catalog cost |
| --- | --- | --- |
| Claude Code | `disable-model-invocation: true` in frontmatter | description truncated at 1536 characters |
| Codex | `policy.allow_implicit_invocation: false` in `agents/openai.yaml`; the astack installer writes it from the frontmatter flag | whole catalog capped at 8000 characters or 2% of context |
| Gemini CLI | none, every enabled skill is listed | every description injected each session |
| OpenCode | none, every skill is listed | every description in the skill tool |

Two consequences. Write every description as if the model will read it, because on two harnesses it will. Keep the model-invoked set small, because forty skills at 200 characters fill Codex's cap.

## Done when

A skill is finished when the frontmatter `name` matches its directory, the description reads as a pointer and fits the 200-character cap, every file it links to exists, and the prose has been through the unslop checklist. The astack installer checks the first three mechanically, so a dry run is the quickest test.
