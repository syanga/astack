# writing-for-agents-mattpocock

Local skill: `skills/writing-for-agents`. Upstream: mattpocock/skills,
`skills/productivity/writing-for-agents`, base commit
`959a8e9f1edc3adbe2f7e3054bb6fbefa6696260`.

## 2026-09-15 — upstream 959a8e9f1edc3adbe2f7e3054bb6fbefa6696260

- Adopted: verbatim import of `SKILL.md`, `SKILL-MECHANICS.md`, and
  `agents/openai.yaml`, plus the upstream MIT `LICENSE`. The skill stays
  model-invocable; the frontmatter description is unchanged.
- Adapted: prose pass using `skills/unslop` on both Markdown files. Rules
  applied were 14 (colon as mid-sentence connector), 16 (bold label followed
  by a colon), 28 (dense sentences), 32 (figurative or personified phrasing),
  33 (verbless fragments and arrows), 9 (one "not just X, but Y"), and one
  passive in the router section. 39 edits, 30 paragraphs. Meaning preserved.
  Matt's leading words (sediment, lever, legwork, fog of war, tracer bullets,
  red) were kept on purpose; only the flourishes around them were removed.
  Rationale: the skill is read by our agents on every skill-authoring task,
  and its own rules should be written the way we want our skills written.
- Adapted: sharpened the Context pointers bullet "Cut identity the body already
  carries" with three sentences stating that a description says when to reach
  the skill, not what it contains, in the shape of one naming clause plus the
  triggers. Rationale: the six-word upstream bullet did not stop a 277-character
  summary-style description in practice. Validated with a two-arm run of four
  Opus 5 agents writing the same skill from the same draft; both agents with the
  sharpened bullet produced the prescribed shape at 148 and 158 characters, the
  two without it produced 151 and 209. Record: `docs/evaluations/description-guidance-2026-09-15.md`.
  The installer now also rejects descriptions over 200 characters
  (`scripts/manage.py`, `DESCRIPTION_LIMIT`), so the bound is structural.
- Skipped: none.
- Deferred (candidates, not yet decided): fold pstack's
  encode-lessons-in-structure into Pruning as one bullet; add an authoring
  validation step (frontmatter valid, referenced files exist, links resolve)
  as a completion criterion; add an "operational, not poetic" test from
  pstack's automate-me guardrails; correct `SKILL-MECHANICS.md` for astack,
  where sibling skills are reachable by relative path in every harness and
  Codex needs `allow_implicit_invocation: false` in `agents/openai.yaml`,
  while Gemini CLI and OpenCode list every skill regardless; add a pointer to
  `skills/unslop` for the final prose pass; a one-line pointer to a future
  eval procedure for testing skill changes.
- Local validation: `python3 scripts/upstream.py diff writing-for-agents-mattpocock --local`
  shows only the unslop edits and the added LICENSE; scan for curly quotes,
  arrows, and long dashes is clean; `python3 -m unittest discover -s tests`
  passes (89 tests); `./install.sh --target all --home <tmp> --dry-run` writes
  the skill to all four harness paths.
- Local implementation: `skills/writing-for-agents/` in the working tree.
