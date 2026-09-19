# astack

Keep your coding agents' instructions, skills, and settings in one repository
and install them across macOS and Linux machines.

astack provides:

- Shared instructions with additions for each agent.
- Reusable skills, including their scripts and supporting files.
- Selected settings merged into agent configuration without replacing unrelated keys.
- Repeatable installation and removal, with conflict checks for local edits and
  restoration of managed settings to their original values.
- An optional [Gitleaks pre-push hook](skills/open-pr/scripts/git_hooks.py)
  to scan commits for secrets. Install and remove it separately from agent configuration.

## Install

You need Git, a POSIX shell, and Python 3.10 or newer. Install your coding agents
separately.

```sh
git clone https://github.com/syanga/astack.git
cd astack
./install.sh --help
./install.sh --dry-run
./install.sh
```

Use `--help` to see supported targets and defaults. To choose agents, pass
`--target <name>` for each one, or `--target all` for every supported agent.
Use the same targets for the preview and installation.

## Customize

Edit the source files, then rerun `./install.sh` with your chosen targets.
Installed instructions and skills are copies, so moving the checkout does not
break them. Restart your agent or reload its instructions and skills after installation.

The repository defines what gets installed:

| Source | Purpose |
| --- | --- |
| [instructions/](instructions/) | Shared preferences and additions for each agent |
| [skills/](skills/) | Available skills and their usage instructions |
| [settings/](settings/) | Settings to manage |
| [harnesses.json](harnesses.json) | Supported agents, destinations, and configuration sources |

For contribution rules, see [AGENTS.md](AGENTS.md).

## Implementation skills

Ask `astack-help` which skills fit your current task. It reads the installed
astack skills and recommends what to use next.

Start with [implement](skills/implement/SKILL.md) when the desired behavior is
clear. Use [prototype](skills/prototype/SKILL.md) first to resolve UI or logic
design questions, then implement the chosen direction.

[architect](skills/architect/SKILL.md) explores the code's structure, and
[to-spec](skills/to-spec/SKILL.md) captures decisions for a durable handoff.
Implementation uses [tdd](skills/tdd/SKILL.md) for test-first changes and
[diagnosing-bugs](skills/diagnosing-bugs/SKILL.md) for uncertain failures.

Use [wizard](skills/wizard/SKILL.md) when setup or migrations need human steps.
It generates an interactive Bash script that guides dashboard actions and
captures configuration values for local development and CI.

These entry points compose with the existing principles, review, and delivery
skills. The [adaptation notes](upstream/implementation-adaptations.md) explain
the upstream sources, overlaps, and workflow choices.

## Debugging and investigation

Use [investigation](skills/investigation/SKILL.md) for a technical question that
needs a cited explanation or recommendation. It selects code, history, or
diagnostic methods and keeps the inquiry read-only.

Use [bug-fix](skills/bug-fix/SKILL.md) to carry a defect through reproduction,
diagnosis, implementation, and verification. Use
[perf-issue](skills/perf-issue/SKILL.md) to improve measured performance with
comparable baseline and post-fix results.

[diagnosing-bugs](skills/diagnosing-bugs/SKILL.md) provides the shared diagnosis
loop. Its [trace](skills/diagnosing-bugs/TRACE-FORENSICS.md) and
[runtime](skills/diagnosing-bugs/RUNTIME-FORENSICS.md) references cover existing
captures and live instrumentation. A diagnosis request ends with findings;
implementation follows the user's scope. See the
[adaptation notes](upstream/debugging-adaptations.md) for source comparisons.

## Multi-phase work

Use [wayfinder](skills/wayfinder/SKILL.md) when a large effort still has decisions
to resolve across sessions. It keeps a shared map of questions, answers, and
unknowns. Its decision tickets do not replace an implementation plan.

Once the route is clear, [multi-phase-plan](skills/multi-phase-plan/SKILL.md)
writes the PR sequence, dependencies, verification steps, and delivery gates.
Use [orchestrate](skills/orchestrate/SKILL.md) to execute a program that needs
multiple owners and durable coordination. Bounded tasks still start with
`implement`; these planning skills are optional entry points, not a required chain.

Sequential and orchestrated execution use the same [durable state convention](skills/orchestrate/STATE.md).
The store records unit states, verification evidence, and a session handoff with
the exact next action. Switching execution skills keeps the same store. The
[orchestration CLI](skills/orchestrate/CLI.md) maintains these files using pstack's
imported Bun/TypeScript runtime, with retained Graphite support and an additional
GitHub frontier adapter. Bun is required when executing multi-phase work; the
first CLI call installs the locked dependencies. Agent dispatch stays with the
active harness.

[Handoff](skills/handoff/SKILL.md) prepares another session to continue the task.
It reuses an execution store or decision map when present. Standalone tasks get
a temporary handoff file. Its pause and pickup procedures preserve partial work
and check ownership before another session takes over.

[Show-me-your-work](skills/show-me-your-work/SKILL.md) owns the decision trail
used by these execution workflows, including reasons, evidence, outcomes, and
the final audit. Its original Bash helper and TSV template are included.

The [planning adaptation notes](upstream/planning-adaptations.md) explain the
upstream workflows and their integration with astack.

## Update or remove

After pulling source changes, rerun the installer with the same targets.
It updates managed files and removes skill files deleted from the source.
Conflicting local edits stop installation. To back up and replace conflicts,
preview with `--force --dry-run` before running with `--force`.

To remove an installation, use the same targets with `./uninstall.sh`.
Preview removal with `--dry-run`. Uninstall preserves unrelated files and
restores managed settings. It does not restore file backups automatically.

## Verify changes

Run the test suite, which uses temporary directories:

```sh
python3 -m unittest discover -s tests -v
```

To preview installation without using your agent configuration, run
`./install.sh --target all --home /tmp/astack-demo --dry-run`.

## Credits

astack draws inspiration and adapts content from
[pstack](https://github.com/cursor/plugins/tree/main/pstack),
[Matt Pocock's skills](https://github.com/mattpocock/skills), and
[gstack](https://github.com/garrytan/gstack).
Adapted skills retain their upstream licenses. The
[upstream manifest](upstream/manifest.json) records sources and revisions.
