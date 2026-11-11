# Skill mechanics

The skill-specific branch of [`writing-for-agents`](SKILL.md). It covers what changes when the document is a skill: frontmatter, the invocation choice, and router skills. Everything else about writing it is the universal reference in `SKILL.md`.

## Invocation

Two choices, each spending one of the two loads:

- A **model-invoked** skill keeps a `description`, so the agent can fire it autonomously, and other skills can reach it. You can still type its name. Model-invocation always _includes_ user reach; a description only ever adds agent discovery and never removes the human's. The description is the skill's top-level context pointer, forced to stay loaded at all times, so it trades permanent context load for discoverability. A model-invoked skill whose content is all reference is also one home for shared reference. Another skill can invoke it, so reference needed by several skills lives in one place. To make a skill model-invoked, omit `disable-model-invocation` and write a model-facing description carrying the trigger branches (the pointer-writing rules in `SKILL.md` apply in full).
- A **user-invoked** skill strips the description from the agent's reach. Only the human typing its name can invoke it, and no other skill can. It costs zero context load but spends cognitive load, because you are the index that must remember it exists. To make a skill user-invoked, set `disable-model-invocation: true`; Codex handles this differently, see Harnesses below. The `description` then becomes human-facing, a one-line summary with trigger lists stripped (see Router skills for the one exception).

Model-invocation is the default, because a user-invoked skill can fail to load when the same message names another skill or command. A model-invoked skill loads wherever the message names it. A niche skill is model-invoked too.

Make a skill user-invoked when a request that matches its description could set off work beyond what the user has asked for. Count the work that the skill starts and the work that a linked skill starts when the agent follows it. Such work is either of these:

- An action outside the conversation that is hard to undo, such as merging PRs, writing issue-tracker tickets, reinstalling skills, editing global guidance, or adding or editing installed skills.
- Subagent fan-out, another provider's model, or long-running work.

A request to ship a PR asks for the merge, and a request to review a PR asks for reviewer subagents. A request for an outcome asks for the work its workflow needs to deliver it, when the workflow scales delegation to the task and a delegated run starts no fan-out of its own, as [scale the workflow](../principles/scale-the-workflow.md) describes. A request to fix a bug or a performance problem is one. A quick question about how code works does not ask for subagent fan-out. Work that an earlier request authorized, such as a review that a running workflow requires before it hands back, counts as asked for. A skill whose procedure is an interview, waiting for the user's answers each round, triggers only when the user asks for it, because a headless run or a delegated worker cannot answer.

A skill that only documents a harness hook is user-invoked too, because the hook runs without the agent.

A user-invoked skill is out of the skill tool's reach, but not out of the file system's. Skills install as sibling directories under one `skills/` folder in every harness, so any skill can read another by relative path, for example `../orchestrate/STATE.md`. Shared reference in a user-invoked skill therefore stays reachable. Point at it by relative path from the skills that need it.

## Splitting by invocation

This is the invocation cut of splitting. The sequence cut lives in `SKILL.md`. Split off a skill when you have a distinct leading word that should trigger it on its own (a trigger word you actually use in your prompts), or another skill must reach it. The new skill is model-invoked by default, so its always-loaded description adds context load, and that independent reach has to be worth it.

## Router skills

When user-invoked skills multiply past what you can remember, a **router skill** cures that piled-up cognitive load. It is one skill that names the others and when to reach for each, so the human has one skill to remember instead of many. The router's description is the one always-loaded mention of the user-invoked skills. The router can only hint, never fire them through the skill tool, because user-invoked skills have no description in the catalog. A router can read the installed skills' frontmatter instead of naming them. A user-invoked description such a router reads may keep one trigger clause when its summary alone does not tell the router when to recommend the skill.

## Harnesses

`disable-model-invocation: true` is Claude Code's spelling. Codex sets it in `agents/openai.yaml`.

| Harness | User-invoked mechanism | Catalog cost |
| --- | --- | --- |
| Claude Code | `disable-model-invocation: true` in frontmatter | description truncated at 1536 characters |
| Codex | `policy.allow_implicit_invocation: false` in `agents/openai.yaml` | As of 0.166, about 2% of the model's context, or 8000 characters when the window is unknown. Over that, Codex cuts the longest descriptions to a shared length. When names alone exceed the budget, it removes descriptions and drops later entries. |

## Done when

Before finishing, check that the frontmatter `name` matches its directory, the description reads as a trigger when the skill is model-invoked or as a one-line summary when it is user-invoked (see Router skills for the one exception), every file it links to exists, and the prose has been through the unslop checklist. If the skill ships its own `agents/openai.yaml`, that file sets `allow_implicit_invocation: false` when the skill is user-invoked, and only then.

When an edit changes decisions or stopping conditions, exercise representative cases for those branches. For new callers or supported uses, trace a representative case through existing eligibility filters and completion instructions. Check behavior as well as packaging, and report any branches that remain untested.
