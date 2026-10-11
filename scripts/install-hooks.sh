#!/usr/bin/env bash
# install-hooks.sh — copies versioned git hooks into .git/hooks/
# Run this once after cloning the repo.

set -euo pipefail

repo_root="$(cd "$(dirname "$0")/.." && pwd)"
hooks_dst="$repo_root/.git/hooks"

if [ ! -d "$hooks_dst" ]; then
  echo "✘ $hooks_dst not found — is this a git repo?" >&2
  exit 1
fi

cp "$repo_root/scripts/commit-msg" "$hooks_dst/commit-msg"
chmod +x "$hooks_dst/commit-msg"
echo "✓ installed commit-msg — enforces Conventional Commits"
