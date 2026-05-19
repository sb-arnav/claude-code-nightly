#!/usr/bin/env bash
# NIGHTLY — pre-run snapshot.
#
# Commits ONLY the append-only / auto-generated files that may have drifted
# during the day: memory/, corrections.jsonl. Anything else dirty is treated
# as real WIP — the script exits non-zero so /nightly aborts instead of
# steamrolling user work.
#
# Idempotent. Safe to run before every /nightly invocation.

set -euo pipefail
CLAUDE_DIR="${HOME}/.claude"
cd "${CLAUDE_DIR}"

if [[ ! -d .git ]]; then
  echo "snapshot: not a git repo — run nightly/install.sh first" >&2
  exit 2
fi

# Auto-snapshotted paths (relative to ~/.claude). Anything outside this list
# that is dirty will block the snapshot.
AUTOSAFE=(
  "memory/"
  "corrections.jsonl"
  "session-state.md"
  "projects/-home-arnav/memory/"        # gitignored anyway, kept for clarity
  "nightly/experiment-log.jsonl"        # loop's own append-only log
  "nightly/dead-letter.jsonl"           # loop's own deadletter log
  "nightly/reports/"                    # morning + weekly reports (audit trail)
  ".last-cleanup"                       # workspace cleanup timestamp
)

# What's currently dirty?
mapfile -t DIRTY < <(git status --porcelain | awk '{print $2}')
if [[ ${#DIRTY[@]} -eq 0 ]]; then
  echo "snapshot: clean tree, nothing to do"
  exit 0
fi

UNSAFE=()
for f in "${DIRTY[@]}"; do
  match=false
  for pat in "${AUTOSAFE[@]}"; do
    if [[ "$f" == "$pat"* ]]; then match=true; break; fi
  done
  if ! $match; then UNSAFE+=("$f"); fi
done

if [[ ${#UNSAFE[@]} -gt 0 ]]; then
  echo "snapshot: refusing to commit — unexpected dirty files (not in autosnap allowlist):" >&2
  printf '  - %s\n' "${UNSAFE[@]}" >&2
  echo "Inspect with: cd ~/.claude && git status" >&2
  exit 3
fi

# Commit just the autosafe paths.
git add -- "${AUTOSAFE[@]}" 2>/dev/null || true
if git diff --staged --quiet; then
  echo "snapshot: nothing staged after filtering"
  exit 0
fi
git -c user.name="nightly-snapshot" -c user.email="nightly@localhost" \
    commit -q -m "nightly: auto-snapshot memory + corrections before run

These paths are append-only during normal Claude Code use. Committed before
a nightly experiment so the loop has a clean baseline. Triggered by
nightly/snapshot.sh."
echo "snapshot: committed $(git rev-parse --short HEAD)"
