# Claude router

An optional local proxy that routes Claude conversations across subscription
accounts. The behavior is specified in
[docs/specs/claude-account-routing.md](../../docs/specs/claude-account-routing.md).
[CONTRACT.md](CONTRACT.md) records what the pinned SDK guarantees and which
contracts are still open.

The module currently holds only the feasibility probes in `probes/`. They embed
the pinned CLIProxyAPI SDK, serve two or more fake accounts from an in-process
fake Anthropic API, and refuse every other host.

## Run the probes

You need Go 1.26.0 or later.

1. Download the pinned dependencies once, with network access:

   ```sh
   go mod download
   ```

2. Run the probes offline:

   ```sh
   GOPROXY=off GOSUMDB=off go test -race ./probes/...
   ```

To save a sanitized JSON transcript per test, set
`CLAUDE_ROUTER_PROBE_EVIDENCE` to a directory. Transcripts hold account labels,
token hash prefixes, statuses, event names, and timing. They hold no tokens and
no prompts.

The configuration hot-reload probe is excluded by default because the pinned SDK
races on that path. Run it with `-tags sdkreload`.
