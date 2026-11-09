---
name: claude-merge-gate
description: Claude Code hook that denies an agent's PR merge until a signed review exists on the head commit.
disable-model-invocation: true
---

# Deny merges without a signed review

`scripts/hook.py` is a Claude Code `PreToolUse` hook for the Bash tool. It
allows a pull request merge only as a lone command:

```sh
gh pr merge [<number> | <url> | <branch>] [--repo <owner>/<repo>] --match-head-commit <sha> [flags]
```

The command must be one line of letters, digits, spaces, and `. _ : / = @ + , % -`,
so the shell passes every word through unchanged. Each flag must be a separate,
literal `gh pr merge` flag. The hook runs `gh pr view` in the session's working
directory and allows the merge when both hold:

- The head commit has a review whose first line carries the header that
  [`pr.py review`](../harden-pr/posting.md) writes, from the repository's owner,
  a member, or a collaborator. The hook ignores the review's outcome.
- `--match-head-commit` names that head commit.

`--disable-auto`, `--help`, and `-h` pass without a check.

The hook denies every other Bash command that mentions a PR merge: `pr merge`
(also with flags between the words), a `pulls/.../merge` endpoint,
`mergePullRequest`, `enablePullRequestAutoMerge`, or `enqueuePullRequest`. That
includes chains, pipes, redirects, `sh -c`, heredocs, and quoted text such as a
commit message that mentions `gh pr merge`. Its deny message asks for the lone
form.

Once the hook checks a merge, it fails closed. It denies the merge when `gh`
fails or times out. If the event is malformed, or the script
or `python3` is missing, the hook exits with code 1, and Claude Code reports the
hook error and runs the command.

The hook has no override. It misses merges in the GitHub web interface, in
non-Bash tools, through `gh` or shell aliases, in scripts that run the merge
themselves, in text built to hide the words, and pushes straight to the base
branch. A header typed by hand with `gh pr review` also passes.
