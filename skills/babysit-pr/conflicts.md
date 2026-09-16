# Resolve a merge or rebase conflict

1. See the state: `git status`, the conflicting files, and the commits on both sides.
2. Find the intent of each side from commit messages, the PR, and the issue it closes.
3. Resolve each hunk keeping both intents. Where they cannot coexist, keep the one matching the PR's stated goal and note the trade-off for the reply. Invent no new behaviour. Resolve rather than abort.
4. Run the repository's checks in this order: typecheck, tests, formatter. Fix what the merge broke.
5. Continue the rebase to its end, or commit the merge.
