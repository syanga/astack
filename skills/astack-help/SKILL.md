---
name: astack-help
description: Recommend astack skills for a task. Use when the user asks which astack skills exist or fit. Most astack skills are user-invoked and absent from this catalog.
---

# Find the right astack skills

Read astack's installation manifest at
`${XDG_STATE_HOME:-$HOME/.local/state}/astack/manifest.json`.
For `--home` installations, use that home's `.local/state/astack/manifest.json`.
If the manifest is missing, ask for the installation location.

Check for an update before recommending:

```sh
python3 <skill-directory>/scripts/check_update.py <manifest>
```

If it reports `update available`, ask the user whether to update astack from
`main` now. If they agree, follow [astack-update](../astack-update/SKILL.md),
then reread the manifest. If it reports `check skipped`, show that line to the
user. In every case, then recommend skills as below.

Under `targets`, select the current agent's map of installed file paths.
Read the frontmatter names and descriptions of its existing `/SKILL.md`
entries, including skills absent from the automatic catalog. Read promising
skill bodies only to assess fit.

Recommend the smallest useful set for the task in the conversation, with a
brief reason for each. If none fits, say so.
Stop after recommending. The approved update is the only skill workflow this
skill runs.
