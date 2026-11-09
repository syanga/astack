---
name: hillclimb-council
description: Hillclimb a quality that models can judge but no command can measure, such as UI design or writing, scored by a blind council.
disable-model-invocation: true
---

# Hillclimb council

Follow [hillclimb](../hillclimb/SKILL.md) with a council vote as its metric. The sections below replace the hillclimb steps they name. Hillclimb owns the rest.

## Set up the vote

This replaces the workload, repro, and metric in hillclimb step 1, and the measurement harness in step 2.

1. Write the judge brief from the request: what the judges assess, for example the design quality of these screens. List the fixed constraints, such as brand colors or placeholder test data, only from the user's limits, the project instructions, and the test setup. Tell the judges not to count them as flaws. Describe what a 4, 6, 8, and 10 look like, so fresh judges use the same scale each round.
2. Choose the input, and add any reference examples the user supplies, such as competitor screenshots. For text, the judges read the files. For visual work:
   - Write a capture script that takes the same screenshots every round, for example through [t3-preview](../t3-preview/SKILL.md). Freeze animations and the page clock, and seed test data from a script, so an untouched screen captures identically every round.
   - Add a scripted layout check, such as no horizontal overflow, to the correctness checks. Run it over every page at each supported width, including pages the screenshots omit.
3. Ask the user, with the question tool, to approve the brief and, for visual work, the screen list. When the user delegated this pick, record both with the task's other decisions instead. Then freeze the brief and references.
4. Run a baseline vote, per the vote steps, on the starting version and the references alone. Its scores are the baseline, and its flaws fill the first flaw queue. Scale each judge's baseline time to a round's ballot, which holds up to two more versions. If a judge times out or its scaled time passes 15 minutes, skip its retries. Shrink each subdirectory's input, keeping every approved screen and file, and vote again. Leave these reruns out of the stop rule's count of inconclusive votes. If the second rerun still times out or passes 15 minutes, ask the user. Then freeze the input format, the judge commands, and any capture script.

The target defaults to 9 out of 10 and the budget to 10 rounds.

## Vote each round

1. Fix broken inputs, such as an empty file or a blank screenshot, before the vote. For visual work, capture the new, current best, and starting versions in one run against the same backend and seeded data. Fix visible regressions, such as overflowing content, and confirm that screens the change does not touch match the current best's.
2. Make a new ballot directory with `mktemp -d` outside any Git checkout. Put each version in its own randomly named subdirectory: the new version, the current best, each reference, and, after the first keep, the starting version. Keep the label map, prompts, and judge outputs outside the ballot directory.
3. Even in T3 Code, start judges through their CLIs instead of `delegate_task`. Start a fresh judge for each of [council's members](../council/SKILL.md#members) in the ballot directory, with that member's model and effort and the read-only invocation in [provider execution](../arena/PROVIDERS.md). Add `--skip-git-repo-check` for Codex. Allow each judge 30 minutes, and set any host command timeout longer than the judge's limit. These limits replace the nine-minute default in provider execution. If a judge fails, times out, or lacks a score for any subdirectory, retry that member up to twice, each time with a fresh judge in the same ballot directory and a 60-minute limit. Keep the other members' scores.
4. Give each judge the brief with no repository paths. Tell it to read only the ballot directory and to judge only by the brief, ignoring any other style instructions it holds. Ask, for each subdirectory, for a score out of 10 to one decimal place and a ranked list of flaws, each naming where it appears, such as a screen or file, and the change that would fix it. The judges must not learn the target, earlier scores, the round number, or which subdirectory holds which version.
5. Keep the new version only when the members' mean score rises at least 0.3 above the current best's, and no member scores it below the current best. This replaces the noise threshold in hillclimb steps 5 and 8. When a vote meets this rule with a mean gain below 0.5, run vote steps 2 to 4 again as a confirm vote. The rule holds only when both votes meet it.
6. After the keep decision, rebuild the flaw queue from the current best's flaws in the round's last vote, and mark the flaws already attempted. Base the next round on one queued flaw, and prefer flaws that several judges name. Give the implementer the flaw and the brief's fixed constraints. For visual work, also give it the capture script, and have it capture its change and fix visible regressions before the vote.
7. After a round that keeps nothing, pivot per hillclimb step 6 and still target one flaw: pick another queued flaw, or make a larger change for the same one, such as restructuring the screen that holds it.

If a member still has no score after its retries, record the vote as inconclusive and continue. In a round, keep nothing, leave the flaw unmarked, and skip the step 7 pivot. The round still counts against the budget. In the baseline vote, start round 1 from the other members' flaws.

## Stop

This replaces hillclimb step 7. After any requested minimum rounds, stop at the first of:

- Every member scores the current best at or above the target and above every reference. Check this before the keep rule, and stop with the current best.
- The budget runs out.
- Two votes in a row end without a score from the same member, even before any requested minimum rounds.

At any stop, the last conclusive vote replaces the frozen measurement in hillclimb step 8's recheck, so rerun only the correctness checks there, then finish that step. If step 8 found a moved base, the recheck is instead one run of vote steps 1 to 4 on the rebased current best, the new base, the original starting version, and the references. If the recheck has no conclusive vote, report it as unavailable, and after a rebase ask the user before opening the PR. After a budget stop, ask the user whether to extend the budget. If the user extends it, continue the loop on the same branch, with the new base as the starting version after a moved base, and repeat step 8 at the next stop.

## Report

Add these to the council review section that [council](../council/SKILL.md#review) puts in the PR:

- The brief.
- The score table. Write each cell as `<new> vs <best>`, followed after the first keep by the starting version's score and then any reference scores, such as `8.3 vs 7.9, start 6.4, ref 8.8`. Write a round's vote and its confirm vote in one cell, such as `8.3 vs 7.9 / 8.0 vs 7.9`. Add a row that names the flaw each round targeted.
- Each judge failure: the member, the vote, the attempts, and the error.
- For visual work, before and after screenshots attached per [open-pr](../open-pr/SKILL.md).

In hillclimb's reply, give scores instead of deltas: the original starting version, any new base, and the accepted version from the recheck.
