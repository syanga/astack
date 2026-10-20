---
name: t3-hosts
description: Show CPU usage and available RAM across machines linked through T3 Connect.
disable-model-invocation: true
---

# Check T3 hosts

Run the bundled command to show a snapshot of the local machine and the machines
linked to the user's T3 Connect account:

```sh
python3 <skill-directory>/scripts/t3_hosts.py
```

Use the actual directory containing this skill. The command discovers the running
server from `T3CODE_HOME`, or `~/.t3`, and discovers remote machines through T3
Connect. It requires an existing running local T3 server and saved Connect sign-in.
It does not start a server or ask the user to maintain host addresses.

Before the first default run on macOS, explain that the command may open a Keychain
dialog for T3's Safe Storage item. The user handles that system dialog. Credentials
stay out of command arguments and output. The command keeps them only in memory.
It reads T3's saved sign-in and creates authenticated sessions with
`orchestration:read`. The local session expires after one minute and is revoked
after the query.

Report unreachable hosts and discovery failures. An incomplete discovery does not
establish that the displayed hosts are the whole account. If the command exits
successfully with no output, T3 is unavailable. Stop without starting T3.

## Terminal use

After installing astack for Codex, run:

```sh
python3 ~/.agents/skills/t3-hosts/scripts/t3_hosts.py
```

For a short command, add this alias to the terminal's shell configuration:

```sh
alias t3-hosts='python3 "$HOME/.agents/skills/t3-hosts/scripts/t3_hosts.py"'
```

Claude-only installations use `~/.claude/skills/t3-hosts` instead. Custom agent
homes use their installed skill directory.

Use `--json` for structured output. `--local` queries only the local T3 server and
skips Connect and Keychain access. `--home PATH` selects another T3 data directory.
`--timeout SECONDS` bounds each network request, with a default of five seconds.

Exit status is 0 for a complete snapshot or an unavailable local T3 server, 1 for
partial results, and 2 for invalid setup. JSON has `hosts`, `errors`, and `complete`.
An unreachable host has no resource measurements, rather than zero CPU or RAM.

## Compatibility

The command requires Python 3.10 or newer. Connect authentication uses Node.js's
crypto library, or the bundled Electron runtime on a Mac with T3 Code installed.
Encrypted desktop sign-in currently works on macOS. Other systems can use a
plaintext sign-in saved by T3, or `--local`.

Host CPU and RAM use T3's `server.getHostResources` RPC. The command supports the
WebSocket handshake for protocols 1 and 2. Disk capacity is not part of that RPC.
The [protocol notes](references/protocol.md) link the upstream contracts.
