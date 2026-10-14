#!/usr/bin/env bash
# Get the current branch into main, safely: only a fast-forward, only after the checks pass.
#
#   scripts/to-main.sh              # checks, then asks before pushing HEAD to origin/main
#   scripts/to-main.sh --yes        # no prompt
#   scripts/to-main.sh --skip-checks
#   scripts/to-main.sh --tag v0.1.0 # also create and push that tag on the pushed commit
#
# It refuses when the tree has uncommitted changes, when main has commits this branch lacks
# (merge or rebase main into the branch first), and when a commit adds something that looks
# like an API key.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

YES=0; SKIP=0; TAG=""
while [ $# -gt 0 ]; do
  case "$1" in
    --yes|-y) YES=1 ;;
    --skip-checks) SKIP=1 ;;
    --tag) TAG="${2:?--tag needs a name}"; shift ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
  shift
done

branch="$(git branch --show-current)"
[ -n "$branch" ] || { echo "detached HEAD: check out a branch first" >&2; exit 1; }
[ "$branch" != "main" ] || { echo "already on main: nothing to merge" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "uncommitted changes: commit or stash them first" >&2; exit 1; }

git fetch -q origin main
behind="$(git rev-list --count HEAD..origin/main)"
ahead="$(git rev-list --count origin/main..HEAD)"
[ "$behind" -eq 0 ] || { echo "origin/main has $behind commit(s) not in $branch; merge or rebase main first" >&2; exit 1; }
[ "$ahead" -gt 0 ] || { echo "$branch has nothing new for main"; exit 0; }

if git log -p origin/main..HEAD | grep -E '^\+' | grep -qE 'sk-ant-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}|ghp_[A-Za-z0-9]{30,}'; then
  echo "a commit adds something that looks like a secret; remove it from history before pushing" >&2
  exit 1
fi

[ "$SKIP" -eq 1 ] || scripts/check.sh

echo
git log --oneline origin/main..HEAD | head -20
echo "($ahead commit(s)) $branch -> origin/main"
if [ "$YES" -ne 1 ]; then
  read -r -p "Push to main? [y/N] " answer
  [ "$answer" = "y" ] || { echo "not pushed"; exit 1; }
fi
git push origin HEAD:main
if [ -n "$TAG" ]; then
  git tag -a "$TAG" -m "$TAG"
  git push origin "$TAG"
fi
echo "main is now at $(git rev-parse --short HEAD)"
