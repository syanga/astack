# Run an outside review

Choose a provider different from the current host: Claude when running in Codex, Codex when running in Claude. In another host, use an available provider. Respect the user's provider and model preferences. If host identity is unclear or inherited host markers conflict, report unavailable outside coverage instead of guessing. A native subagent is a separate pass, not an outside provider.

Check the installed CLI's help before invocation. Missing CLI, authentication failure, or an unsupported option means unavailable coverage; report the cause. Keep the configured model unless the user specified one.

Save the complete prompt, diff, and relevant source context in a private file. Use a fresh output directory for each pass. Run from the reviewed repository, pass the prompt through stdin, and preserve stdout, stderr, and the final response.

For Codex:

```bash
codex exec --sandbox read-only --ephemeral --json \
  --output-last-message '<response-file>' - \
  < '<prompt-file>' > '<events-file>' 2> '<stderr-file>'
```

For Claude:

```bash
claude --print --safe-mode --permission-mode plan \
  --tools 'Read,Glob,Grep' --strict-mcp-config \
  --no-session-persistence --output-format json \
  < '<prompt-file>' > '<response-file>' 2> '<stderr-file>'
```

Claude's result is a JSON envelope; read its result text and error status. Supply the diff explicitly because this invocation has no shell tool. For either provider, successful process exit alone does not establish a completed review. Validate the response as described in [adversarial.md](sections/adversarial.md).

Use a bounded execution timeout, nine minutes by default, and terminate the process on timeout. Preserve partial output as incomplete evidence. The same provider, timeout, and result checks apply to the built-in Codex structured review. If the reviewed source changes while a pass runs, repeat it against the updated source before claiming convergence.
