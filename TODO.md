# TODO

## Enforce Codex background-job routing with a hook

Codex follows `instructions/CODEX.md` to route background jobs, watchers, and
long-lived processes through `codex-background-jobs`, but nothing enforces it.
Codex 0.155.1 runs `PreToolUse` hooks, but no exec call parameter marks a call as
background. A hook cannot tell a background job from a foreground command.
When Codex adds that marker, add a hook like `claude-background-jobs` that denies
background calls that skip `codex-background-jobs`.

Add the hook as a `PreToolUse` entry in
`codex-plugin/plugins/astack/hooks/hooks.json` and raise the plugin's version. The
installer trusts it in `config.toml`.
