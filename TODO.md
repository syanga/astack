# TODO

## Enforce Codex background-job routing with a hook

Codex follows `instructions/CODEX.md` to route background jobs, watchers, and
long-lived processes through `codex-background-jobs`, but nothing enforces it.
Codex 0.155.1 runs `PreToolUse` hooks, yet no exec call parameter marks a call as
background, so a hook cannot tell a background job from a foreground command.
When Codex adds that marker, add a hook like `claude-background-jobs` that denies
background calls that skip the helper.

- Each new Codex hook needs trust review in the TUI before it runs.
- The installer's `$entries` syntax can own one entry in a `hooks.json` list.
  It cannot manage a hook in a `config.toml` `[hooks]` table, because astack
  does not manage TOML tables.
