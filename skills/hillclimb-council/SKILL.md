---
name: hillclimb-council
description: Hillclimb a quality that models can judge but no command can measure, such as UI design or writing, scored by a blind council.
disable-model-invocation: true
---

# Hillclimb council

Follow [hillclimb](../hillclimb/SKILL.md) with a council vote as its metric. The sections below replace the hillclimb steps they name. Hillclimb owns the rest.

## Set up the vote

This replaces the workload, repro, and metric in hillclimb step 1, and the measurement harness in step 2.

1. Write the judge brief from the request: what the judges assess, for example the design quality of these screens.
2. Choose the input. For visual work, write a capture script that takes the same screenshots every round, for example through [t3-preview](../t3-preview/SKILL.md). For text, the judges read the files. Add any reference examples the user supplies, such as competitor screenshots.
3. Ask the user, with the question tool, to approve the brief and, for visual work, the screen list. When the user delegated this pick, record both with the task's other decisions instead. Then freeze the brief, capture script, and references.

The target defaults to 9 out of 10 and the budget to 10 rounds.

## Vote each round

1. Inspect the inputs and fix broken ones, such as a blank screenshot or a stale build. This replaces the sensitivity proof in hillclimb step 2.
2. Make a new temporary directory outside the repository. Put the new version, the current best, and each reference in its own randomly named subdirectory. Start fresh judges from [council's members](../council/SKILL.md#members), read-only. Outside T3 Code, start each judge's CLI in the temporary directory. Give each judge the brief and the temporary path, with no repository paths, and tell it to read only that path. Ask for a score out of 10 and the biggest flaw for each subdirectory. The judges must not learn the target, earlier scores, the round number, or which version is new.
3. Keep the new version only when every judge scores it above the current best. This replaces the noise threshold in hillclimb step 5. The current best's scores in round 1 are the baseline.
4. Ground the next hypothesis in the judges' flaws, and pass the flaws to the implementer. After a round that keeps nothing, pivot category per hillclimb step 6.

If a judge fails or returns no score, pause the run and ask the user. Vote only with every judge.

## Stop

This replaces hillclimb step 7 and the plateau rule in step 6. After any requested minimum rounds, stop at the first of:

- Every judge scores the accepted version at or above the target and above every reference. This vote also serves as the recheck in hillclimb step 8, so rerun only the correctness checks.
- The budget runs out.
- Two rounds in a row keep nothing.

## Report

Add these to the council review section that [council](../council/SKILL.md#review) puts in the PR: the brief, the score table with each cell as `<new> vs <best>` followed by any reference scores, and for visual work, before and after screenshots attached per [open-pr](../open-pr/SKILL.md).
