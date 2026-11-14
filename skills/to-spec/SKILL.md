---
name: to-spec
description: Implementation specs. Use when asked to turn the current conversation into a spec.
---

This skill takes the current conversation context and codebase understanding and produces a spec. Do NOT interview the user; just synthesize what you already know.

Some projects call their test files specs, such as RSpec `*_spec.rb` files and Jest `*.spec.js` files. In such a project, these requests ask for an implementation spec:

- a request whose subject is the conversation, such as "turn this into a spec" or "write a spec for what we discussed"
- a request for an implementation or design spec
- a request that names this skill, directly or through a workflow the user started

In such a project, any other request to write a spec asks for a test. Write that test instead of following this skill.

Deliver the spec where the user asks. If the user names no destination, write the spec to the project's local location for implementation specs. If the project has none, return the spec in the conversation. Publish to an issue tracker or another external system only when the user asks, directly or through a workflow they started. Use the project's actual labels there. If the project keeps implementation specs on such a system, offer to publish there.

## Process

1. Explore the repo to understand the current state of the codebase, if you haven't already. Use the project's domain glossary vocabulary throughout the spec, and respect any ADRs in the area you're touching.

2. Sketch out the seams at which you're going to test the feature. Existing seams should be preferred to new ones. Use the highest seam possible. If new seams are needed, propose them at the highest point you can. The fewer seams across the codebase, the better - the ideal number is one.

Reuse testing boundaries already agreed in the conversation. Mark unresolved boundary choices as proposals rather than starting a new interview. Read [Test Behavior, Not Implementation](../principles/test-behavior-not-implementation.md) for the shared test-quality rules.

3. Write the spec using the template below, then deliver it to the destination chosen above. Separate settled decisions from assumptions and open questions. State whether any open question blocks implementation.

<spec-template>

## Problem Statement

The problem that the user is facing, from the user's perspective.

## Solution

The solution to the problem, from the user's perspective.

## User Stories

A numbered list of user stories covering the distinct actors and outcomes. Each user story should be in the format of:

1. As an <actor>, I want a <feature>, so that <benefit>

<user-story-example>
1. As a mobile bank customer, I want to see balance on my accounts, so that I can make better informed decisions about my spending
</user-story-example>

Cover the specified behavior and important failure cases. Scale the list to the feature.

## Implementation Decisions

A list of implementation decisions that were made. This can include:

- The modules that will be built/modified
- The interfaces of those modules that will be modified
- Technical clarifications from the developer
- Architectural decisions
- Schema changes
- API contracts
- Specific interactions

Do NOT include specific file paths or code snippets. They may end up being outdated very quickly.

Exception: if a prototype produced a snippet that encodes a decision more precisely than prose can (state machine, reducer, schema, type shape), inline it within the relevant decision and note briefly that it came from a prototype. Trim to the decision-rich parts, not a working demo, just the important bits.

## Testing Decisions

A list of testing decisions that were made. Include:

- A description of what makes a good test (only test external behavior, not implementation details)
- Which modules will be tested
- Prior art for the tests (i.e. similar types of tests in the codebase)

## Out of Scope

A description of the things that are out of scope for this spec.

## Further Notes

Any further notes about the feature, including assumptions and unresolved decisions that block implementation.

</spec-template>
