#!/usr/bin/env python3
"""
NIGHTLY — Correction-weighted ground-truth scoring.

The fifth and final critique gap the external reviewer named: "weight
corrections-injected ground-truth far above the regex heuristics."

`corrections.jsonl` records the user telling Claude "you did X, you should
have done Y". Each entry has:
  - prompt: the user prompt that triggered the correction
  - what_i_did: the failed behavior (label = negative)
  - supposed_to: the correct behavior (label = positive)

When a benchmark prompt matches a correction's prompt, that benchmark task
becomes a *labeled* eval — we know what the right behavior looks like, not
just that something was off. The response_text is scored on:
  +1 for keywords from `supposed_to` that appear
  -1 for keywords from `what_i_did` that appear
  → normalized to [0, 1]

This is high-fidelity signal that the regex heuristics can't approximate.
Matched tasks dominate the composite via 5x weighting.

Matching uses normalized prompt comparison — strips slash-command wrappers,
collapses whitespace, lowercases. Cheap-and-cheerful first pass; we can
upgrade to fuzzy match (Levenshtein) if exact-match has too few hits.

Usage:
  python3 corrections_score.py \\
      --benchmark   ~/.claude/nightly/benchmark.jsonl \\
      --corrections ~/.claude/corrections.jsonl \\
      --run-dir     ~/.claude/nightly/experiments/<run_id>/responses

Writes ~/.claude/nightly/experiments/<run_id>/corrections-score.json:
  {
    "n_matched": 3,
    "n_total_corrections": 12,
    "corrections_composite": 0.72,
    "per_matched_task": [
        {"benchmark_id": "abc-001", "correction_ts": "...",
         "matched_supposed_to": ["search first", "use gh search"],
         "matched_what_i_did":  [],
         "task_score": 0.85},
        ...
    ]
  }
"""

from __future__ import annotations

import sys

# Force UTF-8 stdio on Windows where Python defaults to cp1252; without this,
# print() of any Unicode (em-dash, arrows, smart quotes — i.e. most Claude
# output) crashes with UnicodeEncodeError. Idempotent and safe on all platforms.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

import argparse
import json
import re
import statistics
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
CLAUDE = Path.home() / ".claude"


def normalize_prompt(p: str) -> str:
    """Strip slash-command wrappers + collapse whitespace + lowercase."""
    if not p:
        return ""
    p = re.sub(r"<[^>]+>", " ", p)  # strip XML-ish tags
    p = re.sub(r"\s+", " ", p).strip().lower()
    return p


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


def extract_keywords(text: str, min_len: int = 4) -> list[str]:
    """Extract content-bearing words for keyword scoring. Drops stopwords +
    common filler. Returns deduplicated tokens."""
    STOPWORDS = {
        "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
        "be", "been", "being", "have", "has", "had", "do", "does", "did",
        "will", "would", "could", "should", "may", "might", "must", "can",
        "this", "that", "these", "those", "i", "you", "he", "she", "it",
        "we", "they", "my", "your", "his", "her", "its", "our", "their",
        "what", "which", "who", "whom", "where", "when", "why", "how",
        "to", "of", "in", "on", "at", "by", "for", "with", "about", "from",
        "into", "than", "then", "so", "if", "as", "very", "just", "more",
        "less", "also", "only", "even", "now", "still", "really", "actually",
        "thing", "stuff", "way", "ways", "kind", "type", "lot", "lots",
        "first", "second", "third", "next", "last", "all", "any", "some",
        "many", "much", "few", "little", "other", "another", "same", "such",
        "make", "made", "makes", "use", "used", "uses", "using", "get",
        "got", "gets", "getting", "go", "going", "went", "come", "came",
        "coming", "see", "seen", "saw", "look", "looking", "looked",
        "think", "thinks", "thinking", "thought", "know", "knew", "known",
        "say", "said", "says", "tell", "told", "tells",
    }
    text = re.sub(r"[^\w\s-]", " ", text.lower())
    tokens = [t for t in text.split() if len(t) >= min_len and t not in STOPWORDS]
    # Dedup but preserve order
    seen = set()
    out = []
    for t in tokens:
        if t not in seen:
            seen.add(t)
            out.append(t)
    return out


def score_response_against_labels(response_text: str, supposed_to: str, what_i_did: str) -> tuple[float, list[str], list[str]]:
    """Score a response on:
      +1 per keyword from supposed_to that appears
      -1 per keyword from what_i_did that appears
    Normalize to [0, 1]: positive_hits / (positive_hits + negative_hits + 1),
    falling back to 0.5 (neutral) when no signal in either direction.

    Returns (score, matched_positive_keywords, matched_negative_keywords).
    """
    if not response_text:
        return 0.5, [], []
    resp_lower = response_text.lower()
    pos_kw = extract_keywords(supposed_to)
    neg_kw = extract_keywords(what_i_did)
    pos_hits = [k for k in pos_kw if k in resp_lower]
    neg_hits = [k for k in neg_kw if k in resp_lower]
    p, n = len(pos_hits), len(neg_hits)
    if p == 0 and n == 0:
        return 0.5, [], []
    # Soft normalization — large positive hit count saturates, small negative
    # penalty is still meaningful
    score = (p + 1) / (p + n + 2)  # Laplace smoothing for stability at small counts
    return round(score, 4), pos_hits, neg_hits


def match_benchmark_to_corrections(benchmark: list[dict], corrections: list[dict]) -> list[tuple[dict, dict]]:
    """Return list of (benchmark_entry, correction_entry) pairs where prompts
    match after normalization."""
    correction_by_prompt: dict[str, dict] = {}
    for c in corrections:
        np = normalize_prompt(c.get("prompt", ""))
        if np:
            correction_by_prompt[np] = c

    matches: list[tuple[dict, dict]] = []
    for b in benchmark:
        if not b.get("replayable"):
            continue
        np = normalize_prompt(b.get("prompt", ""))
        if np in correction_by_prompt:
            matches.append((b, correction_by_prompt[np]))
            continue
        # Fallback: if correction prompt is contained in benchmark prompt (or vv)
        # — handles cases where benchmark includes wrapper context
        for cp, c in correction_by_prompt.items():
            if len(cp) >= 20 and (cp in np or np in cp):
                matches.append((b, c))
                break
    return matches


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--benchmark", type=Path, default=NIGHTLY / "benchmark.jsonl")
    ap.add_argument("--corrections", type=Path, default=CLAUDE / "corrections.jsonl")
    ap.add_argument("--run-dir", type=Path, required=True,
                    help="Directory of <benchmark_id>.json response files")
    args = ap.parse_args()

    benchmark = load_jsonl(args.benchmark)
    corrections = load_jsonl(args.corrections)
    n_corrections = len(corrections)

    if not corrections:
        out = {
            "n_matched": 0,
            "n_total_corrections": 0,
            "corrections_composite": None,
            "per_matched_task": [],
            "note": "no corrections.jsonl entries; correction-weighted scoring inactive",
        }
    else:
        matches = match_benchmark_to_corrections(benchmark, corrections)
        per_task = []
        for b, c in matches:
            bid = b["benchmark_id"]
            response_file = args.run_dir / f"{bid}.json"
            if not response_file.exists():
                continue
            try:
                resp = json.loads(response_file.read_text(encoding="utf-8"))
            except Exception:
                continue
            score, pos_hits, neg_hits = score_response_against_labels(
                resp.get("response_text", ""),
                c.get("supposed_to", ""),
                c.get("what_i_did", ""),
            )
            per_task.append({
                "benchmark_id": bid,
                "correction_ts": c.get("ts"),
                "correction_root_cause": c.get("root_cause"),
                "matched_supposed_to": pos_hits,
                "matched_what_i_did": neg_hits,
                "task_score": score,
            })

        composite = (
            round(statistics.mean(t["task_score"] for t in per_task), 4)
            if per_task else None
        )
        out = {
            "n_matched": len(per_task),
            "n_total_corrections": n_corrections,
            "corrections_composite": composite,
            "per_matched_task": per_task,
        }

    out_path = args.run_dir.parent / "corrections-score.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"corrections-score: matched {out['n_matched']}/{n_corrections} corrections to benchmark tasks; "
          f"composite={out['corrections_composite']}")
    print(f"wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
