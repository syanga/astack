---
name: astack-help
description: Recommend astack skills for a task. Use when the user asks which astack skills exist or fit. Most astack skills are user-invoked and absent from this catalog.
---

# Find the right astack skills

Read astack's installation manifest at
`${XDG_STATE_HOME:-$HOME/.local/state}/astack/manifest.json`.
For `--home` installations, use that home's `.local/state/astack/manifest.json`.
If the manifest is missing, ask for the installation location.

Under `targets`, select the current agent's map of installed file paths.
Read the frontmatter names and descriptions of its existing `/SKILL.md`
entries, including skills absent from the automatic catalog. Read promising
skill bodies only to assess fit.

Recommend the smallest useful set for the task in the conversation, with a
brief reason for each. If none fits, say so.
Stop after recommending. Do not invoke the skills or perform their workflows.
