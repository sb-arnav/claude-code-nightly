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

# Force UTF-8 stdio on Windows where Python defaults to cp1252; without this,
# print() of any Unicode (em-dash, arrows, smart quotes — i.e. most Claude
# output) crashes with UnicodeEncodeError. Idempotent and safe on all platforms.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
from datetime import datetime, timezone
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
CLAUDE = Path.home() / ".claude"
PROPOSED_DIR = NIGHTLY / "proposed"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"


def parse_markdown_meta(text: str) -> dict:
    """The optimizer writes proposals as human-readable markdown (**Strategy:**,
    **Target:**, **Dry-run score:** …), not a ```json block. Extract the fields
    approve/reject need from those lines. Kept tolerant: missing fields are just
    absent from the returned dict."""
    meta: dict = {}

    def field(label: str) -> str | None:
        m = re.search(rf"\*\*{label}:\*\*\s*`?([^`\n]+?)`?\s*$", text, re.MULTILINE)
        return m.group(1).strip() if m else None

    if (s := field("Strategy")):
        meta["strategy"] = s
    if (t := field("Target")):
        meta["target_file"] = t
    # "**Dry-run score:** 0.9878 → 0.9878 (Δ 0.0)" — first float after the label.
    sm = re.search(r"\*\*(?:Dry-run score|Score):\*\*\s*([\d.]+)", text)
    if sm:
        try:
            meta["score_mean"] = float(sm.group(1))
        except ValueError:
            pass
    return meta


def load_proposal(run_id: str) -> dict | None:
    """Load proposal metadata + diff. Supports both the legacy ```json metadata
    block and the human-readable markdown the optimizer actually writes."""
    p = PROPOSED_DIR / f"{run_id}.md"
    if not p.exists():
        return None
    text = p.read_text()
    meta_match = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    meta: dict | None = None
    if meta_match:
        try:
            meta = json.loads(meta_match.group(1))
        except Exception:
            meta = None
    if meta is None:
        meta = parse_markdown_meta(text)
    # A proposal we can't identify (no strategy AND no target) is unusable.
    if not meta.get("strategy") and not meta.get("target_file"):
        return None
    # Extract diff block
    diff_match = re.search(r"```diff\n(.*?)\n```", text, re.DOTALL)
    meta["_diff"] = diff_match.group(1) if diff_match else None
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

    # The diff only needs a clean *target* to apply. We deliberately do NOT
    # require the whole tree to be clean: the proposal file itself is untracked
    # (it would always trip a whole-tree check while it exists), and snapshot's
    # housekeeping files (experiment-log, session-state, reports, .last-cleanup)
    # are routinely dirty between snapshots. Verify only the target file.
    target = proposal.get("target_file", "").lstrip("/").replace("~/.claude/", "").replace(str(CLAUDE) + "/", "")
    if target:
        target_status = subprocess.check_output(
            ["git", "-C", str(CLAUDE), "status", "--porcelain", "--", target],
            text=True,
        ).strip()
        if target_status:
            print(f"target {target} has uncommitted changes; resolve first:", file=sys.stderr)
            print(target_status, file=sys.stderr)
            return 3

    # Apply
    if not apply_diff(proposal["_diff"]):
        return 4

    # Safety check on the target
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

    # Remove the proposal file BEFORE committing so its removal is captured in
    # this same commit. (Committing first and unlinking after leaves a dangling
    # tracked-then-deleted file that re-blocks the next snapshot.)
    Path(proposal["_path"]).unlink()

    # Commit using the user's own git identity — a user-approved change
    # should be authored by the user, not by the nightly bot. (The bot
    # identity is reserved for automatic snapshots in snapshot.sh.)
    subprocess.run(["git", "-C", str(CLAUDE), "add", "-A"], check=True)
    msg = (
        f"nightly {args.run_id}: user-approved "
        f"{proposal.get('strategy','?')} on {proposal.get('target_file','?')}\n\n"
        f"Originally proposed in observation mode with score "
        f"{proposal.get('score_mean','?')}. Manually approved and applied."
    )
    subprocess.run(
        ["git", "-C", str(CLAUDE), "commit", "-q", "-m", msg],
        check=True,
    )
    new_sha = subprocess.check_output(
        ["git", "-C", str(CLAUDE), "rev-parse", "HEAD"], text=True
    ).strip()

    # Append to experiment-log (allowlisted — the next snapshot commits it).
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

    print(f"approved {args.run_id}: committed {new_sha[:8]}, proposal cleaned up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
