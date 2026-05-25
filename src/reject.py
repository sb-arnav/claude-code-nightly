#!/usr/bin/env python3
"""
NIGHTLY — Reject a proposed change.

The lighter counterpart to approve.py. Just:
  1. Append a (strategy, target_file) entry to dead-letter.jsonl so the
     same change isn't re-proposed.
  2. Remove the proposal file.
  3. Append a 'user-rejected' entry to experiment-log.jsonl.

Usage:
  python3 reject.py <run_id> "<reason>"

The reason becomes a `what_you_said`-shaped entry in corrections.jsonl so
the proposer learns from it like any other correction.
"""

from __future__ import annotations

import argparse
import json
import re
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
DEAD = NIGHTLY / "dead-letter.jsonl"
CORRECTIONS = CLAUDE / "corrections.jsonl"


def parse_markdown_meta(text: str) -> dict:
    """The optimizer writes proposals as human-readable markdown (**Strategy:**,
    **Target:** …), not a ```json block. Extract what reject needs."""
    meta: dict = {}

    def field(label: str) -> str | None:
        m = re.search(rf"\*\*{label}:\*\*\s*`?([^`\n]+?)`?\s*$", text, re.MULTILINE)
        return m.group(1).strip() if m else None

    if (s := field("Strategy")):
        meta["strategy"] = s
    if (t := field("Target")):
        meta["target_file"] = t
    return meta


def load_proposal_meta(run_id: str) -> dict | None:
    """Supports both the legacy ```json metadata block and the human-readable
    markdown the optimizer actually writes."""
    p = PROPOSED_DIR / f"{run_id}.md"
    if not p.exists():
        return None
    text = p.read_text()
    m = re.search(r"```json\n(.*?)\n```", text, re.DOTALL)
    meta: dict | None = None
    if m:
        try:
            meta = json.loads(m.group(1))
        except Exception:
            meta = None
    if meta is None:
        meta = parse_markdown_meta(text)
    if not meta.get("strategy") and not meta.get("target_file"):
        return None
    meta["_path"] = str(p)
    return meta


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("reason", help="Why you're rejecting; becomes a correction entry")
    args = ap.parse_args()

    proposal = load_proposal_meta(args.run_id)
    if proposal is None:
        print(f"no proposal found for run {args.run_id}", file=sys.stderr)
        return 2

    now = datetime.now(tz=timezone.utc).isoformat()
    strategy = proposal.get("strategy")
    target_file = proposal.get("target_file")

    # 1. Dead-letter
    dl_entry = {
        "run_id": args.run_id,
        "strategy": strategy,
        "target_file": target_file,
        "reverted_commit": None,  # never committed in observation mode
        "revert_mode": "observation-rejected",
        "reason": args.reason,
        "ts": now,
    }
    with DEAD.open("a") as fh:
        fh.write(json.dumps(dl_entry) + "\n")

    # 2. Correction so proposer learns
    correction = {
        "ts": now,
        "project": "workspace",
        "prompt": f"NIGHTLY {args.run_id}: {strategy} on {target_file}",
        "what_i_did": proposal.get("change_summary") or strategy,
        "what_you_said": args.reason,
        "supposed_to": f"avoid this change; do not retry the same (strategy={strategy}, target={target_file}) pair",
        "root_cause": "nightly-rejected-in-observation",
        "proposed_rule": f"Dead-letter (strategy={strategy}, target_file={target_file}). The reason: {args.reason}",
    }
    with CORRECTIONS.open("a") as fh:
        fh.write(json.dumps(correction) + "\n")

    # 3. Experiment-log
    log_entry = {
        "run_id": f"{args.run_id}-rejected",
        "ts": now,
        "strategy": "user-rejection",
        "target_file": target_file,
        "baseline_commit": proposal.get("baseline_commit"),
        "new_commit": None,
        "baseline_score": None,
        "score_mean": None,
        "delta": None,
        "decision": "user-rejected",
        "n_replayed": 0,
        "budget_used_usd": 0.0,
        "notes": f"Manually rejected from observation-mode proposal. reason={args.reason}",
    }
    with EXP_LOG.open("a") as fh:
        fh.write(json.dumps(log_entry) + "\n")

    # 4. Remove proposal
    Path(proposal["_path"]).unlink()

    print(f"rejected {args.run_id}: dead-lettered, correction logged, proposal cleaned up")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
