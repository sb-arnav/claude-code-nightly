#!/usr/bin/env python3
"""
NIGHTLY — Bootstrap Baseline.

Writes a seed entry to experiment-log.jsonl so the first real /nightly run
has something to compare against. The seed is a *synthetic* baseline: it
simulates "perfect-fidelity replay" by feeding each benchmark task's own
ground-truth metrics back into the scorer.

This is not a real measurement of current ~/.claude. It's a fixed anchor
that the first real run replaces. The point is to avoid the cold-start
problem where night 1 has nothing to beat and the loop's keep/revert
decision is meaningless.

Run once during install:
  python3 ~/.claude/nightly/baseline.py
"""

from __future__ import annotations

import json
import statistics
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
BENCH = NIGHTLY / "benchmark.jsonl"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"
SCORER = NIGHTLY / "scorer.py"


def synth_response_from_gt(entry: dict) -> dict:
    gt = entry["ground_truth"]
    # Reconstruct a response that mirrors ground truth on every mechanical
    # signal. response_text is intentionally short and neutral — it cannot
    # contain premature/options patterns by construction.
    return {
        "benchmark_id": entry["benchmark_id"],
        "duration_sec": gt["duration_sec"],
        "output_tokens": gt["output_tokens"],
        "response_text": "(synthetic ground-truth-fidelity baseline)",
        "tools": gt["tools"],
        "files_changed": [None] * gt["files_changed_count"],
        "tool_call_sequence": list(gt["tools"].keys()),
        "completed_cleanly": gt["outcome"] in ("completed", "corrected"),
        "correction_hook_fired": gt["correction_logged"],
    }


def main() -> int:
    if not BENCH.exists():
        print(f"benchmark missing: {BENCH}. Run benchmark.py first.", file=sys.stderr)
        return 2

    run_id = "baseline-seed"
    run_dir = NIGHTLY / "experiments" / run_id / "responses"
    run_dir.mkdir(parents=True, exist_ok=True)

    n = 0
    with BENCH.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if not entry.get("replayable"):
                continue
            resp = synth_response_from_gt(entry)
            (run_dir / f"{entry['benchmark_id']}.json").write_text(json.dumps(resp), encoding="utf-8")
            n += 1

    score_path = NIGHTLY / "experiments" / run_id / "score.json"
    subprocess.run(
        [sys.executable, str(SCORER),
         "--benchmark", str(BENCH),
         "--run-dir", str(run_dir),
         "--out", str(score_path)],
        check=True,
    )
    score = json.loads(score_path.read_text())

    log_entry = {
        "run_id": run_id,
        "ts": datetime.now(tz=timezone.utc).isoformat(),
        "strategy": "synthetic-baseline-seed",
        "target_file": None,
        "baseline_commit": None,
        "new_commit": None,
        "baseline_score": None,
        "score_mean": score["score_mean"],
        "score_median": score["score_median"],
        "delta": None,
        "decision": "seed",
        "n_replayed": n,
        "budget_used_usd": 0.0,
        "notes": "Synthetic baseline from ground-truth-fidelity replay. Replaced by first real run.",
    }
    with EXP_LOG.open("a") as fh:
        fh.write(json.dumps(log_entry) + "\n")

    print(f"baseline seeded: n={n} mean={score['score_mean']} median={score['score_median']}")
    print(f"  experiment-log: {EXP_LOG}")
    print(f"  score detail:   {score_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
