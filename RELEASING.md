# Making changes and releases

## A change

1. `git switch main && git pull && git switch -c <short-name>`.
2. Make the change and add a line under **## [Unreleased]** in `CHANGELOG.md`.
3. Restart and check it: `systemctl --user restart orbit-helper`. For `homewatch.sh`, `adb -s <tv>:5555 shell pkill -f homewatch.sh` and the helper pushes the new copy within 30 s.
4. `git push -u origin HEAD && gh pr create --fill`. CI compiles the Python and runs shellcheck.
5. When it's green: `gh pr merge --squash --delete-branch && git switch main && git pull`.

## A release

1. In a PR: rename **[Unreleased]** to `## [X.Y.Z] - YYYY-MM-DD`, add a fresh empty **[Unreleased]** above it, and update the compare links. Merge it.
2. On `main`: `scripts/release.sh X.Y.Z`. It tags `vX.Y.Z` and creates the GitHub release with that version's changelog as notes.
