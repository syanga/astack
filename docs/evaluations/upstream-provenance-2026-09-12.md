# Agent simulation: upstream provenance

Four subagents exercised the workflow in isolated copies of the working tree.
Three used actual upstream skills to register historical origins, create small
authorized adaptations, and assess current changes. A fourth continued solely
from the handoff document. They did not receive the implementation conversation
or proposed bug fixes before their first passes.

The main repository still has no imported skills. All sample imports, candidate
changes, and skill executions stayed in temporary evaluation checkouts.

## Scenarios

| Skill | Original upstream commit | Current commit assessed | Result |
| --- | --- | --- | --- |
| gstack `review` | `702a1a9b698080aca72503f5feed9ed8cd552348` | `71f6048e8ada25180e61438abc1d98cb151fe9a7` | Independent code review plus historical skill adaptation and update proposals |
| pstack `unslop` | `73f8be4873ea4ba2b7378243a036d3360c69e04d` | `9d3fc719acd04947fcebef97e53e4feac0afe355` | Adapted prose editing to protect code/commands; assessed updates without adopting them |
| Matt Pocock `handoff` | `386d4ff719a7c420ad1454232d0436b01f1b8c17` | `3cca18b368ae95cdbdebbff572ccafa662551015` | Created a temporary handoff; a fresh agent recovered and verified the pending work |

Source paths: [gstack review](https://github.com/garrytan/gstack/tree/702a1a9b698080aca72503f5feed9ed8cd552348/review),
[pstack unslop](https://github.com/cursor/plugins/tree/73f8be4873ea4ba2b7378243a036d3360c69e04d/pstack/skills/unslop),
and [Matt Pocock handoff](https://github.com/mattpocock/skills/tree/386d4ff719a7c420ad1454232d0436b01f1b8c17/skills/productivity/handoff).

Each agent read root `AGENTS.md`, `CLAUDE.md`, and `upstream/README.md`, then ran
`list`, historical `track`, `check`, upstream `diff`, pinned `diff --ref`, and
offline `diff --local`. Hash comparisons showed that checks preserved provenance
records, original snapshots, and local adaptations. Candidate updates remained
pending; the agents did not treat recommendations as permission to adopt them.

The review agent executed the installed gstack `/review` workflow with isolated
state and its relevant checklists. It performed the internal review passes itself,
without nested reviewers. The unslop and handoff agents read and executed their
respective current upstream skill instructions for the behavioral exercises.

## Defects found and fixed

1. **Concurrent registrations lost an import.** Two `track` processes using
   different sources could both succeed while the last manifest write dropped the
   other entry. A repository-local lock now covers registration, manifest reload,
   and shared fetches. The independent retest observed one success and one explicit
   retry response; retrying preserved both imports.
2. **Upstream Git rules could omit original files from a commit.** A copied
   `.gitignore` hid already-tracked upstream text files when the snapshot was
   staged in astack. A fresh clone then failed baseline integrity checks. Baselines
   now use ZIP files with original paths, bytes, and executable modes; upstream
   ignore rules and attributes stay inside the archive. The independent retest
   staged, committed, and cloned snapshots with upstream ignore/CRLF rules and
   verified all archive bytes and subsequent checks.
3. **Installer tests failed after adding a real skill.** The empty-skills test
   removed only its synthetic skill but retained imported skills copied from the
   repository. Tests now reset their copied skill collection before creating
   fixtures. A fresh agent reran all 32 tests with a customized unslop skill
   present; all passed and the skill's contents and modes stayed unchanged.

The review-log directory now also exists in the skeleton, avoiding an unnecessary
missing-directory error when an agent writes its first review entry.

Regression coverage includes normal Git staging and fresh-clone integrity,
operation contention and retry, and the existing installer behavior tests.

## Handoff continuation

The fourth agent received only the generated OS-temporary handoff path and the
request to continue evaluating changes without adopting them. It recovered the
isolated checkout, original/current SHAs, local customization, review ledger,
exact diffs, and test history from the referenced files.

It independently regenerated matching diffs and confirmed two pending choices:
adapting upstream's explicit Skill-tool wording for different coding agents, and
adding upstream `agents/openai.yaml` metadata. Neither change was adopted.
Protected hashes matched the producer's record. No blocking handoff omission
was found; one test-source line reference was slightly stale.

## Skill behavior and limits

- Unslop preserved command strings and improved prose, but its meaning-preserving
  style pass also preserved a deliberately false claim that `check` automatically
  imports updates. A separate factual check caught it. Requiring factual review
  is a candidate for our future adaptation, not a feature of upstream unslop.
- Handoff's current wording assumes a literal Skill tool. The continuation agent
  recognized that this is not available in every coding agent and proposed wording
  that fits each agent's invocation mechanism.
- The sample gstack adaptation still depends on gstack's runtime helpers. This
  evaluation proves provenance and review behavior, not standalone deployment of
  that draft skill across coding agents.
- These were agent simulations on macOS. The repository's CI matrix covers macOS
  and Linux, but this evaluation did not publish a branch or run remote CI.

To repeat, create isolated copies, choose the historical refs above with `track`,
make a small explicit local adaptation, and run both upstream and local diffs.
Pass only the produced handoff artifact to a fresh agent. Keep adoption decisions
pending and compare before/after hashes of the manifest, baseline archives, and
local skills. Run `python3 -m unittest discover -s tests -v` in a copy containing
at least one customized skill.
