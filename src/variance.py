#!/usr/bin/env python3
"""
NIGHTLY — Variance estimation via bootstrap subsampling.

The fourth critique fix the external review named: "Δ ≥ +0.02 on a 40-task
benchmark is below noise. No variance estimation, no repeated trials per
proposal. A 'kept' decision and a 'reverted' decision a week apart could
easily be the same proposal with different sampling luck."

This module computes the noise floor of the score by running scorer.py N
times on different stratified subsamples of the same responses. The
score's stdev across subsamples is the noise; a Δ smaller than ~1.5×stdev
is statistically indistinguishable from noise.

Used by the agent in auto-commit mode to refuse keeps where the apparent
improvement is within the noise floor.

Does NOT require running claude -p again — it only re-runs the cheap
mechanical scorer on resampled subsets of existing response files. Cost: 0.

Usage:
  python3 variance.py \\
      --benchmark ~/.claude/nightly/benchmark.jsonl \\
      --run-dir   ~/.claude/nightly/experiments/<run_id>/responses \\
      --n-samples 20 \\
      --subsample-frac 0.7

Writes ~/.claude/nightly/experiments/<run_id>/variance.json with mean,
stdev, min, max, and the per-sample scores. The agent reads `stdev` and
compares Δ to 1.5×stdev as the significance threshold.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=Path, default=NIGHTLY / "benchmark.jsonl")
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="Directory of <benchmark_id>.json response files")
    ap.add_argument("--n-samples", type=int, default=20,
                    help="Number of bootstrap subsamples to score")
    ap.add_argument("--subsample-frac", type=float, default=0.7,
                    help="Fraction of replayable tasks to include in each subsample")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scorer", type=Path, default=NIGHTLY / "scorer.py")
    args = ap.parse_args()

    if not args.benchmark.exists():
        print(f"benchmark missing: {args.benchmark}", file=sys.stderr)
        return 2
    if not args.run_dir.exists():
        print(f"run-dir missing: {args.run_dir}", file=sys.stderr)
        return 2
    if not args.scorer.exists():
        print(f"scorer missing: {args.scorer}", file=sys.stderr)
        return 2

    benchmark = load_jsonl(args.benchmark)
    replayable = [e for e in benchmark if e.get("replayable")]
    n_replayable = len(replayable)
    if n_replayable < 5:
        print(f"only {n_replayable} replayable benchmark entries — variance estimate would be meaningless. Need ≥5.", file=sys.stderr)
        return 2

    subsample_size = max(3, int(n_replayable * args.subsample_frac))
    rng = random.Random(args.seed)

    scores: list[float] = []
    component_means_per_sample: list[dict] = []

    for i in range(args.n_samples):
        # Pick subsample_size benchmark entries
        sub = rng.sample(replayable, subsample_size)
        # Write a tmp benchmark.jsonl with only the subsample
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tf:
            for e in sub:
                tf.write(json.dumps(e) + "\n")
            tmp_bench = tf.name
        # Score against the run-dir (only response files matching the subsample's
        # benchmark_ids will be picked up)
        try:
            proc = subprocess.run(
                [sys.executable, str(args.scorer),
                 "--benchmark", tmp_bench,
                 "--run-dir", str(args.run_dir)],
                capture_output=True, text=True,
            )
            if proc.returncode != 0:
                continue
            score = json.loads(proc.stdout)
            mean = score.get("score_mean")
            if isinstance(mean, (int, float)):
                scores.append(mean)
                component_means_per_sample.append(score.get("component_means") or {})
        except Exception:
            continue
        finally:
            Path(tmp_bench).unlink(missing_ok=True)

    if len(scores) < 3:
        print(f"only {len(scores)} valid subsample scores — can't compute variance reliably", file=sys.stderr)
        return 2

    result = {
        "n_samples_requested": args.n_samples,
        "n_samples_valid": len(scores),
        "subsample_size": subsample_size,
        "n_replayable_total": n_replayable,
        "mean": round(statistics.mean(scores), 4),
        "median": round(statistics.median(scores), 4),
        "stdev": round(statistics.stdev(scores), 4) if len(scores) > 1 else None,
        "min": round(min(scores), 4),
        "max": round(max(scores), 4),
        "scores": [round(s, 4) for s in scores],
        # 1.5σ is a rough "Δ-against-noise" threshold. Bigger than this is
        # plausibly real; smaller is noise.
        "noise_threshold_1_5_sigma": (
            round(statistics.stdev(scores) * 1.5, 4) if len(scores) > 1 else None
        ),
    }

    out = args.run_dir.parent / "variance.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")

    print(f"variance: n={result['n_samples_valid']} mean={result['mean']} "
          f"stdev={result['stdev']} range=[{result['min']}..{result['max']}]")
    print(f"  noise threshold (1.5σ): {result['noise_threshold_1_5_sigma']}")
    print(f"  → keep decision needs Δ > {result['noise_threshold_1_5_sigma']} "
          f"to be plausibly real (not just sampling luck)")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
