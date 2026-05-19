#!/usr/bin/env python3
"""
NIGHTLY — Real benchmark replay.

The piece that turns dry-run into a real loop. Takes a benchmark.jsonl,
invokes `claude -p --model haiku --output-format json` per prompt, parses
the result, and writes one response file per benchmark_id that scorer.py
can consume.

Costs Haiku tokens — capped per-task via --max-budget-usd. Total cap
enforced by the calling agent's $3-per-run budget.

Usage (typical, from the agent's workflow):

  python3 replay.py \
      --benchmark ~/.claude/nightly/benchmark.jsonl \
      --run-dir   ~/.claude/nightly/experiments/<run_id>/responses \
      --model     haiku \
      --max-tasks 10 \
      --max-budget-per-task 0.30

Writes one ~/.claude/nightly/experiments/<run_id>/responses/<benchmark_id>.json
per replayed prompt, in the exact shape scorer.py expects:

  {
    "benchmark_id": "...",
    "duration_sec": 0.0,
    "output_tokens": 0,
    "response_text": "...",
    "tools": {"Read": 4, "Bash": 1},
    "files_changed": [],
    "tool_call_sequence": ["Read","Read","Bash"],
    "completed_cleanly": true,
    "correction_hook_fired": false
  }

Also writes ~/.claude/nightly/experiments/<run_id>/replay-summary.json with
per-task timing + cost so the agent can apply the budget cap.
"""

from __future__ import annotations

import argparse
import json
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
import time
from collections import Counter
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"


def load_benchmark(path: Path) -> list[dict]:
    out = []
    if not path.exists():
        return out
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


def parse_claude_json(stdout: str) -> dict:
    """Parse claude -p --output-format json output. Best-effort: claude may
    emit a single JSON object with `result`/`messages`/`usage` keys, or a
    different shape across versions. We extract:
      - response_text (final assistant text)
      - output_tokens (usage)
      - tools used (counter) + tool_call_sequence (order)
      - completed_cleanly (bool — did we hit a coherent stop_reason)
    Falls back to {} on unparseable output."""
    try:
        o = json.loads(stdout)
    except Exception:
        return {"response_text": stdout[:2000], "_parse_error": True}

    response_text = ""
    tools: Counter = Counter()
    seq: list[str] = []
    output_tokens = 0
    completed = False

    # Shape A: {"type":"result","subtype":"success","result":"…text…","usage":{…},"total_cost_usd":…}
    if isinstance(o, dict):
        if isinstance(o.get("result"), str):
            response_text = o["result"]
        usage = o.get("usage") or {}
        output_tokens = int(usage.get("output_tokens") or 0)
        completed = o.get("subtype") == "success" or o.get("is_error") is False

        # Shape B: includes messages array with full assistant turns
        msgs = o.get("messages") or o.get("transcript") or []
        if isinstance(msgs, list):
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                content = m.get("content")
                if isinstance(content, list):
                    for part in content:
                        if not isinstance(part, dict):
                            continue
                        t = part.get("type")
                        if t == "tool_use":
                            name = part.get("name") or "unknown"
                            tools[name] += 1
                            seq.append(name)
                        elif t == "text" and m.get("role") == "assistant":
                            txt = part.get("text") or ""
                            if txt:
                                response_text = txt  # last wins

    return {
        "response_text": response_text,
        "output_tokens": output_tokens,
        "tools": dict(tools),
        "tool_call_sequence": seq,
        "completed_cleanly": completed,
    }


def replay_one(prompt: str, model: str, max_budget: float, max_turns: int,
               timeout_sec: int) -> tuple[dict, float, float]:
    """Returns (parsed_response, duration_sec, cost_usd_estimate)."""
    start = time.monotonic()
    # --bare: skip hooks, LSP, plugin sync, attribution, auto-memory, background
    # prefetches, keychain reads, and CLAUDE.md auto-discovery. Critical here for
    # two reasons: (1) without it, replaying recursively loads this plugin's
    # SessionStart hook, slowing every replay and potentially printing the
    # NIGHTLY surface banner into the response text; (2) replay is supposed to
    # measure the *substrate change* in isolation — auto-memory + auto-CLAUDE.md
    # would confound that by re-pulling fresh context Claude would normally have.
    cmd = [
        "claude", "-p", "--bare",
        "--model", model,
        "--output-format", "json",
        "--max-turns", str(max_turns),
        "--max-budget-usd", f"{max_budget:.2f}",
        prompt,
    ]
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
        )
    except subprocess.TimeoutExpired:
        duration = time.monotonic() - start
        return ({"response_text": "(replay timeout)", "completed_cleanly": False,
                 "tools": {}, "tool_call_sequence": [], "output_tokens": 0,
                 "_timeout": True},
                duration, 0.0)

    duration = time.monotonic() - start
    parsed = parse_claude_json(proc.stdout)

    # Extract cost from the JSON if claude reported it
    cost = 0.0
    try:
        o = json.loads(proc.stdout)
        cost = float(o.get("total_cost_usd") or 0.0)
    except Exception:
        pass

    if proc.returncode != 0:
        parsed.setdefault("_returncode", proc.returncode)
        parsed.setdefault("_stderr", proc.stderr[:1000])
        parsed["completed_cleanly"] = False

    return parsed, duration, cost


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=Path, default=NIGHTLY / "benchmark.jsonl")
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="Where to write per-task response files")
    ap.add_argument("--model", default="haiku",
                    help="claude --model value (haiku/sonnet)")
    ap.add_argument("--max-tasks", type=int, default=10,
                    help="Replay at most N replayable tasks; randomized if benchmark is larger")
    ap.add_argument("--max-budget-per-task", type=float, default=0.30,
                    help="Per-task USD cap passed to claude --max-budget-usd")
    ap.add_argument("--total-budget", type=float, default=2.00,
                    help="Stop early if cumulative cost exceeds this")
    ap.add_argument("--max-turns", type=int, default=12)
    ap.add_argument("--timeout-sec", type=int, default=180,
                    help="Per-task wall-clock timeout")
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    if not args.benchmark.exists():
        print(f"benchmark missing: {args.benchmark}", file=sys.stderr)
        return 2
    args.run_dir.mkdir(parents=True, exist_ok=True)

    bench = [e for e in load_benchmark(args.benchmark) if e.get("replayable")]
    if not bench:
        print("no replayable benchmark entries — nothing to replay", file=sys.stderr)
        return 0

    # Deterministic subsample
    import random
    rng = random.Random(args.seed)
    if len(bench) > args.max_tasks:
        bench = rng.sample(bench, args.max_tasks)

    summary = {
        "n_attempted": 0,
        "n_completed": 0,
        "n_timeout": 0,
        "n_failed": 0,
        "total_cost_usd": 0.0,
        "model": args.model,
        "per_task": [],
        "stopped_early": False,
    }

    for entry in bench:
        if summary["total_cost_usd"] >= args.total_budget:
            summary["stopped_early"] = True
            break
        summary["n_attempted"] += 1
        bid = entry["benchmark_id"]
        prompt = entry["prompt"]
        parsed, duration, cost = replay_one(
            prompt, args.model, args.max_budget_per_task,
            args.max_turns, args.timeout_sec,
        )
        if parsed.get("_timeout"):
            summary["n_timeout"] += 1
        elif parsed.get("completed_cleanly"):
            summary["n_completed"] += 1
        else:
            summary["n_failed"] += 1

        # Write the response file in scorer.py's expected shape
        response = {
            "benchmark_id": bid,
            "duration_sec": round(duration, 2),
            "output_tokens": parsed.get("output_tokens", 0),
            "response_text": parsed.get("response_text", ""),
            "tools": parsed.get("tools", {}),
            "files_changed": [],
            "tool_call_sequence": parsed.get("tool_call_sequence", []),
            "completed_cleanly": bool(parsed.get("completed_cleanly", False)),
            "correction_hook_fired": False,  # would require hook telemetry capture
        }
        if parsed.get("_returncode") is not None:
            response["_replay_returncode"] = parsed["_returncode"]
        (args.run_dir / f"{bid}.json").write_text(json.dumps(response, indent=2), encoding="utf-8")

        summary["per_task"].append({
            "benchmark_id": bid,
            "duration_sec": round(duration, 2),
            "cost_usd": round(cost, 4),
            "completed": bool(parsed.get("completed_cleanly", False)),
            "timeout": bool(parsed.get("_timeout", False)),
        })
        summary["total_cost_usd"] = round(summary["total_cost_usd"] + cost, 4)

    summary["total_cost_usd"] = round(summary["total_cost_usd"], 4)
    summary_path = args.run_dir.parent / "replay-summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    print(f"replay: attempted={summary['n_attempted']} "
          f"completed={summary['n_completed']} "
          f"timeout={summary['n_timeout']} "
          f"failed={summary['n_failed']} "
          f"cost=${summary['total_cost_usd']:.2f}"
          f"{' (stopped-early on budget)' if summary['stopped_early'] else ''}")
    print(f"summary: {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
