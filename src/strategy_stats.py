#!/usr/bin/env python3
"""
NIGHTLY — Strategy Effectiveness Tracker.

Reads experiment-log.jsonl and surfaces per-strategy success rates so the
nightly-optimizer agent can bias toward strategies that actually work.

Inspired by cgraves09/autoskill's FINDINGS.md observation that out of 7 named
mutation operators, only 2 produced lasting improvement. Without per-strategy
tracking, the optimizer wastes runs on strategies that empirically don't move
the score.

Usage:
  python3 strategy_stats.py            # human-readable table
  python3 strategy_stats.py --json     # machine-readable for the agent
"""

from __future__ import annotations

import argparse
import json
import sys

# Force UTF-8 stdio on Windows where Python defaults to cp1252; without this,
# print() of any Unicode (em-dash, arrows, smart quotes — i.e. most Claude
# output) crashes with UnicodeEncodeError. Idempotent and safe on all platforms.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"
DEAD = NIGHTLY / "dead-letter.jsonl"


def load_log() -> list[dict]:
    if not EXP_LOG.exists():
        return []
    out = []
    with EXP_LOG.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def load_deadletter() -> list[dict]:
    if not DEAD.exists():
        return []
    out = []
    with DEAD.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# Decisions that count as "kept" (the change is now in the substrate)
KEPT_DECISIONS = {"kept", "first-real-baseline"}
# Decisions that count as "tried but didn't survive"
LOST_DECISIONS = {"reverted", "held", "deadletter-blocked", "user-reverted"}


def compute_stats(log: list[dict]) -> dict[str, dict]:
    """Per-strategy: tried, kept, success_rate, last_kept_ts, last_tried_ts."""
    stats: dict[str, dict] = defaultdict(
        lambda: {"tried": 0, "kept": 0, "lost": 0, "last_kept_ts": None, "last_tried_ts": None}
    )
    for entry in log:
        strategy = entry.get("strategy")
        if not strategy or strategy in ("synthetic-baseline-seed", "user-disapproval"):
            continue
        ts = entry.get("ts")
        decision = entry.get("decision")
        s = stats[strategy]
        s["tried"] += 1
        if ts and (s["last_tried_ts"] is None or ts > s["last_tried_ts"]):
            s["last_tried_ts"] = ts
        if decision in KEPT_DECISIONS:
            s["kept"] += 1
            if ts and (s["last_kept_ts"] is None or ts > s["last_kept_ts"]):
                s["last_kept_ts"] = ts
        elif decision in LOST_DECISIONS:
            s["lost"] += 1

    # Compute rates + recommendation
    out: dict[str, dict] = {}
    for strategy, s in stats.items():
        rate = s["kept"] / s["tried"] if s["tried"] else 0.0
        # Recommendation logic:
        #  - "untried": never tried — try at least once
        #  - "promising": ≥40% success across ≥3 attempts — keep using
        #  - "neutral":   between 10-40% OR small sample — try occasionally
        #  - "avoid":     <10% across ≥5 attempts — don't propose unless forced
        if s["tried"] == 0:
            rec = "untried"
        elif s["tried"] >= 5 and rate < 0.10:
            rec = "avoid"
        elif s["tried"] >= 3 and rate >= 0.40:
            rec = "promising"
        else:
            rec = "neutral"
        out[strategy] = {
            "tried": s["tried"],
            "kept": s["kept"],
            "lost": s["lost"],
            "success_rate": round(rate, 3),
            "last_kept_ts": s["last_kept_ts"],
            "last_tried_ts": s["last_tried_ts"],
            "recommendation": rec,
        }
    return out


# The full strategy menu as declared in the agent prompt. Surface untried ones
# explicitly so the agent biases toward exploration of unknowns.
ALL_STRATEGIES = [
    "rule-rewrite",
    "hook-tighten",
    "memory-add",
    "skill-description-tighten",
    "rule-reorder",
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="Machine-readable JSON output")
    args = ap.parse_args()

    log = load_log()
    stats = compute_stats(log)
    deadletter_pairs = [(d.get("strategy"), d.get("target_file")) for d in load_deadletter()]

    # Ensure every declared strategy appears, even if untried
    for s in ALL_STRATEGIES:
        if s not in stats:
            stats[s] = {
                "tried": 0, "kept": 0, "lost": 0, "success_rate": 0.0,
                "last_kept_ts": None, "last_tried_ts": None, "recommendation": "untried",
            }

    payload = {
        "n_total_runs": len(log),
        "per_strategy": stats,
        "dead_lettered_pairs": deadletter_pairs,
        "promising": sorted([s for s, v in stats.items() if v["recommendation"] == "promising"],
                            key=lambda s: -stats[s]["success_rate"]),
        "untried":   sorted([s for s, v in stats.items() if v["recommendation"] == "untried"]),
        "avoid":     sorted([s for s, v in stats.items() if v["recommendation"] == "avoid"]),
        "neutral":   sorted([s for s, v in stats.items() if v["recommendation"] == "neutral"]),
        "as_of": datetime.now(tz=timezone.utc).isoformat(),
    }

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    # Human-readable
    print(f"NIGHTLY strategy stats — {payload['n_total_runs']} runs in experiment-log")
    print()
    print(f"{'strategy':<32} {'tried':>5} {'kept':>5} {'rate':>6} {'rec':<10} last_kept")
    print("-" * 80)
    for s, v in sorted(stats.items(), key=lambda kv: (-kv[1]["success_rate"], kv[0])):
        last = (v["last_kept_ts"] or "—")[:10]
        print(f"{s:<32} {v['tried']:>5} {v['kept']:>5} {v['success_rate']:>6.2%} {v['recommendation']:<10} {last}")
    print()
    if payload["dead_lettered_pairs"]:
        print("dead-lettered (strategy, target):")
        for s, t in payload["dead_lettered_pairs"]:
            print(f"  - {s}  →  {t}")
    print()
    print("Agent guidance:")
    if payload["promising"]:
        print(f"  PREFER:  {', '.join(payload['promising'])}")
    if payload["untried"]:
        print(f"  EXPLORE: {', '.join(payload['untried'])} (no data yet)")
    if payload["avoid"]:
        print(f"  AVOID:   {', '.join(payload['avoid'])} (proven ineffective)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
