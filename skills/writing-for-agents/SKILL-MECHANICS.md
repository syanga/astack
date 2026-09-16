# Skill mechanics

The skill-specific branch of [`writing-for-agents`](SKILL.md). It covers what changes when the document is a skill: frontmatter, the invocation choice, and router skills. Everything else about writing it is the universal reference in `SKILL.md`.

## Invocation

Two choices, trading the two loads:

- A **model-invoked** skill keeps a `description`, so the agent can fire it autonomously, and other skills can reach it. You can still type its name. Model-invocation always _includes_ user reach; a description only ever adds agent discovery and never removes the human's. The description is the skill's top-level context pointer, forced to stay loaded at all times, so it trades permanent context load for discoverability. A model-invoked skill whose content is all reference is also one home for shared reference. Another skill can invoke it, so reference needed by several skills lives in one place. To make a skill model-invoked, omit `disable-model-invocation` and write a model-facing description carrying the trigger branches (the pointer-writing rules in `SKILL.md` apply in full).
- A **user-invoked** skill strips the description from the agent's reach. Only the human typing its name can invoke it, and no other skill can. It costs zero context load but spends cognitive load, because you are the index that must remember it exists. To make a skill user-invoked, set `disable-model-invocation: true`. The `description` then becomes human-facing, a one-line summary with trigger lists stripped.

Pick model-invocation only when the agent must reach the skill on its own, or another skill must. If it only ever fires by hand, make it user-invoked and pay no context load.

Shared reference that two user-invoked skills both need can live in neither, because with no descriptions neither can fire the other. Push it to a plain file outside the skill system, as external reference any skill can point at.

## Splitting by invocation

This is the invocation cut of splitting. The sequence cut lives in `SKILL.md`. Split off a model-invoked skill when you have a distinct leading word that should trigger it on its own (a trigger word you actually use in your prompts), or another skill must reach it. You pay context load for the new always-loaded description, so that independent reach has to be worth it.

## Router skills

When user-invoked skills multiply past what you can remember, a **router skill** cures that piled-up cognitive load. It is one user-invoked skill that names the others and when to reach for each, so the human has one skill to remember instead of many. It can only hint, never fire them, because user-invoked skills have no description and nothing but the human can reach them.
