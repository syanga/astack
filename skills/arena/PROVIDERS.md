# Run a provider task

Use fresh native agents or the installed Claude and Codex CLIs. Follow the
session's provider and model preferences. Keep each provider's configured model
unless the user selects another. Record the provider and reported model identity.
Leave unknown model identities unknown.

For outside coverage, choose Claude when hosted in Codex and Codex when hosted
in Claude. In another host, choose an available provider. If host identity is
unclear or inherited host markers conflict, report outside coverage as unavailable
until the host is resolved. A completed result can still inform the work, but
does not satisfy an outside-provider gate. A native agent is an independent
context, not an outside provider.

Check the installed CLI's help before invocation. A missing CLI, failed
authentication, or unsupported option means unavailable coverage. Report it
and use only the caller's permitted fallback. Preserve the difference between
unavailable coverage and a completed task with no findings.

## Supply the task and capture the result

Use a fresh output directory for each candidate or review. Save the complete
prompt in a private file, including the source snapshot, scope, deliverable,
and acceptance criteria. Pass it through stdin. Give readers access only to the
source and context they need. Explicitly supply required skill instructions
because provider sessions may not inherit them.

For Codex, run from the assigned repository or isolated worktree:

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

These invocations leave source unchanged. A design candidate returns its sketch
as text. A code candidate returns a patch or complete files for the parent to
materialize and test in its assigned worktree. Embed the assigned diff text in
the prompt for Claude; a diff pathname alone is insufficient. Put referenced
supporting files inside the provider's readable checkout or snapshot. Native
code-writing runners may edit their assigned worktree when the task authorizes
implementation.

Preserve stdout, stderr, and the final response. Claude returns a JSON envelope.
Read its result text and error status. Inspect Codex's final response and events.
Process exit alone is not completion. Reject empty, refused, malformed, or
incomplete output according to the caller's acceptance criteria. A candidate's
claimed tests need observed evidence before they count as verification.

## Bound and reconcile execution

Use a bounded execution timeout, nine minutes by default or the caller's shorter
remaining budget. Terminate timed-out processes and reconcile child processes
before reusing their workspace. Preserve partial output as incomplete evidence.
Respect available concurrency and queue remaining work. Report any process that
could not be stopped and keep its workspace owned until reconciled.

The caller owns evaluation, fallback, and synthesis. If the source changes while
a task runs, retain its original snapshot identity. Repeat affected work before
claiming the result applies to a newer revision.
