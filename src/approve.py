#!/usr/bin/env python3
"""
NIGHTLY — Approve a proposed change (observation-mode workflow).

In observation mode (the default), /nightly proposes changes but never
commits them — it writes the proposal + diff + score to
~/.claude/nightly/proposed/<run_id>.md and reverts. This script lets the
user manually approve a proposal: re-apply the diff and commit with the
correct author email.

Usage:
  python3 approve.py <run_id>

Workflow:
  1. Load the proposal from ~/.claude/nightly/proposed/<run_id>.md.
  2. Re-apply the recorded diff to ~/.claude/ (relative to baseline_commit).
  3. Verify safety_check still passes (substrate may have evolved since).
  4. Commit with the right email.
  5. Append a 'user-approved' entry to experiment-log.jsonl.
  6. Remove the proposal file (it's now in git history).

Reject the symmetric case: see reject.py.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
CLAUDE = Path.home() / ".claude"
PROPOSED_DIR = NIGHTLY / "proposed"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"


def load_proposal(run_id: str) -> dict | None:
    """Proposal markdown contains a YAML front-matter-ish block with run metadata
    plus a fenced diff. Parse both."""
    p = PROPOSED_DIR / f"{run_id}.md"
    if not p.exists():
        return None
    text = p.read_text()
    # Extract metadata block (between first `---meta` fence and next `---`)
    meta_re = re.compile(r"```json\n(.*?)\n```", re.DOTALL)
    meta_match = meta_re.search(text)
    if not meta_match:
        return None
    try:
        meta = json.loads(meta_match.group(1))
    except Exception:
        return None
    # Extract diff block
    diff_re = re.compile(r"```diff\n(.*?)\n```", re.DOTALL)
    diff_match = diff_re.search(text)
    diff_text = diff_match.group(1) if diff_match else None
    meta["_diff"] = diff_text
    meta["_path"] = str(p)
    return meta


def apply_diff(diff_text: str) -> bool:
    """Apply a unified diff to ~/.claude/. Returns True on success."""
    proc = subprocess.run(
        ["git", "-C", str(CLAUDE), "apply", "--whitespace=nowarn", "-"],
        input=diff_text, text=True, capture_output=True,
    )
    if proc.returncode != 0:
        print(f"git apply failed:\n{proc.stderr}", file=sys.stderr)
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    args = ap.parse_args()

    proposal = load_proposal(args.run_id)
    if proposal is None:
        print(f"no proposal found for run {args.run_id} at {PROPOSED_DIR}/{args.run_id}.md", file=sys.stderr)
        return 2

    if not proposal.get("_diff"):
        print(f"proposal {args.run_id} has no diff block; cannot apply", file=sys.stderr)
        return 2

    # Verify tree clean before applying
    porcelain = subprocess.check_output(
        ["git", "-C", str(CLAUDE), "status", "--porcelain", "--untracked-files=all"],
        text=True,
    ).strip()
    if porcelain:
        print("substrate has uncommitted changes; resolve first:", file=sys.stderr)
        print(porcelain, file=sys.stderr)
        return 3

    # Apply
    if not apply_diff(proposal["_diff"]):
        return 4

    # Safety check on the target
    target = proposal.get("target_file", "").lstrip("/").replace("~/.claude/", "").replace(str(CLAUDE) + "/", "")
    if target:
        sc = subprocess.run(
            [sys.executable, str(NIGHTLY / "safety_check.py"), "--target", target],
            capture_output=True, text=True,
        )
        if sc.returncode != 0:
            # Revert
            subprocess.run(["git", "-C", str(CLAUDE), "checkout", "."])
            subprocess.run(["git", "-C", str(CLAUDE), "clean", "-fd"])
            print(f"safety_check rejected the re-applied change: {sc.stderr}", file=sys.stderr)
            return 5

    # Commit with the right email
    subprocess.run(["git", "-C", str(CLAUDE), "add", "-A"], check=True)
    msg = (
        f"nightly {args.run_id}: user-approved "
        f"{proposal.get('strategy','?')} on {proposal.get('target_file','?')}\n\n"
        f"Originally proposed in observation mode with score "
        f"{proposal.get('score_mean','?')}. Manually approved and applied."
    )
    subprocess.run(
        ["git", "-C", str(CLAUDE),
         "-c", "user.name=Arnav Maurya",
         "-c", "user.email=arnavmaurya.am@gmail.com",
         "commit", "-q", "-m", msg],
        check=True,
    )
    new_sha = subprocess.check_output(
        ["git", "-C", str(CLAUDE), "rev-parse", "HEAD"], text=True
    ).strip()

    # Append to experiment-log
    entry = {
        "run_id": f"{args.run_id}-approved",
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "strategy": "user-approval",
        "target_file": proposal.get("target_file"),
        "baseline_commit": proposal.get("baseline_commit"),
        "new_commit": new_sha,
        "baseline_score": None,
        "score_mean": proposal.get("score_mean"),
        "delta": None,
        "decision": "user-approved",
        "n_replayed": 0,
        "budget_used_usd": 0.0,
        "notes": f"Manually approved from proposal at {proposal['_path']}",
    }
    with EXP_LOG.open("a") as fh:
        fh.write(json.dumps(entry) + "\n")

    # Remove proposal file
    Path(proposal["_path"]).unlink()

    print(f"approved {args.run_id}: committed {new_sha[:8]}, proposal cleaned up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
