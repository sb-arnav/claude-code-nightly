#!/usr/bin/env bash
# NIGHTLY — pre-run snapshot.
#
# Commits all dirty files in ~/.claude before a nightly run.
# This directory is auto-managed by Claude Code — all changes are safe to commit.
#
# Idempotent. Safe to run before every /nightly invocation.

set -euo pipefail
CLAUDE_DIR="${HOME}/.claude"
cd "${CLAUDE_DIR}"

if [[ ! -d .git ]]; then
  echo "snapshot: not a git repo — run nightly/install.sh first" >&2
  exit 2
fi

if git status --porcelain --untracked-files=all | grep -q .; then
  git add -A
  git -c user.name="nightly-snapshot" -c user.email="nightly@localhost" \
      commit -q -m "nightly: auto-snapshot before run"
  echo "snapshot: committed $(git rev-parse --short HEAD)"
else
  echo "snapshot: clean tree, nothing to do"
fi
