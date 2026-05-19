#!/usr/bin/env python3
"""
NIGHTLY — Weekly Rollup.

Aggregates the last N days of experiment-log entries into a single markdown
report. Run weekly by cron (or on-demand) to make the compounding visible.

Daily reports don't reveal the trend. The weekly rollup is the artifact that
answers "is the loop actually working?" — by surfacing:

  - Score trend over the period
  - Which strategies landed vs which struggled
  - Kept changes with one-line diff summaries
  - Dead-lettered patterns
  - Concrete suggestions for what to try next

Usage:
  python3 weekly_rollup.py                  # last 7 days → today's rollup
  python3 weekly_rollup.py --days 30        # custom period
  python3 weekly_rollup.py --since 2026-05-13
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
import statistics
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
CLAUDE = Path.home() / ".claude"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"
DEAD = NIGHTLY / "dead-letter.jsonl"
ROLLUP_DIR = NIGHTLY / "reports" / "weekly"


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


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


def filter_period(log: list[dict], since: datetime) -> list[dict]:
    out = []
    for e in log:
        ts = parse_ts(e.get("ts"))
        if ts is None:
            continue
        if ts >= since:
            out.append(e)
    return out


def git_diff_summary(commit: str | None) -> str:
    if not commit:
        return "(no commit)"
    proc = subprocess.run(
        ["git", "-C", str(CLAUDE), "show", "--stat", "--format=", commit],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return f"(diff unavailable for {commit[:8]})"
    lines = [l for l in proc.stdout.strip().splitlines() if l.strip()]
    return "\n".join(f"    {l}" for l in lines) if lines else "(empty diff)"


def render(log: list[dict], since: datetime, period_days: int) -> str:
    runs = filter_period(log, since)
    # Filter out the synthetic seed and disapproval entries; they're not "runs"
    real_runs = [r for r in runs if r.get("decision") not in ("seed", "user-reverted")]

    decisions = Counter(r.get("decision") for r in real_runs)
    strategies = Counter(r.get("strategy") for r in real_runs)

    # Score trend (only entries with a numeric score)
    scored = [r for r in real_runs if isinstance(r.get("score_mean"), (int, float))]
    scored.sort(key=lambda r: r.get("ts") or "")
    score_series = [(r.get("ts", "")[:10], r["score_mean"]) for r in scored]

    # Kept changes
    kept = [r for r in real_runs if r.get("decision") in ("kept", "first-real-baseline")]

    # Dead-letter additions during the period (best-effort — match by ts via run_id)
    dl_pairs: list[tuple[str, str]] = []
    if DEAD.exists():
        with DEAD.open() as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = parse_ts(o.get("ts"))
                if ts and ts >= since:
                    dl_pairs.append((o.get("strategy"), o.get("target_file")))

    # Per-strategy effectiveness across the period
    per_strategy_kept: defaultdict[str, int] = defaultdict(int)
    per_strategy_tried: defaultdict[str, int] = defaultdict(int)
    for r in real_runs:
        s = r.get("strategy")
        if not s:
            continue
        per_strategy_tried[s] += 1
        if r.get("decision") in ("kept", "first-real-baseline"):
            per_strategy_kept[s] += 1

    today = datetime.now(tz=timezone.utc).date().isoformat()

    lines: list[str] = []
    lines.append(f"# NIGHTLY Weekly Rollup — {today}")
    lines.append("")
    lines.append(f"**Period:** last {period_days} days ({since.date().isoformat()} → {today})")
    lines.append(f"**Total runs:** {len(real_runs)}")
    lines.append("")

    if not real_runs:
        lines.append("No real runs in this period. NIGHTLY hasn't fired or all entries are seed/disapproval.")
        lines.append("")
        lines.append("If the cron is installed, check `~/.claude/nightly/logs/cron.log` for errors.")
        return "\n".join(lines)

    # Decision breakdown
    lines.append("## Decisions")
    lines.append("")
    lines.append("| Decision | Count |")
    lines.append("|---|---|")
    for d, n in decisions.most_common():
        lines.append(f"| {d} | {n} |")
    lines.append("")

    # Score trend
    if score_series:
        first_score = score_series[0][1]
        last_score = score_series[-1][1]
        delta = last_score - first_score
        median_score = statistics.median([s for _, s in score_series])
        lines.append("## Score trend")
        lines.append("")
        lines.append(f"**First → last:** {first_score:.4f} → {last_score:.4f}  (Δ {delta:+.4f})")
        lines.append(f"**Median:** {median_score:.4f}")
        lines.append("")
        lines.append("| Date | Score | Decision | Strategy |")
        lines.append("|---|---|---|---|")
        for r in scored:
            lines.append(
                f"| {(r.get('ts','')[:10])} "
                f"| {r['score_mean']:.4f} "
                f"| {r.get('decision','?')} "
                f"| {r.get('strategy','?')} |"
            )
        lines.append("")

    # Per-strategy effectiveness in this period
    lines.append("## Strategy effectiveness (this period)")
    lines.append("")
    lines.append("| Strategy | Tried | Kept | Rate |")
    lines.append("|---|---|---|---|")
    for s in sorted(per_strategy_tried.keys(),
                    key=lambda s: -per_strategy_kept[s] / max(1, per_strategy_tried[s])):
        tried = per_strategy_tried[s]
        kept_n = per_strategy_kept[s]
        rate = kept_n / tried if tried else 0
        lines.append(f"| {s} | {tried} | {kept_n} | {rate:.0%} |")
    lines.append("")

    # Kept changes with diffs
    if kept:
        lines.append("## Kept changes")
        lines.append("")
        for r in kept:
            lines.append(
                f"### `{r.get('run_id','?')}` — {r.get('strategy','?')} on {r.get('target_file','?')}"
            )
            lines.append("")
            lines.append(f"- **Score:** {r.get('score_mean','?')}")
            lines.append(f"- **Decision:** {r.get('decision','?')}")
            lines.append(f"- **Commit:** `{(r.get('new_commit') or '')[:8]}`")
            lines.append("- **Diff:**")
            lines.append("```")
            lines.append(git_diff_summary(r.get("new_commit")))
            lines.append("```")
            lines.append("")

    # Dead-letter additions
    if dl_pairs:
        lines.append("## Dead-lettered (won't be retried)")
        lines.append("")
        for s, t in dl_pairs:
            lines.append(f"- `{s}` → `{t}`")
        lines.append("")

    # Forward-looking guidance: surface untried/promising
    try:
        from strategy_stats import compute_stats, ALL_STRATEGIES, load_log as load_log_for_stats
        stats = compute_stats(load_log_for_stats())
        promising = [s for s, v in stats.items() if v["recommendation"] == "promising"]
        untried = [s for s in ALL_STRATEGIES if s not in stats or stats[s]["tried"] == 0]
        avoid = [s for s, v in stats.items() if v["recommendation"] == "avoid"]
        lines.append("## Guidance for next week")
        lines.append("")
        if promising:
            lines.append(f"- **Prefer:** {', '.join(promising)} (proven kept rate)")
        if untried:
            lines.append(f"- **Explore:** {', '.join(untried)} (no data yet)")
        if avoid:
            lines.append(f"- **Avoid:** {', '.join(avoid)} (proven ineffective)")
        if not (promising or untried or avoid):
            lines.append("- Insufficient data. Let the loop run another week.")
        lines.append("")
    except Exception as e:
        lines.append(f"_(strategy guidance unavailable: {e})_")
        lines.append("")

    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--since", type=str, default=None, help="YYYY-MM-DD override")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)
    else:
        since = datetime.now(tz=timezone.utc) - timedelta(days=args.days)

    log = load_log()
    report = render(log, since, args.days)

    ROLLUP_DIR.mkdir(parents=True, exist_ok=True)
    today = datetime.now(tz=timezone.utc).date().isoformat()
    out_path = args.out or (ROLLUP_DIR / f"weekly-{today}.md")
    out_path.write_text(report, encoding="utf-8")
    print(report)
    print()
    print(f"wrote: {out_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
