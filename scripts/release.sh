#!/bin/bash
# Tag the merged main branch as vX.Y.Z and create the GitHub release from its changelog section.
#   scripts/release.sh 1.1.0        (see RELEASING.md)
set -euo pipefail
cd "$(dirname "$0")/.."
V=${1:?usage: scripts/release.sh X.Y.Z}
[[ $V =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "version must be X.Y.Z"; exit 1; }
git fetch -q origin
[ "$(git branch --show-current)" = main ] || { echo "switch to main first"; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "working tree not clean"; exit 1; }
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "main differs from origin/main: pull first"; exit 1; }
git rev-parse -q --verify "refs/tags/v$V" >/dev/null && { echo "v$V already exists"; exit 1; }
grep -q "^## \[$V\] - " CHANGELOG.md || { echo "CHANGELOG.md has no '## [$V] - <date>' section"; exit 1; }
NOTES=$(mktemp); trap 'rm -f "$NOTES"' EXIT
awk -v v="$V" '$0 ~ "^## \\[" v "\\]" {on=1; next} on && /^## \[/ {exit} on && !/^\[.*\]: / {print}' CHANGELOG.md > "$NOTES"
git tag -a "v$V" -m "Orbit-Helper $V"
git push -q origin "v$V"
gh release create "v$V" --title "Orbit-Helper $V" --notes-file "$NOTES" --verify-tag
