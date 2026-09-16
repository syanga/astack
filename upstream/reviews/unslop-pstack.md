# unslop-pstack

Local skill: `skills/unslop`. Upstream: cursor/plugins, `pstack/skills/unslop`,
base commit `c1c0a32802223f4be824112dd83d33ad29a8b26c`.

## 2026-09-15 — upstream c1c0a32802223f4be824112dd83d33ad29a8b26c

- Adopted: verbatim import of `SKILL.md` plus the upstream MIT `LICENSE`.
  The skill stays user-invoked (`disable-model-invocation: true`). Rule
  numbers are stable ids cited by other skills and must keep their gaps.
- Adapted: description shortened to the human-facing one-liner "Cut AI tells
  from any writing." The upstream tail "Must always apply" is invisible in
  Claude Code for a user-invoked skill but shown on every turn in Gemini CLI
  and OpenCode. Added `agents/openai.yaml` with
  `policy.allow_implicit_invocation: false` so Codex also treats the skill as
  user-invoked. Decision: keep unslop user-invoked. The always-on subset of its
  rules lives in the global `instructions/AGENTS.md`; skills and playbooks that
  produce prose artifacts reach the full checklist by relative path. Revisit if
  slop appears in artifacts written outside any routed workflow.
- Skipped: none.
- Deferred: none.
- Local validation: `python3 scripts/upstream.py diff unslop-pstack --local`
  shows only the added LICENSE; `python3 -m unittest discover -s tests`
  passes (89 tests); dry-run install writes the skill to all four harness
  paths. First real use: the prose pass recorded in
  `writing-for-agents-mattpocock.md`.
- Local implementation: `skills/unslop/` in the working tree.
