#!/usr/bin/env python3
"""
NIGHTLY — LLM-as-judge scoring (v0.6+).

The load-bearing methodology fix the external review named: regex heuristics
are gameable, so add a model-judged dimension that reads the actual response
and rates it on rubric criteria the regexes can't reliably detect.

How it works:
  - Takes a benchmark entry + a per-task response file (from replay.py).
  - Builds a structured rubric prompt: "Given this user prompt and this
    response, rate the response 1-5 on each of: takes a position, completes
    the task, search-first for design prompts, tool selection appropriateness,
    response specificity."
  - Calls `claude -p --model haiku --output-format json` with the rubric
    (same auth path as replay.py — no API key needed).
  - Parses the JSON judgment, returns per-dimension scores.

Cost: ~$0.005-0.02 per judged task on Haiku. Default sample = 5 tasks
per run, so ~$0.05/run added to the existing ~$1-2 replay cost.

Why this addresses the critique:
  - Regex `no_premature` catches "feels balanced" but misses subtle hedging.
    Judge reads the actual text and rates "did this take a position?" on a
    1-5 scale.
  - Regex `no_options` catches "Option A/B/C" but a CLAUDE.md edit that just
    forbids that phrase would score great without improving reasoning.
    Judge rates "did this offer multiple choices instead of recommending one?"
  - Goodhart-resistance: the judge prompt is fixed across runs and doesn't
    reveal what the substrate change was, so the optimizer can't game it by
    targeting the judge's blind spots.

NOT a full fix for the methodology gap. Judges have their own failure modes
(sycophancy, length bias, charity-grading). Use this as ONE more signal
combined with mechanical scoring, not as the single source of truth.

Usage:
  python3 judge.py \\
      --benchmark ~/.claude/nightly/benchmark.jsonl \\
      --run-dir   ~/.claude/nightly/experiments/<run_id>/responses \\
      --sample    5 \\
      --model     haiku \\
      --max-budget 0.50

Writes ~/.claude/nightly/experiments/<run_id>/judge-scores.json with
per-task judgments and an aggregate.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
import subprocess
import sys
import textwrap
import time
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"

RUBRIC_DIMENSIONS = [
    "position_taking",     # Did the response take a clear position vs hedge / present options?
    "task_completion",     # Did it finish the task vs abandon / loop / give up?
    "search_first",        # For design prompts: did it scan prior art before drafting?
    "tool_appropriate",    # Were the tool choices sensible for the task?
    "response_specific",   # Was the response specific to the request vs generic-AI?
]

JUDGE_PROMPT_TEMPLATE = """\
You are evaluating one task an AI coding agent completed. Rate the response on five rubric dimensions, 1-5.

Return ONLY a JSON object on a single line, no prose, no markdown fence. Schema:
{{"position_taking": <1-5>, "task_completion": <1-5>, "search_first": <1-5>, "tool_appropriate": <1-5>, "response_specific": <1-5>, "rationale": "<one sentence>"}}

Rubric (each 1-5):

1. position_taking — Did the agent take a clear position?
   5 = stated a single recommendation with reasoning
   3 = leaned one way but hedged
   1 = presented options A/B/C without recommending; "either works"

2. task_completion — Did the agent finish the task?
   5 = reached a coherent stopping point with the work done
   3 = made meaningful progress but didn't finish
   1 = abandoned / looped / asked questions instead of trying

3. search_first — For design / "how should I build X" prompts: did the agent scan prior art first?
   5 = ran a web/repo search before drafting
   3 = referenced its own knowledge of prior art without searching
   1 = jumped straight to design without context-gathering
   N/A (return 3) — if this wasn't a design-shape prompt

4. tool_appropriate — Were the tool choices sensible?
   5 = each tool call had clear purpose for the task
   3 = mostly appropriate with one or two redundant calls
   1 = wrong tools or many redundant calls (e.g. Read-Read-Read with no Edit)

5. response_specific — Was the response specific to THIS user's prompt?
   5 = answer addresses the specific ask, no boilerplate
   3 = mostly specific with some generic framing
   1 = generic-AI response, could have been written without reading the prompt

USER PROMPT:
\"\"\"
{prompt}
\"\"\"

AGENT RESPONSE:
\"\"\"
{response}
\"\"\"

AGENT TOOLS USED ({tools_count} total):
{tools_summary}

Return the JSON now:"""


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


def truncate(s: str, n: int) -> str:
    if not s:
        return ""
    return s if len(s) <= n else s[: n - 50] + "\n…[truncated]…\n" + s[-50:]


def build_judge_prompt(benchmark_entry: dict, response: dict) -> str:
    tools = response.get("tools", {}) or {}
    tools_summary = ", ".join(f"{k}×{v}" for k, v in sorted(tools.items()))
    if not tools_summary:
        tools_summary = "(no tool calls)"
    return JUDGE_PROMPT_TEMPLATE.format(
        prompt=truncate(benchmark_entry.get("prompt", ""), 1500),
        response=truncate(response.get("response_text", ""), 2000),
        tools_count=sum(tools.values()),
        tools_summary=tools_summary,
    )


def call_judge(prompt: str, model: str, max_budget: float, timeout_sec: int) -> tuple[dict | None, float]:
    """Call claude -p to score one (prompt, response) pair. Returns (parsed_scores_or_None, cost_usd)."""
    cmd = [
        "claude", "-p",
        "--model", model,
        "--output-format", "json",
        "--max-budget-usd", f"{max_budget:.2f}",
        "--max-turns", "1",  # judge is a single-turn response
        prompt,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_sec)
    except subprocess.TimeoutExpired:
        return None, 0.0
    if proc.returncode != 0:
        return None, 0.0
    try:
        outer = json.loads(proc.stdout)
    except Exception:
        return None, 0.0
    cost = float(outer.get("total_cost_usd") or 0.0)
    result_text = outer.get("result") or ""
    # Judge should have emitted a single-line JSON; extract it
    inner = None
    for line in result_text.splitlines():
        line = line.strip()
        if line.startswith("{") and line.endswith("}"):
            try:
                inner = json.loads(line)
                break
            except Exception:
                continue
    if inner is None:
        # Fallback: try to extract by regex
        import re
        m = re.search(r"\{[^{}]*position_taking[^{}]*\}", result_text)
        if m:
            try:
                inner = json.loads(m.group(0))
            except Exception:
                pass
    return inner, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=Path, default=NIGHTLY / "benchmark.jsonl")
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="Directory of <benchmark_id>.json response files from replay.py")
    ap.add_argument("--sample", type=int, default=5,
                    help="Number of tasks to judge (random subsample)")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--model", default="haiku")
    ap.add_argument("--max-budget-per-judge", type=float, default=0.10)
    ap.add_argument("--total-budget", type=float, default=0.50,
                    help="Stop judging when cumulative spend hits this")
    ap.add_argument("--timeout-sec", type=int, default=60)
    args = ap.parse_args()

    if not args.benchmark.exists():
        print(f"benchmark missing: {args.benchmark}", file=sys.stderr)
        return 2
    if not args.run_dir.exists():
        print(f"run-dir missing: {args.run_dir}", file=sys.stderr)
        return 2

    bench = {e["benchmark_id"]: e for e in load_jsonl(args.benchmark) if e.get("replayable")}
    response_files = list(args.run_dir.glob("*.json"))
    if not response_files:
        print(f"no response files in {args.run_dir}; run replay.py first", file=sys.stderr)
        return 2

    rng = random.Random(args.seed)
    if len(response_files) > args.sample:
        response_files = rng.sample(response_files, args.sample)

    judgments: list[dict] = []
    total_cost = 0.0
    stopped_early = False

    for rf in response_files:
        if total_cost >= args.total_budget:
            stopped_early = True
            break
        bid = rf.stem
        if bid not in bench:
            continue
        try:
            response = json.loads(rf.read_text())
        except Exception:
            continue
        prompt = build_judge_prompt(bench[bid], response)
        scores, cost = call_judge(prompt, args.model, args.max_budget_per_judge, args.timeout_sec)
        total_cost += cost
        if scores is None:
            judgments.append({"benchmark_id": bid, "scores": None, "cost_usd": round(cost, 4), "judge_failed": True})
            continue
        # Normalize: drop unexpected keys, coerce numbers
        clean = {}
        for d in RUBRIC_DIMENSIONS:
            v = scores.get(d)
            try:
                clean[d] = max(1, min(5, int(round(float(v)))))
            except (TypeError, ValueError):
                clean[d] = None
        clean["rationale"] = scores.get("rationale") or ""
        judgments.append({
            "benchmark_id": bid,
            "scores": clean,
            "cost_usd": round(cost, 4),
        })

    # Aggregate: per-dimension means (ignoring None), normalize to 0-1
    per_dim_scores: dict[str, list[float]] = {d: [] for d in RUBRIC_DIMENSIONS}
    for j in judgments:
        if not j.get("scores"):
            continue
        for d in RUBRIC_DIMENSIONS:
            v = j["scores"].get(d)
            if isinstance(v, (int, float)):
                per_dim_scores[d].append((v - 1) / 4.0)  # 1-5 → 0-1

    aggregate = {
        "n_judged": sum(1 for j in judgments if j.get("scores")),
        "n_failed": sum(1 for j in judgments if j.get("judge_failed")),
        "total_cost_usd": round(total_cost, 4),
        "stopped_early": stopped_early,
        "model": args.model,
        "per_dimension_means": {
            d: round(statistics.mean(per_dim_scores[d]), 4) if per_dim_scores[d] else None
            for d in RUBRIC_DIMENSIONS
        },
        "judge_composite": (
            round(statistics.mean(
                v for vs in per_dim_scores.values() for v in vs
            ), 4)
            if any(per_dim_scores.values()) else None
        ),
        "judgments": judgments,
    }

    out = args.run_dir.parent / "judge-scores.json"
    out.write_text(json.dumps(aggregate, indent=2))
    print(f"judge: n={aggregate['n_judged']} failed={aggregate['n_failed']} "
          f"composite={aggregate['judge_composite']} cost=${total_cost:.3f}"
          f"{' (stopped-early)' if stopped_early else ''}")
    print(f"per-dimension means: {aggregate['per_dimension_means']}")
    print(f"wrote: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
