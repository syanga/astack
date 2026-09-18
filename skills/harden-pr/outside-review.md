# Run an outside review

Choose a provider different from the current host: Claude when running in Codex, Codex when running in Claude. In another host, use an available provider. Respect the user's provider and model preferences. If host identity is unclear or inherited host markers conflict, report unavailable outside coverage instead of guessing. A native subagent is a separate pass, not an outside provider.

Check the installed CLI's help before invocation. Missing CLI, authentication failure, or an unsupported option means unavailable coverage; report the cause. Keep the configured model unless the user specified one.

Use a fresh output directory for each pass. Run from the reviewed repository and preserve stdout, stderr, and the final response.

## Prompted review

Save the complete prompt, diff, and relevant source context in a private file. Pass the prompt through stdin.

For Codex:

```bash
codex exec --sandbox read-only --ephemeral --json \
  --output-last-message '<response-file>' - \
  < '<prompt-file>' > '<events-file>' 2> '<stderr-file>'
```

For Claude:

```bash
claude --print --safe-mode \
  --tools 'Read,Glob,Grep' --strict-mcp-config \
  --no-session-persistence --output-format json \
  < '<prompt-file>' > '<response-file>' 2> '<stderr-file>'
```

Claude's result is a JSON envelope; read its result text and error status. Supply the diff explicitly because this invocation has no shell tool.

## Codex built-in structured review

For a structured review with Codex, use its built-in review when its base comparison covers the requested changes:

```bash
codex review --base '<fixed-point>' -c 'sandbox_mode="read-only"'
```

`--base` and a positional prompt are mutually exclusive. Keep `--base` when resolving an argument error; dropping it changes the diff scope. If the built-in comparison does not cover the requested changes, use the prompted review above with the captured diff and structured prompt.

## Results and limits

Successful process exit alone does not establish a completed review. Validate the response as described in [adversarial.md](sections/adversarial.md).

Use a bounded execution timeout, nine minutes by default, and terminate the process on timeout. Preserve partial output as incomplete evidence. If the reviewed source changes while a pass runs, repeat it against the updated source before claiming convergence.
