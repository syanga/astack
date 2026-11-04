---
name: hillclimb-council
description: Hillclimb a quality that models can judge but no command can measure, such as UI design or writing, scored by a blind council.
disable-model-invocation: true
---

# Hillclimb council

Follow [hillclimb](../hillclimb/SKILL.md) with a council vote as its metric. Each section names the hillclimb steps it replaces. Hillclimb owns the rest.

## Set up the vote

This replaces the workload, repro, and metric in hillclimb step 1, and the measurement harness in step 2.

1. Write the judge brief from the request: what the judges assess, for example the design quality of these screens. Leave out the target.
2. Choose the input. For visual work, write a capture script that takes the same screenshots every round, for example through [t3-preview](../t3-preview/SKILL.md). For text, the judges read the files. Add any reference examples the user supplies, such as competitor screenshots.
3. Ask the user, with the question tool, to approve the brief and, for visual work, the screen list. When the user delegated this pick, record both with the task's other decisions instead. Then freeze the brief, capture script, and references.

The target defaults to 9 out of 10 and the budget to 10 rounds.

## Vote each round

1. Look at the inputs before you send them, and fix broken ones, such as a blank screenshot or a stale build. This replaces the sensitivity proof in hillclimb step 2.
2. Copy the brief, the new version, the current best, and the references into a new temporary directory outside the repository, in random order, each under a random filename. Start fresh judges from [council's members](../council/SKILL.md#members), read-only, and tell them to read only that directory. Ask for a score out of 10 and the biggest flaw for each item. The judges must not learn the target, earlier scores, the round number, or which version is new.
3. Keep the new version only when every judge scores it above the current best and the correctness checks stay green. This replaces the noise threshold in hillclimb step 5. The current best's scores in round 1 are the baseline.
4. Pass the judges' flaws into the next hypothesis and change.

If a judge fails or returns no score, pause the run and ask the user. A vote needs every judge.

## Stop

This replaces the stop rules in hillclimb steps 6 and 7. Stop when the first of these happens:

- Every judge scores the accepted version at or above the target and above every reference. This vote also serves as the recheck in hillclimb step 8, so rerun only the correctness checks.
- The budget runs out.
- A round keeps nothing, the next round pivots category per hillclimb step 6, and it also keeps nothing.

## Report

Add to the PR and the reply: the brief, [council's score table](../council/SKILL.md#review) with each cell as `<new> vs <best>` followed by any reference scores, and for visual work, before and after screenshots attached per [open-pr](../open-pr/SKILL.md).
