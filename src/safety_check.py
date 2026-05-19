#!/usr/bin/env python3
"""
NIGHTLY — Apply-time Safety Guard.

Verifies a proposed substrate change isn't destructive before the agent commits.
Modeled on cgraves09/autoskill's "minimum line count guard" — they observed
that without it, the optimizer kept producing 1-line rewrites that nuked
99-line skill files.

Run AFTER the agent has applied its edit but BEFORE git commit:

  python3 safety_check.py --target <file>

Exit codes:
  0 = safe to commit
  3 = unsafe — reject + revert + dead-letter the (strategy, target)

The agent's workflow doc instructs it to call this script and treat exit 3
as a "revert + log decision: 'unsafe-rejected'".
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

CLAUDE = Path.home() / ".claude"

# Files/dirs the agent must NEVER touch under any strategy.
FORBIDDEN = [
    ".git/",
    ".gitignore",
    ".credentials.json",
    ".mcp.json",
    "settings.json",
    "settings.local.json",
    "projects/",
    "plugins/",
    "statsig/",
    "ide/",
    "sessions/",
    "tasks/",
    "history.jsonl",
    "learning/",
    "file-history/",
    "paste-cache/",
    "telemetry/",
    "cache/",
    "backups/",
]

# Per-file safety rules for the file being modified.
MIN_REMAINING_LINES_IF_ORIGINAL_LARGE = 20  # files >50 lines must stay >=20
MAX_LINE_REDUCTION_RATIO = 0.50              # may not remove >50% of lines


def git_show(commit: str, path: str) -> str | None:
    """Get the file's content at a given commit. None if not present."""
    proc = subprocess.run(
        ["git", "-C", str(CLAUDE), "show", f"{commit}:{path}"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout


def lines_of(text: str | None) -> int:
    if not text:
        return 0
    return sum(1 for line in text.splitlines() if line.strip())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True, help="Path relative to ~/.claude/")
    ap.add_argument("--baseline-commit", default="HEAD~1",
                    help="Git ref to compare against (default HEAD~1 — pre-experiment state)")
    args = ap.parse_args()

    target = args.target.lstrip("/")
    # Normalize away leading ~/.claude/
    for prefix in (f"{CLAUDE}/", str(CLAUDE) + "/", "~/.claude/"):
        if target.startswith(prefix):
            target = target[len(prefix):]
            break

    # 1. Forbidden path check
    for f in FORBIDDEN:
        if target.startswith(f) or target == f.rstrip("/"):
            print(f"UNSAFE: target {target} matches forbidden path {f}", file=sys.stderr)
            return 3

    target_path = CLAUDE / target

    # 2. Did the change accidentally delete the file entirely?
    new_content = target_path.read_text() if target_path.exists() else None
    if new_content is None:
        # File deleted by the experiment — only allowed if the strategy is
        # explicitly "memory-remove" or similar, not implemented yet.
        print(f"UNSAFE: target {target} was deleted by the experiment", file=sys.stderr)
        return 3

    # 3. Compare line counts vs baseline
    old_content = git_show(args.baseline_commit, target)
    old_lines = lines_of(old_content)
    new_lines = lines_of(new_content)

    if old_lines > 0:
        if old_lines >= 50 and new_lines < MIN_REMAINING_LINES_IF_ORIGINAL_LARGE:
            print(
                f"UNSAFE: target {target} had {old_lines} lines; now {new_lines}. "
                f"Refusing — looks like destructive rewrite "
                f"(threshold: keep ≥{MIN_REMAINING_LINES_IF_ORIGINAL_LARGE} lines for files originally ≥50).",
                file=sys.stderr,
            )
            return 3
        reduction = (old_lines - new_lines) / old_lines
        if reduction > MAX_LINE_REDUCTION_RATIO:
            print(
                f"UNSAFE: target {target} lost {reduction:.0%} of its lines "
                f"({old_lines} → {new_lines}). Threshold: max {MAX_LINE_REDUCTION_RATIO:.0%}.",
                file=sys.stderr,
            )
            return 3

    print(f"safe: {target} ({old_lines} → {new_lines} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
