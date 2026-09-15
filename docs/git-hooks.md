# Optional Gitleaks pre-push guard

The guard checks outgoing commit patches for secrets before Git sends a push.
It runs independently of Claude, Codex, or any other harness. Installation is
explicit per repository; the ordinary astack installer does not enable it.

## Install, replace, and remove

From an astack checkout:

```sh
python3 scripts/git_hooks.py install --repo /path/to/project --dry-run
python3 scripts/git_hooks.py install --repo /path/to/project

# Replace gstack's managed guard while retaining it for restoration:
python3 scripts/git_hooks.py install --repo /path/to/project --replace-gstack

python3 scripts/git_hooks.py uninstall --repo /path/to/project --dry-run
python3 scripts/git_hooks.py uninstall --repo /path/to/project
```

Requirements: Python 3.10+, Git 2.31+, and macOS or Linux. Online installation
downloads Gitleaks 8.30.1 for ARM64 or x64 from its official GitHub release and
checks the archive against the committed SHA-256 in `tools/gitleaks/releases.json`.
Only the executable is read from the archive; arbitrary archive paths are not
extracted. The MIT license is bundled beside the installed executable.

For offline installation, provide a trusted executable of that exact version:

```sh
python3 scripts/git_hooks.py install --repo /path/to/project \
  --gitleaks /path/to/gitleaks
```

The provided executable is version-checked and copied into the installation;
its provenance is the caller's responsibility. No download happens in this mode.
Dry runs neither download nor execute a scanner and create no files.

The hook and its payload live in the repository's common Git hooks directory:

```text
hooks/pre-push                   Managed entry point
hooks/pre-push.astack-original   Saved predecessor, when one existed
hooks/astack-gitleaks/           Runner, pinned executable, rules, license, state
```

Linked worktrees share this installation. It survives moving the astack checkout
or the target repository on the same platform. It does not change `core.hooksPath`,
global Git configuration, or other repositories. Custom hook directories (such as
Husky or a globally configured `core.hooksPath`) and symlinked hooks are refused
instead of modifying shared or committed configuration.

An existing executable hook is preserved and receives the original arguments and
complete stdin before the scanner runs. Its rejection still blocks the push.
`--replace-gstack` requires gstack's managed marker, saves that wrapper without
executing it, and continues to run its executable `pre-push.local`, if present.
It does not run or require gstack while the replacement is installed. The local
hook stays untouched; uninstall restores the gstack wrapper and its prior behavior.
Modified/custom wrappers should be reviewed before using this replacement option.

Reinstalling an intact installation is a no-op. To change versions or options,
uninstall first, then install. Uninstallation checks managed files and the saved
predecessor before changing anything; modified files or extra files require manual
reconciliation. It restores the predecessor's bytes and mode, or removes the hook
when none existed. `uninstall.sh` removes harness configuration only; use the command
above to remove this optional integration. Concurrent installer operations are
rejected; an interrupted operation may leave `astack-hooks.lock` in the common
Git directory. Confirm no installer is running before removing that lock.
If interruption occurs after the active hook is replaced, the complete installation
and saved predecessor are retained; retry installation or run uninstall to restore
the predecessor. A failure before replacement cleans up only when the original
hook is confirmed unchanged.

## What is scanned

- Existing branches: commits reachable from the proposed tip but not from the
  remote tip supplied by Git. Intermediate commits are included, even when a
  later commit deletes a secret. Force-pushes use the same reachability rule.
- New branches and tags: all history reachable from the proposed commit. This is
  deliberately conservative and can report historical findings already published
  on other branches. It does not trust another remote's tracking refs as proof
  that content is safe to publish to this destination.
- Merge commits: patches against each parent, including resolution changes.
- Annotated tags pointing to commits: their referenced commit history. Tags pointing
  directly to blobs or trees are rejected because they cannot be scanned this way.
- Ref deletions and unchanged refs: nothing new to scan. Chained hooks still run.

Missing remote-tip objects and shallow history block the push with a fetch/retry
message. The hook does not silently fetch, change refs, or widen permissions.
Git replacement objects are disabled during history selection and scanning so the
guard checks the original objects that Git will transfer, including annotated tags.
Git diff drivers and text conversions are disabled during scanning. The guard
uses Gitleaks' bundled default rules; working-tree `.gitleaks.toml`, `.gitleaksignore`,
`gitleaks:allow` comments, and `GITLEAKS_*` environment overrides cannot silently
weaken this guard. Custom allowlists are not part of this first version.

Secret findings and scanner failures block the push. Diagnostics show rule, path,
line, and commit without printing secret values or scanner stderr. Reports are
redacted and kept in a private temporary directory that is removed after scanning.
Each scan has a 120-second scanner deadline and a 150-second process timeout;
expiration blocks rather than claiming a clean result.

This is pattern-based detection, not proof that a push is free of secrets. Binary
and archive coverage follows Gitleaks' Git-patch scanner; this integration does not
enable archive traversal. It does not scan PR descriptions or rotate credentials.
As with other local hooks, `git push --no-verify` bypasses it (and all other pre-push
checks). Use that only as a deliberate decision after investigating the finding.

## Verification and updates

The default test suite uses temporary Git repositories and a fake executable for
failure/lifecycle checks. To exercise actual pushes with the pinned scanner:

```sh
ASTACK_TEST_GITLEAKS=/path/to/gitleaks \
  python3 -m unittest discover -s tests -p test_git_hooks.py -v
```

CI provisions the verified release into a disposable repository and runs these
tests on macOS and Linux. Tests cover install/reinstall/uninstall, worktrees,
hook chaining and gstack replacement, scanner failures, clean pushes, intermediate
commits, force-pushes, merge resolutions, annotated tags, and multiple refs.

To upgrade, review the upstream release and detection behavior, update `VERSION`
in `scripts/gitleaks_pre_push.py`, the release checksums, and this documentation;
run both lifecycle and real-scanner tests before publishing. Download pins are
release provenance, not an imported astack skill or a gstack runtime dependency.

Sources: [Gitleaks release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1),
[Git scanning](https://github.com/gitleaks/gitleaks/tree/v8.30.1#git), and
[Git's pre-push interface](https://git-scm.com/docs/githooks#_pre_push).
