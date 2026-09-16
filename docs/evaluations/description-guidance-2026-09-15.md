# Agent run: skill description guidance

Question: does sharpening the "Cut identity the body already carries" bullet in
`skills/writing-for-agents` stop agents from writing summary-style descriptions?

## Setup

Four Opus 5 subagents, one per isolated sandbox under `/tmp`. Each sandbox held
copies of `skills/writing-for-agents` and `skills/unslop` and the same 224-character
draft of a preview skill. Two sandboxes had the upstream six-word bullet; two had
the sharpened bullet. All four received the identical prompt: turn the draft into
a model-invocable `t3-preview` skill, reading writing-for-agents first and applying
unslop after. The prompt did not mention descriptions, length, or a comparison.
Grading read the written files, not the agents' summaries.

## Results

| Sandbox | Bullet | Description length | Shape |
| --- | --- | --- | --- |
| p | upstream | 209 | identity list plus three triggers naming tools |
| q | upstream | 151 | short identity plus two triggers |
| r | sharpened | 148 | one naming clause plus two triggers |
| s | sharpened | 158 | one naming clause plus two triggers |

Both sharpened-arm agents produced the prescribed shape and independently chose
the same opening clause. The upstream arm split. No agent followed the draft
bullet's "in one sentence"; all four wrote two sentences, so that phrase was
dropped from the adopted wording. Secondary behaviour (dropping out-of-scope
sections, removing the user's name, positive phrasing, no long dashes) was the
same across arms.

## Decision

Adopt the sharpened bullet without the sentence-count clause. Add a 200-character
description cap to the installer, which would have rejected exactly the one
summary-style output and passed the other three. Two samples per arm is a strong
signal, not proof; rerun if the pattern recurs.
