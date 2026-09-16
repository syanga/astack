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
- Adapted (second pass): `SKILL-MECHANICS.md` corrected for astack. A
  user-invoked skill is reachable by relative path because every harness
  installs skills as siblings, so shared reference may live there; the router
  sentence was narrowed to the skill tool; a Harnesses table records each
  harness's user-invoked mechanism and catalog cost; a "Done when" criterion
  closes the skill branch. `SKILL.md` gained a pointer to the sibling unslop
  skill for the final prose pass and, in Pruning, pstack's
  encode-lessons-in-structure as one bullet. The validation criterion is
  backed by the installer, which now checks relative links and writes or
  verifies the Codex policy file from the frontmatter flag.
- Skipped: pstack's "operational, not poetic" guardrail. The leading-words
  section plus the unslop pointer already cover it; a third statement would be
  duplication.
- Deferred: a pointer to an eval procedure for testing wording changes, until
  an eval skill exists to point at.
- Local validation: `python3 scripts/upstream.py diff writing-for-agents-mattpocock --local`
  shows only the unslop edits and the added LICENSE; scan for curly quotes,
  arrows, and long dashes is clean; `python3 -m unittest discover -s tests`
  passes (89 tests); `./install.sh --target all --home <tmp> --dry-run` writes
  the skill to all four harness paths.
- Local implementation: `skills/writing-for-agents/` in the working tree.
