# Upstream provenance and review

Our skills can be adaptations, rewrites, or combinations of upstream skills.
Tracking their origins does not require identical contents or automatic syncing.

`manifest.json` starts with three sources and no imported skills:

| Source ID | Repository | Skill paths are relative to |
| --- | --- | --- |
| `gstack` | [garrytan/gstack](https://github.com/garrytan/gstack) | Repository root, e.g. `review` |
| `pstack` | [cursor/plugins](https://github.com/cursor/plugins/tree/main/pstack) | Repository root, e.g. `pstack/skills/tdd` |
| `mattpocock` | [mattpocock/skills](https://github.com/mattpocock/skills) | Repository root; browse its `skills/` hierarchy for a specific skill |

No third-party skill content is imported by this skeleton. Choose skills as you
adapt them. A source's configured ref is a branch used to look for updates; each
import resolves its base to a full commit SHA at tracking time.

## Register an adaptation

From the astack checkout:

```sh
python3 scripts/upstream.py list
python3 scripts/upstream.py track review-gstack \
  --source gstack \
  --path review \
  --include bin/gstack-review-log \
  --include LICENSE \
  --local skills/review \
  --notes "Adapt review guidance for our harnesses; preserve our approval workflow."
```

This records the upstream repository, branch, exact commit, primary path, additional
paths, and intended local destination in `manifest.json`. It stores the original
bytes and executable modes in `bases/review-gstack.zip`, preserving upstream paths.
It does not create or overwrite `skills/review`; author that customization separately.
Commit the manifest and snapshot along with the adaptation so they travel together
to every computer. Keep relevant license and attribution files with adapted content.
Snapshots are ZIP files so upstream ignore rules and Git attributes cannot drop or
rewrite their contents when you commit them. Use the diff commands below to inspect
them, or open the ZIP for individual reference files.

Use `--ref <commit-or-tag>` if your actual starting version differs from today's
upstream branch. Do not pin today's version when adapting an older local copy.
Use `--include` for dependencies outside the primary skill directory, such as
shared helpers or generator templates. Only tracked paths appear in upstream diffs;
review dependency coverage when introducing new references. All path arguments
are relative to the **repository root**, including the `pstack/` prefix for pstack.

An import ID identifies one provenance relationship. Multiple IDs may point to the
same local skill when it draws from multiple upstream skills or repositories.
Local destinations can also be files under `instructions/`. The primary upstream
path and local path should both be files or both be directories for useful local diffs.

## Inspect changes

```sh
python3 scripts/upstream.py check                    # Summary for every import
python3 scripts/upstream.py check review-gstack      # Summary for one import
python3 scripts/upstream.py diff review-gstack       # Original -> current upstream
python3 scripts/upstream.py diff review-gstack --local  # Original -> our primary path
python3 scripts/upstream.py diff review-gstack --ref <reviewed-sha>
```

`check` and the default `diff` fetch the configured upstream branch and print the
resolved current SHA. Use that SHA with `diff --ref` to keep a review tied to one
revision even if the branch moves during your work. `diff --local` needs no network
and shows intentional divergence in the primary mapped path. For rewrites, use
the diff as context and compare behavior; mechanical patch application may not fit.

These commands never change skills, snapshots, or pins. Fetches use disposable bare
Git repositories under ignored `.cache/upstream/`, with no upstream checkout or setup
execution. Snapshots reject symlinks and submodules and are checked for unexpected
changes before comparisons. A whole tracked directory disappearing upstream is
reported as deletions. A move to a new, untracked path requires manual investigation.
Paths that alias on the host filesystem (for example `README.md` and `readme.md`
on a case-insensitive Mac) fail explicitly during snapshot creation or extraction,
instead of silently losing an original file. Review those sources on a
case-sensitive filesystem.
Commands that fetch or register imports take a repository-local lock. A concurrent
command exits with a retry message instead of overwriting another import's record.

Original snapshots are committed locally, so original-to-current comparison still
works if upstream rewrites or removes the old commit. Only the requested current
revision must remain fetchable. After deleting the cache or moving computers,
`check` recreates it. Network/ref errors are reported as errors, never as “no changes.”

## Record selective decisions

Keep the **original base immutable**. Updating a baseline to the latest SHA after
accepting one change would falsely imply every upstream change was incorporated.
Instead, add a review entry in `reviews/<import-id>.md` using this shape:

```markdown
## YYYY-MM-DD — upstream <full-reviewed-commit-sha>

- Adopted/adapted: <change, source files, local effect, and rationale>
- Skipped: <change and why it conflicts with our preferences>
- Deferred: <change and what would make it worth revisiting>
- Local validation: <tests or realistic skill exercises and their results>
- Local implementation: <files or commit, when available>
```

Only record decisions actually made; leave undecided changes marked as candidates.
A reviewed SHA records what was assessed, not what was fully merged. Future agents
read this ledger alongside the full base-to-current diff and avoid presenting
already-decided changes as new unless something relevant has changed.

The root `AGENTS.md` and `CLAUDE.md` direct agents to propose specific changes and
get your decision before adopting them, while honoring any approval already given
in the conversation. There is no automatic update, merge, or baseline-advance command.
