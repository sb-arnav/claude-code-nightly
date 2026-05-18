#!/usr/bin/env python3
"""
NIGHTLY — Disapprove (two-way feedback).

If the user reads a morning report and disagrees with the kept change, he can
veto it. This script:

  1. Looks up the run in experiment-log.jsonl.
  2. Reverts the git commit that introduced the change (if still HEAD).
  3. Appends an entry to corrections.jsonl explaining the veto (so the next
     night's proposer learns from it like any other correction).
  4. Appends to dead-letter.jsonl so the same (strategy, target_file) won't
     be re-tried.
  5. Logs the disapproval back into experiment-log.jsonl.

Usage:
  python3 ~/.claude/nightly/disapprove.py <run_id> "<reason>"

The reason becomes the `what_you_said` field of the resulting correction —
write it the way you'd correct Claude in chat. Example:

  python3 ~/.claude/nightly/disapprove.py 2026-05-18-2200 \\
    "don't add hedging language to the operating mode; that's the opposite of position-first"
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
CLAUDE = Path.home() / ".claude"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"
DEAD = NIGHTLY / "dead-letter.jsonl"
CORRECTIONS = CLAUDE / "corrections.jsonl"


def find_run(run_id: str) -> dict | None:
    if not EXP_LOG.exists():
        return None
    last = None
    with EXP_LOG.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("run_id") == run_id:
                last = o
    return last


def head_sha() -> str:
    return subprocess.check_output(
        ["git", "-C", str(CLAUDE), "rev-parse", "HEAD"], text=True
    ).strip()


def revert_if_head(commit_sha: str) -> str:
    """If commit_sha is still HEAD, revert it (preferred — preserves history).
    If newer commits sit on top, do a `git revert` of the specific commit
    (may conflict; abort and tell user)."""
    current = head_sha()
    if commit_sha == current:
        subprocess.check_call(
            ["git", "-C", str(CLAUDE), "reset", "--hard", "HEAD^"]
        )
        return "reset-hard"
    # Not HEAD anymore — try a non-HEAD revert
    proc = subprocess.run(
        ["git", "-C", str(CLAUDE), "revert", "--no-edit", commit_sha],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        # Abort the revert so the tree is clean
        subprocess.run(
            ["git", "-C", str(CLAUDE), "revert", "--abort"],
            capture_output=True,
        )
        raise RuntimeError(
            f"revert failed (likely conflicts with newer commits). "
            f"Manual cleanup needed:\n{proc.stderr}"
        )
    return "revert-commit"


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: disapprove.py <run_id> \"<reason>\"", file=sys.stderr)
        return 2
    run_id = sys.argv[1]
    reason = sys.argv[2]

    run = find_run(run_id)
    if run is None:
        print(f"run not found: {run_id}", file=sys.stderr)
        return 2
    if run.get("decision") not in ("kept", "first-real-baseline"):
        print(
            f"run {run_id} has decision={run.get('decision')}; nothing to disapprove",
            file=sys.stderr,
        )
        return 2

    new_commit = run.get("new_commit")
    if not new_commit:
        print(f"run {run_id} has no new_commit recorded; cannot revert", file=sys.stderr)
        return 2

    # 1. Revert the change in git.
    revert_mode = revert_if_head(new_commit)

    # 2. Append to corrections.jsonl so the proposer learns.
    correction = {
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "project": "workspace",
        "prompt": f"NIGHTLY {run_id}: {run.get('strategy')} on {run.get('target_file')}",
        "what_i_did": run.get("notes") or run.get("strategy"),
        "what_you_said": reason,
        "supposed_to": f"avoid this change; do not retry the same (strategy={run.get('strategy')}, target={run.get('target_file')}) pair",
        "root_cause": "nightly-disapproved",
        "proposed_rule": f"Dead-letter (strategy={run.get('strategy')}, target_file={run.get('target_file')}). Future /nightly runs MUST NOT propose the same pair. The reason the user vetoed: {reason}",
    }
    with CORRECTIONS.open("a") as fh:
        fh.write(json.dumps(correction) + "\n")

    # 3. Append to dead-letter.jsonl.
    dl_entry = {
        "run_id": run_id,
        "strategy": run.get("strategy"),
        "target_file": run.get("target_file"),
        "reverted_commit": new_commit,
        "revert_mode": revert_mode,
        "reason": reason,
        "ts": datetime.now(tz=timezone.utc).isoformat(),
    }
    with DEAD.open("a") as fh:
        fh.write(json.dumps(dl_entry) + "\n")

    # 4. Append a disapproval entry to experiment-log so timelines stay coherent.
    log_entry = {
        "run_id": f"{run_id}-disapproved",
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "strategy": "user-disapproval",
        "target_file": run.get("target_file"),
        "baseline_commit": new_commit,
        "new_commit": head_sha(),
        "baseline_score": run.get("score_mean"),
        "score_mean": None,
        "delta": None,
        "decision": "user-reverted",
        "n_replayed": 0,
        "budget_used_usd": 0.0,
        "notes": f"Disapproved by the user. revert_mode={revert_mode}. reason={reason}",
    }
    with EXP_LOG.open("a") as fh:
        fh.write(json.dumps(log_entry) + "\n")

    print(f"disapproved {run_id}: reverted ({revert_mode}), dead-lettered, correction logged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
