#!/usr/bin/env python3
"""
NIGHTLY — Personal Benchmark Builder.

Reads corpus.jsonl (produced by miner.py) and selects a stratified eval suite of
~30-50 real prompts from your actual work, weighted across:

  - project diversity (workspace / <project-a> / <project-b> / …)
  - task-type diversity (code-change / debug / audit / research / doc-write / exploration / chat)
  - difficulty diversity (short / medium / long)
  - recency mix (60% last 30 days, 30% 30-90 days, 10% older)

Writes ~/.claude/nightly/benchmarks/benchmark-YYYY-MM-DD.jsonl and updates a
symlink `benchmark.jsonl` -> latest. Old benchmark files are retained as
regression suites.
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
import math
import random
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
CORPUS = NIGHTLY / "corpus.jsonl"
BENCH_DIR = NIGHTLY / "benchmarks"
LATEST = NIGHTLY / "benchmark.jsonl"

# Tasks shorter than this are too noisy to be useful as eval cases.
MIN_DURATION_SEC = 60
MIN_PROMPT_CHARS = 20
# Prompts longer than this are usually pasted documents, not work asks.
MAX_PROMPT_CHARS = 4000

RECENCY_BUCKETS = [
    ("last_30d", 30, 0.60),
    ("30_90d", 90, 0.30),
    ("older", math.inf, 0.10),
]


def load_corpus(path: Path) -> list[dict]:
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


def parse_ts(s: str) -> datetime:
    return datetime.fromisoformat(s)


def keep_task(t: dict) -> bool:
    if t.get("duration_sec", 0) < MIN_DURATION_SEC:
        return False
    pc = t.get("prompt_chars", 0)
    if pc < MIN_PROMPT_CHARS or pc > MAX_PROMPT_CHARS:
        return False
    if t.get("outcome") == "no-op":
        return False
    # Strip obvious slash-command echoes that snuck past mining.
    if t["prompt"].lstrip().startswith("<"):
        return False
    return True


def recency_bucket(t: dict, now: datetime) -> str:
    ts = parse_ts(t["first_message_at"])
    age = (now - ts).days
    if age <= 30:
        return "last_30d"
    if age <= 90:
        return "30_90d"
    return "older"


def stratified_sample(tasks: list[dict], target_size: int, seed: int, now: datetime) -> list[dict]:
    rng = random.Random(seed)

    # Bucket by (recency, task_type, project). Within each bucket, sort by
    # duration descending so we prefer richer signals when downsampling.
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for t in tasks:
        key = (recency_bucket(t, now), t["task_type"], t["project"])
        buckets[key].append(t)
    for arr in buckets.values():
        arr.sort(key=lambda x: x["duration_sec"], reverse=True)

    # Quota per recency bucket from the configured weights.
    recency_quota = {name: max(1, round(target_size * w)) for name, _, w in RECENCY_BUCKETS}

    picked: list[dict] = []
    # Walk recency buckets in priority order. Within each, round-robin across
    # (task_type, project) keys until quota is met or pool is exhausted.
    for name, _, _ in RECENCY_BUCKETS:
        quota = recency_quota[name]
        # All buckets matching this recency, randomized order:
        keys = [k for k in buckets if k[0] == name]
        rng.shuffle(keys)
        # Round-robin pop one from each key:
        added = 0
        while added < quota and keys:
            next_keys = []
            for k in keys:
                if added >= quota:
                    break
                if buckets[k]:
                    picked.append(buckets[k].pop(0))
                    added += 1
                if buckets[k]:
                    next_keys.append(k)
            keys = next_keys

    # If quotas underran (e.g., not enough old tasks), top up from anywhere.
    if len(picked) < target_size:
        leftovers = [t for arr in buckets.values() for t in arr]
        rng.shuffle(leftovers)
        picked.extend(leftovers[: target_size - len(picked)])

    # If we somehow overran (rounding), trim.
    return picked[:target_size]


def to_benchmark_entry(t: dict) -> dict:
    """Reshape a corpus row into a benchmark-eval-ready row.

    Mechanical-scoring signals are computed at run time against a candidate
    config; here we only record the prompt, ground-truth shape, and replay
    metadata.
    """
    return {
        "benchmark_id": t["task_id"],
        "prompt": t["prompt"],
        "project": t["project"],
        "task_type": t["task_type"],
        "difficulty": t["difficulty"],
        "ground_truth": {
            "tool_calls": t["tool_calls"],
            "tools": t["tools"],
            "files_changed_count": len(t["files_changed"]),
            "duration_sec": t["duration_sec"],
            "output_tokens": t["output_tokens"],
            "outcome": t["outcome"],
            "correction_logged": t["correction_logged"],
        },
        "first_message_at": t["first_message_at"],
        "session_id": t["session_id"],
        "replayable": (
            t["tool_calls"] > 0
            and t["outcome"] in ("completed", "corrected")
            and not t["prompt"].lstrip().startswith("/")
        ),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--corpus", type=Path, default=CORPUS)
    ap.add_argument("--out-dir", type=Path, default=BENCH_DIR)
    ap.add_argument("--size", type=int, default=40)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    if not args.corpus.exists():
        print(f"corpus not found: {args.corpus}. Run miner.py first.")
        return 2

    args.out_dir.mkdir(parents=True, exist_ok=True)
    tasks = [t for t in load_corpus(args.corpus) if keep_task(t)]
    if not tasks:
        # Fresh install with too few sessions to satisfy filters (e.g., all
        # under 90 seconds). Write an empty benchmark and exit 0 so install.sh
        # doesn't die. /nightly will skip replay until the corpus matures.
        today = datetime.now(tz=timezone.utc).date().isoformat()
        out = args.out_dir / f"benchmark-{today}.jsonl"
        out.write_text("", encoding="utf-8")
        if LATEST.is_symlink() or LATEST.exists():
            LATEST.unlink()
        LATEST.symlink_to(out)
        if not args.quiet:
            print("no eligible tasks in corpus after filtering")
            print(f"wrote empty benchmark: {out}")
            print("(run again after a few real Claude Code sessions have accumulated)")
        return 0

    now = datetime.now(tz=timezone.utc)
    picked = stratified_sample(tasks, args.size, args.seed, now)

    today = now.date().isoformat()
    out = args.out_dir / f"benchmark-{today}.jsonl"
    with out.open("w") as fh:
        for t in picked:
            fh.write(json.dumps(to_benchmark_entry(t), ensure_ascii=False) + "\n")

    if LATEST.is_symlink() or LATEST.exists():
        LATEST.unlink()
    LATEST.symlink_to(out)

    if not args.quiet:
        proj = Counter(p["project"] for p in picked)
        tt = Counter(p["task_type"] for p in picked)
        diff = Counter(p["difficulty"] for p in picked)
        rec = Counter(recency_bucket(p, now) for p in picked)
        repl = sum(1 for p in picked if to_benchmark_entry(p)["replayable"])
        print(f"eligible tasks (post-filter): {len(tasks)}")
        print(f"selected:    {len(picked)}")
        print(f"replayable:  {repl}/{len(picked)}")
        print(f"by_project:  {dict(proj)}")
        print(f"by_type:     {dict(tt)}")
        print(f"by_difficulty: {dict(diff)}")
        print(f"by_recency:  {dict(rec)}")
        print(f"wrote:       {out}")
        print(f"symlinked:   {LATEST} -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
