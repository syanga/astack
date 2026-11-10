---
name: hillclimb-council
description: Hillclimb a quality that models can judge but no command can measure, such as UI design or writing, scored by a blind council.
disable-model-invocation: true
---

# Hillclimb council

Follow [hillclimb](../hillclimb/SKILL.md) with a council vote as its metric. The sections below replace the hillclimb steps they name. Hillclimb owns the rest.

## Set up the vote

This replaces the workload, repro, and metric in hillclimb step 1, and the measurement harness in step 2.

1. Write the judge brief from the request: what the judges assess, for example the design quality of these screens. List the fixed constraints, such as brand colors or placeholder test data, only from the user's limits, the project instructions, and the test setup. Tell the judges not to count them as flaws. Define a major flaw as one that hurts the audience's understanding, next action, or trust, and every other flaw as minor. Describe what a 4, 6, 8, 9, and 10 look like by their major and minor flaws, so fresh judges use the same scale each round.
2. Choose the input, and add any reference examples the user supplies, such as competitor screenshots. For text, the judges read the files. For visual work:
   - Write a capture script that takes the same screenshots every round, for example through [t3-preview](../t3-preview/SKILL.md). Freeze animations and the page clock, and seed test data from a script, so an untouched screen captures identically every round. Save the text each screenshot shows outside the ballot. Before the baseline vote, confirm that each screen's saved text holds the text its screenshot shows and no more, such as no text from a closed disclosure.
   - Add a scripted layout check, such as no horizontal overflow, to the correctness checks. Run it over every page at each supported width, including pages the screenshots omit.
3. Write a quote check, a script that lists, per member and version, the flaws whose quote is absent from that version's text. A quote of n words is present when some run of consecutive words in that text differs from it, ignoring case and punctuation, by at most n/5 words changed, added, or removed, rounded down. Count each character of a script written without spaces, such as Chinese, as a word. A quote is also absent when it matches another ballot version's text, ignoring case and punctuation, with no word changed, added, or removed, but matches this version's text only within that allowance. A flaw with no quote counts as absent. The check reads the files for text work and the saved screen text for visual work, and skips versions with neither, such as reference screenshots.
4. Ask the user, with the question tool, to approve the brief and, for visual work, the screen list. When the user delegated this pick, record both with the task's other decisions instead. Then freeze the brief and references.
5. Run a baseline vote, per the vote steps, on the starting version and the references alone. Its scores are the baseline, and its flaws, less those the quote check lists per vote step 6, fill the first flaw queue. Look up each flaw the quote check lists in the input. If any quote does appear, fix the saved text or the check, and rerun only the check. If a member gives no quote for any of its flaws, change how vote step 4 asks for quotes, leaving the approved brief unchanged. From each judge's time per subdirectory, estimate its time on a round's ballot, which holds up to two more subdirectories than the baseline's. If a judge times out or its estimate passes half the 30-minute limit in vote step 3, skip its retries and change the input format to a smaller one, such as a lower image size or a more compact file type. If a judge fails after its retries, fix its command, keeping its model and effort. After a change to how vote step 4 asks for quotes, to the input format, or to a judge command, rerun the baseline, at most twice. Only the last baseline vote counts toward the stop rules. If the content itself would have to change, or the second rerun still fails, ask the user. Then freeze the input format, the judge commands, any capture script, and the quote check.

The target defaults to 9 out of 10 and the budget to 10 rounds. Judges re-anchor their scale each vote, so measure the target on the baseline scale. In a vote, a member's baseline-scale score for the current best is its baseline score for the starting version plus its score for the current best minus its score for the starting version. After a keep, the current best is the version just kept. A member without a baseline score uses its starting-version score from the first vote in which it scores.

## Vote each round

1. Fix broken inputs, such as an empty file or a blank screenshot, before the vote. For visual work, capture the new, current best, and starting versions in one run against the same backend and seeded data. Fix visible regressions, such as overflowing content, and confirm that screens the change does not touch match the current best's.
2. Make a new ballot directory with `mktemp -d` outside any Git checkout. Put each version in its own randomly named subdirectory: the new version, the current best, each reference, and, after the first keep, the starting version. When versions have byte-identical files or captures, put only one of them in the ballot and give the others its scores and flaws. Keep the label map, prompts, and judge outputs outside the ballot directory.
3. Even in T3 Code, start judges through their CLIs instead of `delegate_task`. Start a fresh judge for each of [council's members](../council/SKILL.md#members) in the ballot directory, with that member's model and effort and the read-only invocation in [provider execution](../arena/PROVIDERS.md). Add `--skip-git-repo-check` for Codex. Allow each judge 30 minutes, and set any host command timeout longer than the judge's limit. These limits replace the nine-minute default in provider execution. If a judge fails, times out, or lacks a score for any subdirectory, retry that member up to twice, each time with a fresh judge in the same ballot directory and a 60-minute limit. Keep the other members' scores.
4. Give each judge the brief with no repository paths. Tell it to read only the ballot directory and to judge only by the brief, ignoring any other style instructions it holds. Ask, for each subdirectory, for a score out of 10 to one decimal place and a list of up to 8 flaws, each marked major or minor, with major flaws first and each group ranked by damage. Each flaw names where it appears, such as a screen or file, quotes one unbroken span of the text at that spot or nearest it, and gives the change that would fix it. The judges must not learn the target, earlier scores, the round number, or which subdirectory holds which version.
5. A vote favors the new version when no member scores it below the current best and at least one scores it above, by any margin. Keep it when every member's gain is at least 0.5. When the vote favors it but some member's gain is below 0.5, run vote steps 2 to 4 again as a confirm vote, and keep it only if that vote favors it too. Otherwise revert it. This rule replaces the noise threshold in hillclimb steps 5 and 8. In hillclimb step 6, keep a simplification unless some member scores it more than 0.3 below the current best.
6. After the keep decision, rebuild the flaw queue from the current best's flaws in the round's last vote, drop the flaws the quote check lists, keeping them all if none would remain, and mark the flaws already attempted. Base the next round on one queued flaw. Prefer unattempted flaws, then major flaws, then flaws that several judges name. Give the implementer the flaw and the brief's fixed constraints. For visual work, also give it the capture script, and have it capture its change and fix visible regressions before the vote.
7. After a round that keeps nothing, pivot per hillclimb step 6 and still target one flaw: pick another queued flaw, or make a larger change for the same one, such as restructuring the screen that holds it.

If a member still has no score after its retries, record the vote as inconclusive and continue. In a round, keep nothing, leave the flaw unmarked, and skip the step 7 pivot. The round still counts against the budget. If the user lets an inconclusive baseline vote stand, start round 1 from the other members' flaws.

## Stop

This replaces hillclimb step 7. After any requested minimum rounds, stop at the first of:

- Every member's baseline-scale score for the current best reaches the target, and every member scores the current best above every reference. Check this with the baseline vote's scores, and after each round's keep decision with that round's last vote. Each time it holds, rerun that vote outside the budget: the baseline vote, whose new scores replace the baseline for each member that scores in it, or a vote on the current best, the starting version, and the references. Stop only if it holds there too. When it and the budget stop fall on the same round, report the target stop.
- The budget runs out.
- Two votes in a row, counting confirm votes, end without a score from the same member, even before any requested minimum rounds.

At any stop, the last conclusive vote replaces the frozen measurement in hillclimb step 8's recheck, so rerun only the correctness checks there, then finish that step. If no vote is conclusive, report the recheck as unavailable. After a budget stop, ask the user whether to extend the budget. If the user extends it, continue the loop on the same branch and repeat step 8 at the next stop.

If step 8 finds a moved base, then instead of reusing the last conclusive vote, the recheck runs vote steps 1 to 4 with the rebased current best as the new version and the new base as the current best, plus the starting version and the references. Vote step 5's rule, with its confirm vote, serves only as hillclimb step 8's noise test, and the recheck keeps or reverts nothing. If the recheck is inconclusive, ask the user before opening the PR. Later rounds use the new base as the starting version. Each member's baseline score for the new base is its baseline-scale score for the new base in the recheck's first vote.

## Report

Add these to the council review section that [council](../council/SKILL.md#review) puts in the PR:

- The brief.
- The score table. Write each cell as `<new> vs <best>`, followed after the first keep by the starting version's score and then any reference scores, such as `8.3 vs 7.9, start 6.4, ref 8.8`. Write a round's vote and its confirm vote in one cell, such as `8.3 vs 7.9 / 8.0 vs 7.9`. For a version left out as identical, write `same as <version>` in place of its score, such as `8.3 vs 7.9, start same as best`. Add a row per member with its baseline-scale score for the current best after each round, and a row that names the flaw each round targeted.
- Each judge failure: the member, the vote, the attempts, and the error.
- For visual work, before and after screenshots attached per [open-pr](../open-pr/SKILL.md).

In hillclimb's reply, give scores from the recheck instead of deltas: the accepted version, the starting version, any new base, and each member's baseline-scale score for the accepted version. Write a version left out as identical as `same as <version>`, as in the score table. Still list any dropped change.
