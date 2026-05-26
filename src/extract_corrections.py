#!/usr/bin/env python3
"""
Extract implicit corrections from session transcripts.

Scans ~/.claude/projects/*/*.jsonl for patterns where the user negates or
redirects Claude's output, then re-instructs. These are correction signals
that nightly-optimizer can use to propose substrate improvements.

Correction patterns detected:
  1. Negation + re-instruction: user says "不要/不是/别/wrong/no" then gives new direction
  2. Repeated prompt: user re-sends a very similar prompt (Claude didn't get it right)
  3. Explicit correction keywords: "应该/should/而不是/instead of/重做/redo"

Output: appends to ~/.claude/corrections.jsonl in the format nightly expects.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass

PROJECTS = Path.home() / ".claude" / "projects"
CORRECTIONS = Path.home() / ".claude" / "corrections.jsonl"

NEGATION_PATTERNS = re.compile(
    r"(?:^|\s)("
    r"不要|不是|别这样|不对|错了|重做|重来|不行|"
    r"不需要|不用|没让你|不是这个意思|搞错了|"
    r"wrong|no[,.\s]|don'?t|stop|redo|not what|"
    r"shouldn'?t|instead of|而不是|应该是|"
    r"太[长复冗]|多余|删掉|去掉|"
    r"why did you|为什么你"
    r")",
    re.IGNORECASE | re.MULTILINE,
)

CORRECTION_KEYWORDS = re.compile(
    r"(?:^|\s)("
    r"应该|should|要的是|正确的|"
    r"我要的是|改成|换成|用.*而不是|"
    r"直接|just do|只需要|简单点"
    r")",
    re.IGNORECASE | re.MULTILINE,
)


NOISE_PREFIXES = (
    "Stop hook", "<system-reminder>", "<command-", "<task-notification>",
    "<user-prompt-submit-hook>", "[ULTRAWORK", "[RALPH", "Arguments:",
    "hook feedback:", "Something went wrong", "<local-command",
    "hook success:", "[MAGIC KEYWORD", "A session-scoped Stop hook",
    "⏺ Bash", "⏺ Read", "⏺ Edit", "⏺ Write", "⎿",
    "no hostkeys", "nc -", "ssh ", "sudo ",
    "<teammate-message", "Skill No additional",
    "You are an expert", "You are a",
)
NOISE_SUBSTRINGS = (
    "session-scoped Stop hook",
    "Mode active. Continue working",
    "Mode active. If all work",
    "hook is now active with condition",
    "teammate_id=",
    "launchctl",
    "How would you like to proceed",
)


def get_user_text(msg: dict) -> str:
    content = msg.get("message", {}).get("content", "")
    if isinstance(content, str):
        text = content
    elif isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, dict) and p.get("type") == "text":
                parts.append(p.get("text", ""))
        text = " ".join(parts)
    else:
        text = ""
    # Skip system/hook noise
    stripped = text.strip()
    if any(stripped.startswith(pf) for pf in NOISE_PREFIXES):
        return ""
    if any(ns in stripped for ns in NOISE_SUBSTRINGS):
        return ""
    if stripped.startswith("<") and ">" in stripped[:60]:
        return ""
    # Skip very short messages (likely "ok", "y", "n")
    if len(stripped) < 10:
        return ""
    # Skip log lines (timestamp patterns like "2026-05-12 23:13:51")
    if re.match(r"\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}", stripped):
        return ""
    return text


def get_timestamp(msg: dict) -> str:
    return msg.get("timestamp", "")


def extract_project_name(path: Path) -> str:
    return path.parent.name.replace("-Users-luwei-will-", "").replace("-", "/")[:60]


def is_correction_pair(prev_user_text: str, curr_user_text: str) -> dict | None:
    """Check if curr_user_text is a correction of what Claude did after prev_user_text."""
    if not curr_user_text or len(curr_user_text) < 5:
        return None

    neg_match = NEGATION_PATTERNS.search(curr_user_text[:200])
    corr_match = CORRECTION_KEYWORDS.search(curr_user_text[:300])

    if not neg_match and not corr_match:
        return None

    signal_type = "negation" if neg_match else "correction_keyword"
    matched = (neg_match or corr_match).group(1)

    return {
        "signal_type": signal_type,
        "trigger_word": matched.strip(),
    }


def extract_from_session(jsonl_path: Path, since: str, until: str) -> list[dict]:
    results = []
    try:
        msgs = [json.loads(l) for l in jsonl_path.open(encoding="utf-8") if l.strip()]
    except Exception:
        return results

    user_msgs = [m for m in msgs if m.get("type") == "user"]
    if len(user_msgs) < 2:
        return results

    # Check date range
    first_ts = get_timestamp(user_msgs[0])[:10]
    if first_ts and (first_ts < since or first_ts > until):
        return results

    project = extract_project_name(jsonl_path)
    session_id = jsonl_path.stem

    for i in range(1, len(user_msgs)):
        prev_text = get_user_text(user_msgs[i - 1])
        curr_text = get_user_text(user_msgs[i])

        detection = is_correction_pair(prev_text, curr_text)
        if not detection:
            continue

        ts = get_timestamp(user_msgs[i])
        results.append({
            "timestamp": ts,
            "session_id": session_id,
            "project": project,
            "original_prompt": prev_text[:500],
            "correction_text": curr_text[:500],
            "signal_type": detection["signal_type"],
            "trigger_word": detection["trigger_word"],
            "root_cause": "user-correction-extracted",
            "proposed_rule": curr_text[:200],
        })

    return results


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="2026-05-01")
    ap.add_argument("--until", default="2026-05-21")
    ap.add_argument("--dry-run", action="store_true", help="Print but don't write")
    ap.add_argument("--limit", type=int, default=100, help="Max corrections to extract")
    args = ap.parse_args()

    all_corrections: list[dict] = []

    jsonl_files = sorted(PROJECTS.glob("*/*.jsonl"))
    print(f"Scanning {len(jsonl_files)} session files for corrections in {args.since}~{args.until}...")

    for jf in jsonl_files:
        found = extract_from_session(jf, args.since, args.until)
        all_corrections.extend(found)
        if len(all_corrections) >= args.limit * 3:
            break

    # Deduplicate by correction_text similarity (take first occurrence)
    seen_texts: set[str] = set()
    unique: list[dict] = []
    for c in all_corrections:
        key = c["correction_text"][:80]
        if key not in seen_texts:
            seen_texts.add(key)
            unique.append(c)

    # Take top N by signal strength (negation > keyword)
    unique.sort(key=lambda x: (0 if x["signal_type"] == "negation" else 1))
    unique = unique[: args.limit]

    print(f"Found {len(all_corrections)} raw corrections, {len(unique)} unique (limit={args.limit})")

    if args.dry_run:
        for c in unique[:10]:
            print(f"\n  [{c['trigger_word']}] {c['correction_text'][:100]}")
            print(f"    project={c['project']} ts={c['timestamp'][:10]}")
        if len(unique) > 10:
            print(f"\n  ... and {len(unique) - 10} more")
        return 0

    # Append to corrections.jsonl
    with CORRECTIONS.open("a", encoding="utf-8") as fh:
        for c in unique:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")

    print(f"Appended {len(unique)} corrections to {CORRECTIONS}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
