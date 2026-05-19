#!/usr/bin/env python3
"""
NIGHTLY — Mechanical keep/revert decision.

Closes the "computed but not gated" critique. Before this script, the agent
read score.json + judge-scores.json + variance.json and was told in prose
to apply the gates. Prose isn't enforcement; the agent could miss a gate
or interpret a borderline number generously. This script returns the
decision as data — the agent just executes whatever decide.py says.

Gates (in auto-commit mode; observation mode skips this and always returns
`proposed-*`):

  1. Δ ≥ +0.02              (original mechanical floor)
  2. Δ > variance_threshold (statistical significance — variance.py)
  3. judge_composite ≥ 0.6  (semantic quality — judge.py)
  4. n_judge_failed < 2     (judge ran and produced usable output)

If all four pass → "kept". Any failure → distinct decision label so the
audit trail shows which gate killed the run.

Usage:
  python3 decide.py --run-dir ~/.claude/nightly/experiments/<run_id>

  # Override the gates (e.g. when judge or variance is missing):
  python3 decide.py --run-dir ... --skip-judge --skip-variance

Output: a single JSON object to stdout (also written to <run-dir>/decision.json).

  {
    "decision": "kept" | "noise-rejected" | "delta-below-floor"
              | "judge-rejected" | "judge-missing"
              | "proposed-kept" | "proposed-reverted" | "first-real-baseline",
    "delta": 0.034,
    "score_mean": 0.912,
    "baseline_score": 0.878,
    "variance_threshold": 0.0098,
    "judge_composite": 0.74,
    "n_judge_failed": 0,
    "gates_passed": ["delta-floor","variance","judge","judge-completeness"],
    "gates_failed": [],
    "reason": "all four gates passed; auto-commit kept",
    "mode": "auto-commit" | "observation"
  }
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"

DELTA_FLOOR = 0.02
JUDGE_FLOOR = 0.6
MAX_JUDGE_FAILED = 2  # exclusive: < 2 (so 0 or 1 is fine)


def load_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def find_baseline(exp_log: Path) -> dict | None:
    """Latest entry with decision in ('kept','first-real-baseline')."""
    if not exp_log.exists():
        return None
    last_kept = None
    with exp_log.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            if o.get("decision") in ("kept", "first-real-baseline"):
                last_kept = o
    return last_kept


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="Path to ~/.claude/nightly/experiments/<run_id>/ "
                         "(NOT the responses/ subdir — the parent)")
    ap.add_argument("--mode", choices=["auto-commit", "observation"], default=None,
                    help="Override mode detection. Default: auto-detect via auto-commit.yes marker.")
    ap.add_argument("--skip-judge", action="store_true",
                    help="Don't require judge gate (e.g. when judge wasn't run)")
    ap.add_argument("--skip-variance", action="store_true",
                    help="Don't require variance gate (e.g. when variance wasn't run)")
    ap.add_argument("--exp-log", type=Path, default=NIGHTLY / "experiment-log.jsonl",
                    help="Path to experiment-log.jsonl (for baseline lookup)")
    args = ap.parse_args()

    if not args.run_dir.exists():
        print(json.dumps({"decision": "error", "reason": f"run-dir missing: {args.run_dir}"}), file=sys.stderr)
        return 2

    # Mode detection
    mode = args.mode
    if mode is None:
        mode = "auto-commit" if (NIGHTLY / "auto-commit.yes").exists() else "observation"

    score = load_json(args.run_dir / "score.json")
    judge = load_json(args.run_dir / "judge-scores.json")
    variance = load_json(args.run_dir / "variance.json")

    if score is None:
        out = {"decision": "error", "reason": "score.json missing", "mode": mode}
        print(json.dumps(out))
        return 2

    score_mean = score.get("score_mean")
    if not isinstance(score_mean, (int, float)):
        out = {"decision": "error", "reason": "score.json has no score_mean", "mode": mode}
        print(json.dumps(out))
        return 2

    baseline = find_baseline(args.exp_log)
    if baseline is None:
        # First real run — always keep (gated only on sanity floor 0.5)
        if score_mean < 0.5:
            out = {
                "decision": "sanity-floor-rejected",
                "score_mean": score_mean,
                "baseline_score": None,
                "delta": None,
                "reason": f"score_mean {score_mean} below sanity floor 0.5; loop is broken, not the substrate",
                "mode": mode,
            }
        else:
            out = {
                "decision": "first-real-baseline" if mode == "auto-commit" else "proposed-first-real-baseline",
                "score_mean": score_mean,
                "baseline_score": None,
                "delta": None,
                "reason": "no prior baseline; this run establishes it",
                "mode": mode,
            }
        (args.run_dir / "decision.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
        print(json.dumps(out))
        return 0

    baseline_score = baseline.get("score_mean")
    delta = score_mean - baseline_score if isinstance(baseline_score, (int, float)) else None

    gates_passed: list[str] = []
    gates_failed: list[str] = []
    judge_composite = (judge or {}).get("judge_composite") if judge else None
    n_judge_failed = (judge or {}).get("n_failed", 0) if judge else 0
    variance_threshold = (variance or {}).get("noise_threshold_1_5_sigma") if variance else None

    # Gate 1: Δ ≥ +0.02
    if delta is not None and delta >= DELTA_FLOOR:
        gates_passed.append("delta-floor")
    else:
        gates_failed.append("delta-floor")

    # Gate 2: Δ > variance_threshold (statistical significance)
    if args.skip_variance:
        pass  # gate skipped
    elif variance_threshold is None:
        gates_failed.append("variance-missing")
    elif delta is not None and delta > variance_threshold:
        gates_passed.append("variance")
    else:
        gates_failed.append("variance")

    # Gate 3: judge_composite ≥ 0.6
    if args.skip_judge:
        pass
    elif judge_composite is None:
        gates_failed.append("judge-missing")
    elif judge_composite >= JUDGE_FLOOR:
        gates_passed.append("judge")
    else:
        gates_failed.append("judge")

    # Gate 4: not too many judge failures
    if args.skip_judge:
        pass
    elif n_judge_failed >= MAX_JUDGE_FAILED:
        gates_failed.append("judge-completeness")
    else:
        gates_passed.append("judge-completeness")

    # In observation mode, never auto-keep — surface what auto-commit would have done
    if mode == "observation":
        would_keep = (len(gates_failed) == 0)
        decision = "proposed-kept" if would_keep else "proposed-reverted"
        reason = ("would have kept under auto-commit; proposal awaits review"
                  if would_keep
                  else f"would have reverted under auto-commit: failed {gates_failed}")
    else:
        # Auto-commit mode
        if len(gates_failed) == 0:
            decision = "kept"
            reason = f"all gates passed: Δ={delta:+.4f}"
        elif gates_failed == ["delta-floor"] and delta is not None and delta <= -DELTA_FLOOR:
            decision = "reverted"
            reason = f"Δ={delta:+.4f} below revert threshold -{DELTA_FLOOR}"
        elif gates_failed == ["delta-floor"]:
            decision = "delta-below-floor"
            reason = f"Δ={delta:+.4f} within ±{DELTA_FLOOR} (marginal, held)"
        elif "variance" in gates_failed:
            decision = "noise-rejected"
            reason = f"Δ={delta:+.4f} but noise threshold is {variance_threshold:.4f} — within sampling noise"
        elif "judge" in gates_failed or "judge-completeness" in gates_failed:
            decision = "judge-rejected"
            reason = f"judge_composite={judge_composite} failed (floor {JUDGE_FLOOR}) or n_judge_failed={n_judge_failed} >= {MAX_JUDGE_FAILED}"
        elif "judge-missing" in gates_failed or "variance-missing" in gates_failed:
            decision = "gates-missing"
            reason = f"required signals missing: {[g for g in gates_failed if g.endswith('-missing')]}"
        else:
            decision = "gates-failed"
            reason = f"failed gates: {gates_failed}"

    out = {
        "decision": decision,
        "score_mean": round(score_mean, 4),
        "baseline_score": round(baseline_score, 4) if isinstance(baseline_score, (int, float)) else None,
        "delta": round(delta, 4) if delta is not None else None,
        "variance_threshold": round(variance_threshold, 4) if isinstance(variance_threshold, (int, float)) else None,
        "judge_composite": round(judge_composite, 4) if isinstance(judge_composite, (int, float)) else None,
        "n_judge_failed": n_judge_failed,
        "gates_passed": gates_passed,
        "gates_failed": gates_failed,
        "reason": reason,
        "mode": mode,
    }
    (args.run_dir / "decision.json").write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
