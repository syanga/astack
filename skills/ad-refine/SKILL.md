---
name: ad-refine
description: Refine one static ad over at most five rounds, with the council as the craft check and a buyer panel command as the metric.
argument-hint: "<ad image> <ad text> <panel command> <margin>"
disable-model-invocation: true
---

# Ad refine

Follow [hillclimb-council](../hillclimb-council/SKILL.md) on one ad, with [`rounds.py`](scripts/rounds.py) keeping score. The council's flaws are the craft check, and the caller's buyer panel is the metric. Each section below replaces the hillclimb and hillclimb-council steps it names. Those skills own the rest.

The run writes only inside its run folder. It makes no commits, branches, or PRs.

## Inputs

The caller supplies these. Ask for any that is missing.

- The ad: one image and the text that runs with it, such as the primary text and headline.
- The craft brief: a rubric whose gate checks define a major flaw, and the brand guide and claims file it names.
- How to render a new version, such as a static-ads request file and image backend.
- The panel command template. `rounds.py` replaces `{panel}` with `A` or `B`, `{image}` with the version's image, and `{copy}` with its `copy.txt`, each shell-quoted. The command prints, as its last line, a JSON object whose `purchase_intent.mean` is the panel's mean purchase intent. Panel A is the metric. Panel B uses another model, other personas, and other reference statements, and confirms a keep. Each panel must draw the same personas on every run, so the new version and the current best face the same buyers.
- The margin: the largest change in panel A's mean that repeated runs on one unchanged ad produce.

## Set up the run

This replaces hillclimb steps 1 to 3 and hillclimb-council's "Set up the vote".

1. Make a run folder outside any Git checkout, such as `~/Downloads/<brand>-refine-<date>/`. Give each version its own subfolder: `v0` for the starting ad, then `v1`, `v2`, and so on. A version folder holds the image as `ad.png`, `ad.jpg`, `ad.jpeg`, or `ad.webp`, the ad's text as `copy.txt`, any render request, and `text.txt`. Write `text.txt` as `copy.txt` followed by every word the image shows, as rendered, misspellings included. Keep judge prompts and outputs in the run folder too. The run folder is the decision trail.
2. Write the judge brief from the craft brief. A failed gate check is a major flaw, and every other flaw is minor. Ask for the rubric's scores in place of hillclimb-council's score out of 10, and for each flaw's check id. The caller's rubric is already approved, so skip setup step 4's approval and freeze the brief.
3. Write the quote check per setup step 3. It reads each version's `text.txt`. When a version's `text.txt` has no words, the check lists none of that version's flaws that have no quote.
4. Run the baseline vote per setup step 5 on `v0` alone. Write the craft file described below, then run:

   ```bash
   python3 <skill-dir>/scripts/rounds.py start <run> --version v0 --craft <craft file> \
     --panel-command '<template>' --margin <margin>
   ```

   `<skill-dir>` is the folder this SKILL.md is in. For a budget below 5 rounds, add `--rounds <n>`.

The craft file maps each council member to its flaws per version. List only flaws that pass the quote check. Write `null` for a member that gave no score after its retries.

```json
{
  "claude-opus": {"v0": [{"severity": "major", "check": "claims", "quote": "US$36", "fix": "Drop the price"}]},
  "gpt-sol": {"v0": []}
}
```

## Run each round

These steps replace hillclimb steps 4 and 5 and hillclimb-council's vote steps as named.

1. Build the bundle per vote step 7 from the current best's flaws: three fixes, or every queued flaw if fewer. If the current best has a major flaw, put every queued major flaw in the bundle instead, because only a version without major flaws reaches the panels. Each fix edits the render request or `copy.txt`. Write all of them into one new version yourself, with no parts, implementers, or worktrees. Render it with the current best image as a reference, so the render keeps what the fixes leave alone. Leave the reference out when a fix changes the type, colors, or layout of the whole ad. After a round reverts on a conclusive vote, pivot per vote step 8. After an inconclusive vote, skip the pivot as hillclimb-council says.
2. View the render before the vote. If the image is blank, has the wrong size, or garbles text that the request spelled out, render again. Write the new version's `text.txt`.
3. Vote per vote steps 2 to 4. The ballot holds the image and `copy.txt` of the new version and the current best, and nothing else. Panel B replaces the confirm ballot, so make none.
4. Write the craft file with both versions for each member, then run:

   ```bash
   python3 <skill-dir>/scripts/rounds.py round <run> --version v<n> --craft <craft file> \
     --targets '<the flaws this bundle fixes>'
   ```

   This replaces vote step 5. `rounds.py` runs the panels, applies the keep rule, prints the round table, and ends with `next:` and the current best, or `stop:` and the reasons. If a panel command fails, `rounds.py` exits 1, records nothing, and names the saved stderr. Fix the cause and run the same command again.

Skip vote step 6's trim. Each render redraws the whole image, so dropping one fix also changes what the other fixes left alone.

### Keep rule

`rounds.py round` reverts when the vote is inconclusive. Otherwise it compares the craft verdicts of the new version and the current best:

- Both fail. The round keeps the new version when no member lists more major flaws in it than in the current best, and at least one member lists fewer. This is vote step 5's favor rule, counted on major flaws. No panel runs.
- The new version fails and the current best passes. The round reverts.
- The new version passes and the current best fails. The round keeps the new version and runs panel A on it alone. Its mean is the panel baseline for later rounds.
- Both pass. `rounds.py` runs panel A on both. Panel A favors the new version when its mean rises by more than the margin. Only then does `rounds.py` run panel B on both. Panel B confirms when its mean rises at all. The round keeps the new version only when panel B confirms.

## Stop

This replaces hillclimb-council's Stop and hillclimb steps 7 to 9. `rounds.py` prints `stop:` with every reason that holds, and refuses more rounds, when any of these holds:

- The budget is spent: 5 rounds, or `--rounds`.
- The current best has no flaws in a conclusive vote. The run skips the empty-queue rerun to save a vote. A stop here leaves a current best that a full vote found clean.
- One member gave no score in two votes in a row.

No target stop applies, because the panel has no target score. Run no recheck, review-pr, or PR.

## Report

This replaces hillclimb's Reply and hillclimb-council's Report. Reply with the last round table, the current best's folder, its flaws from the last vote, and each judge failure: the member, the vote, the attempts, and the error or the other version it quoted. `python3 <skill-dir>/scripts/rounds.py table <run>` prints the table again.
