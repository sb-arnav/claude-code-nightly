#!/usr/bin/env python3
"""
NIGHTLY — Mechanical Scorer (v0.1).

Reads:
  - a benchmark.jsonl (from benchmark.py)
  - a run directory containing one response file per benchmark_id

Emits:
  - JSON to stdout: aggregate score + per-task scores + per-signal breakdown

No LLM-as-judge in v0.1 — all signals deterministic. Lets us run the loop for
real without burning tokens on judge calls before the mechanical baseline is
stable.

Response file format (one JSON file per benchmark_id at <run-dir>/<benchmark_id>.json):
{
  "benchmark_id": "abc12345-000",
  "duration_sec": 123.4,
  "output_tokens": 8210,
  "response_text": "...",
  "tools": {"Read": 4, "Bash": 1, ...},
  "files_changed": ["..."],
  "tool_call_sequence": ["Read", "Read", "Bash", "Edit", ...],
  "completed_cleanly": true,
  "correction_hook_fired": false
}

The agent loop in /nightly produces these. The scorer is decoupled from how
they're produced — could be a real claude run, a mock, or a regression replay.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys

# Force UTF-8 stdio on Windows where Python defaults to cp1252; without this,
# print() of any Unicode (em-dash, arrows, smart quotes — i.e. most Claude
# output) crashes with UnicodeEncodeError. Idempotent and safe on all platforms.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"

# Phrases that empirically correlate with premature-closure or hedging behavior
# users have corrected against. Maintained as a flat list so it's transparent
# and easy to extend per-user.
PREMATURE_PHRASES = [
    "feels balanced",
    "deferred until",
    "i think the right call",
    "either approach works",
    "ultimately your call",
    "let me know if",  # closes too soon when used as the entire final line
    "to summarize, ",
    "in summary, ",
    "tldr:",
    "tl;dr:",
]

# Patterns that indicate the response offered multiple-choice options instead of
# taking a position. Default heuristic for the "take a position, don't hedge"
# anti-pattern; users can edit per their own correction preferences.
OPTIONS_PATTERNS = [
    re.compile(r"\b(option|approach|path|choice)\s*[abc1234]\b", re.I),
    re.compile(r"\b(?:there are|here are)\s+(?:two|three|four|2|3|4)\s+(?:options|approaches|paths|ways)\b", re.I),
    re.compile(r"\bwould you (?:prefer|like|want)\b.*\bor\b", re.I),
]

# "Search-first" indicators in the tool call sequence for design-shaped prompts.
SEARCH_TOOLS = {"WebSearch", "WebFetch", "mcp__claude_ai_Tavily__tavily_search", "Tavily"}
GH_SEARCH_RE = re.compile(r"\bgh\s+search\b")

# Design-shaped prompts that *should* have triggered a search-first.
DESIGN_PROMPT_RE = re.compile(
    r"\b(design|architect|how (?:would|should) (?:we|you)|better idea|what should we build|brainstorm|plan a)\b",
    re.I,
)

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def load_jsonl(path: Path) -> list[dict]:
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


def count_premature_phrases(text: str) -> int:
    lower = text.lower()
    return sum(lower.count(p) for p in PREMATURE_PHRASES)


def count_options_framing(text: str) -> int:
    return sum(1 for pat in OPTIONS_PATTERNS if pat.search(text))


def search_first_for_design(benchmark: dict, response: dict) -> tuple[bool, bool]:
    """Returns (was_design_prompt, search_fired_first)."""
    prompt = benchmark["prompt"]
    is_design = bool(DESIGN_PROMPT_RE.search(prompt))
    if not is_design:
        return False, True  # vacuously passes
    seq = response.get("tool_call_sequence") or []
    # The first non-Read tool in the sequence should be a search.
    for name in seq:
        if name in {"Read", "Glob", "Grep"}:
            continue
        if name in SEARCH_TOOLS:
            return True, True
        # Bash-with-gh-search counts too — check bash args separately if recorded.
        if name == "Bash":
            # Conservative: assume Bash is search-first if response text mentions gh search.
            if GH_SEARCH_RE.search(response.get("response_text") or ""):
                return True, True
            return True, False
        # Any other action before search = failed search-first
        return True, False
    # No tools at all = no search fired
    return True, False


def cost_ratio(response: dict, ground_truth: dict) -> float:
    """Token efficiency: ratio of run tokens to ground-truth tokens.

    <1.0 = cheaper than original, >1.0 = more expensive. We score it as
    `clamp(2 - ratio, 0, 1)` so 0.5x cost → 1.5 clamped to 1.0, 2x cost → 0.0.
    """
    gt = max(1, ground_truth.get("output_tokens") or 1)
    run = max(0, response.get("output_tokens") or 0)
    if run == 0:
        return 0.0
    ratio = run / gt
    return max(0.0, min(1.0, 2.0 - ratio))


def score_task(benchmark: dict, response: dict) -> dict:
    gt = benchmark["ground_truth"]
    text = response.get("response_text") or ""
    duration = float(response.get("duration_sec") or 0.0)
    tools = response.get("tools") or {}
    tools_total = sum(tools.values())

    completed = bool(response.get("completed_cleanly", False))
    correction_fired = bool(response.get("correction_hook_fired", False))
    premature = count_premature_phrases(text)
    options = count_options_framing(text)
    was_design, search_first = search_first_for_design(benchmark, response)

    # Tool-count alignment: did the run use a sensible number of tools versus
    # the ground truth? Both 0 tools when GT had many = bad. Massive overshoot
    # also bad. Convert to a [0,1] alignment score.
    gt_tools = max(1, gt.get("tool_calls") or 1)
    if tools_total == 0:
        tool_alignment = 0.0
    else:
        ratio = tools_total / gt_tools
        # 1.0 alignment if within 0.5x-2.0x; linear falloff otherwise.
        if 0.5 <= ratio <= 2.0:
            tool_alignment = 1.0
        elif ratio < 0.5:
            tool_alignment = ratio / 0.5
        else:
            tool_alignment = max(0.0, 1.0 - (ratio - 2.0) / 4.0)

    # Cost is logged as a diagnostic only — not scored. Optimizing for "cheaper"
    # creates a Goodhart trap when replay model (Haiku) differs from ground-truth
    # model (Sonnet+full context). Quality-equivalent isn't measurable here yet.
    cost = cost_ratio(response, gt)

    # Composite score. Weights chosen so anti-pattern signals (correction
    # firing, premature closure, options framing) materially dominate — those
    # are behaviors users have explicitly corrected against. Tool alignment is
    # the only tiebreaker; cost is diagnostic-only.
    completion_pts = 1.0 if completed else 0.0
    no_correction_pts = 0.0 if correction_fired else 1.0
    no_premature_pts = max(0.0, 1.0 - premature * 0.5)
    no_options_pts = 1.0 if options == 0 else 0.0
    search_first_pts = 1.0 if (not was_design or search_first) else 0.0

    weights = {
        "completion": 1.5,
        "no_correction": 2.0,
        "no_premature": 1.5,
        "no_options": 1.5,
        "search_first": 1.0,
        "tool_alignment": 1.0,
    }
    components = {
        "completion": completion_pts,
        "no_correction": no_correction_pts,
        "no_premature": no_premature_pts,
        "no_options": no_options_pts,
        "search_first": search_first_pts,
        "tool_alignment": tool_alignment,
    }
    diagnostics = {"cost": cost}
    total_weight = sum(weights.values())
    score = sum(weights[k] * components[k] for k in weights) / total_weight

    return {
        "benchmark_id": benchmark["benchmark_id"],
        "score": round(score, 4),
        "components": {k: round(v, 4) for k, v in components.items()},
        "diagnostics": {k: round(v, 4) for k, v in diagnostics.items()},
        "raw": {
            "premature_phrases": premature,
            "options_patterns": options,
            "was_design_prompt": was_design,
            "search_first": search_first,
            "tools_total": tools_total,
            "gt_tools_total": gt.get("tool_calls"),
            "duration_sec": duration,
            "output_tokens": response.get("output_tokens"),
            "gt_output_tokens": gt.get("output_tokens"),
        },
    }


def load_response(run_dir: Path, benchmark_id: str) -> dict | None:
    p = run_dir / f"{benchmark_id}.json"
    if not p.exists():
        return None
    try:
        with p.open() as fh:
            return json.load(fh)
    except Exception:
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=Path, default=NIGHTLY / "benchmark.jsonl")
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="Directory containing <benchmark_id>.json response files")
    ap.add_argument("--out", type=Path, default=None,
                    help="Optional output path; defaults to stdout")
    args = ap.parse_args()

    if not args.benchmark.exists():
        print(f"benchmark missing: {args.benchmark}", file=sys.stderr)
        return 2
    if not args.run_dir.exists():
        print(f"run-dir missing: {args.run_dir}", file=sys.stderr)
        return 2

    benchmark = load_jsonl(args.benchmark)
    per_task: list[dict] = []
    missing: list[str] = []
    for entry in benchmark:
        if not entry.get("replayable"):
            continue
        resp = load_response(args.run_dir, entry["benchmark_id"])
        if resp is None:
            missing.append(entry["benchmark_id"])
            continue
        per_task.append(score_task(entry, resp))

    scores = [t["score"] for t in per_task]
    aggregate = {
        "n": len(per_task),
        "missing_responses": missing,
        "score_mean": round(statistics.mean(scores), 4) if scores else None,
        "score_median": round(statistics.median(scores), 4) if scores else None,
        "score_stdev": round(statistics.stdev(scores), 4) if len(scores) > 1 else None,
        "component_means": {
            k: round(statistics.mean([t["components"][k] for t in per_task]), 4)
            for k in ("completion","no_correction","no_premature","no_options","search_first","tool_alignment")
        } if per_task else {},
        "diagnostic_means": {
            k: round(statistics.mean([t["diagnostics"][k] for t in per_task]), 4)
            for k in ("cost",)
        } if per_task else {},
        "per_task": per_task,
    }
    blob = json.dumps(aggregate, indent=2)
    if args.out:
        args.out.write_text(blob, encoding="utf-8")
    else:
        print(blob)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
