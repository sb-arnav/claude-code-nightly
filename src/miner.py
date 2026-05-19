#!/usr/bin/env python3
"""
NIGHTLY — Session Miner.

Walks ~/.claude/projects/*/*.jsonl, segments each session into tasks by user-prompt
boundary, and writes one JSON line per task to corpus.jsonl.

A "task" is everything an assistant did between one user prompt and the next.
Slash-command wrappers, dequeued queue ops, attachments, and hook stdouts are
skipped — only real user input opens a task.
"""

from __future__ import annotations

import argparse
import json
import re
import sys

# Force UTF-8 stdio on Windows where Python defaults to cp1252; without this,
# print() of any Unicode (em-dash, arrows, smart quotes — i.e. most Claude
# output) crashes with UnicodeEncodeError. Idempotent and safe on all platforms.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):
    pass
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

NIGHTLY = Path.home() / ".claude" / "nightly"
PROJECTS = Path.home() / ".claude" / "projects"
CORPUS = NIGHTLY / "corpus.jsonl"
CORRECTIONS = Path.home() / ".claude" / "corrections.jsonl"

SLASH_PREFIXES = (
    "<command-name>",
    "<command-message>",
    "<local-command-stdout>",
    "<local-command-caveat>",
    "<command-stdout>",
    "<command-stderr>",
    "<task-notification>",
    "<system-reminder>",
    "<user-prompt-submit-hook>",
)
EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}
MIN_TASK_SECONDS = 90


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        # JSONLs use ISO-8601 with trailing Z
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def extract_user_text(msg: Any) -> str | None:
    if isinstance(msg, dict):
        c = msg.get("content")
    else:
        c = msg
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        parts = []
        for p in c:
            if isinstance(p, dict) and p.get("type") == "text":
                t = p.get("text") or ""
                if t:
                    parts.append(t)
        if parts:
            return "\n".join(parts)
    return None


def is_real_user_prompt(text: str | None) -> bool:
    if not text:
        return False
    stripped = text.lstrip()
    if any(stripped.startswith(p) for p in SLASH_PREFIXES):
        # Slash-command echo. Skip — the real intent is in <command-args>.
        return False
    # Tool result responses can appear as "user" role; they show up as a list with
    # type=tool_result which we already filtered (extract_user_text returns text only).
    return True


def load_corrections() -> list[dict[str, Any]]:
    if not CORRECTIONS.exists():
        return []
    out = []
    with CORRECTIONS.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = parse_ts(o.get("ts"))
            if ts is None:
                continue
            o["_ts"] = ts
            out.append(o)
    return out


def project_from_dir(dir_name: str) -> str:
    """Best-effort: unmangle Claude Code's `-home-<user>-<project>` convention.

    Claude Code writes per-project session dirs at
    `~/.claude/projects/-home-<user>-<…path…>/`. We strip the home-<user>
    prefix matching the current OS user; what remains is the project name.
    Examples (assume current user is `bob`):
      `-home-bob`          → `workspace`
      `-home-bob-Staq`     → `Staq`
      `-home-bob-code-foo` → `code/foo`
    Falls back to a best-effort de-mangled name for any other shape.
    """
    import getpass
    user = getpass.getuser()
    parts = [p for p in dir_name.split("-") if p]
    if parts and parts[0] == "home" and len(parts) >= 2 and parts[1] == user:
        rest = parts[2:]
        if not rest:
            return "workspace"
        return "/".join(rest).strip("/") or "workspace"
    # Generic fallback for unfamiliar layouts.
    return dir_name.lstrip("-") or "unknown"


def classify_task(prompt: str, tool_counts: Counter, files_changed: list[str]) -> str:
    p = prompt.lower()
    if files_changed:
        return "code-change"
    if any(t in tool_counts for t in ("WebSearch", "WebFetch", "Tavily", "Linear")):
        return "research"
    if any(k in p for k in ("debug", "broken", "error", "fix the", "why is")):
        return "debug"
    if any(k in p for k in ("audit", "review", "check", "verify")):
        return "audit"
    if tool_counts.get("Read", 0) > 0 and tool_counts.get("Bash", 0) > 0 and not files_changed:
        return "exploration"
    if any(k in p for k in ("write a", "draft", "compose", "essay", "doc")):
        return "doc-write"
    return "chat"


def difficulty_bucket(duration_sec: float, tool_calls: int) -> str:
    if duration_sec < 120:
        return "short"
    if duration_sec < 600:
        return "medium"
    return "long"


def iter_session(path: Path) -> Iterable[dict[str, Any]]:
    with path.open() as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def segment_tasks(messages: list[dict[str, Any]], session_id: str, project: str,
                  corrections: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort by timestamp, walk forward, open a task at each real user prompt."""
    messages = sorted(messages, key=lambda m: m.get("timestamp") or "")
    tasks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None

    def close(end_ts: datetime | None) -> None:
        nonlocal current
        if current is None:
            return
        start = current["_start_ts"]
        end = end_ts or current.get("_last_ts") or start
        duration = max(0.0, (end - start).total_seconds())
        tool_counts: Counter = current["_tool_counts"]
        files_changed = sorted(set(current["_files_changed"]))
        # Detect a correction logged within the task window (+/- 60s tolerance).
        correction_logged = False
        for c in corrections:
            cts = c["_ts"]
            if start <= cts <= end:
                # Project match is soft: corrections record "workspace" or a project name.
                cproj = (c.get("project") or "").lower()
                if cproj in ("", "workspace", project.lower()):
                    correction_logged = True
                    break
        prompt = current["prompt"]
        outcome = "completed"
        if duration < MIN_TASK_SECONDS:
            outcome = "abandoned"
        elif not tool_counts and not current["_final_assistant_chars"]:
            outcome = "no-op"
        if correction_logged:
            outcome = "corrected"
        rec = {
            "task_id": f"{session_id[:8]}-{len(tasks):03d}",
            "session_id": session_id,
            "project": project,
            "first_message_at": start.isoformat(),
            "last_message_at": end.isoformat(),
            "duration_sec": round(duration, 1),
            "prompt": prompt,
            "prompt_chars": len(prompt),
            "tool_calls": sum(tool_counts.values()),
            "tools": dict(tool_counts),
            "files_changed": files_changed,
            "final_response_chars": current["_final_assistant_chars"],
            "output_tokens": current["_output_tokens"],
            "outcome": outcome,
            "correction_logged": correction_logged,
            "task_type": classify_task(prompt, tool_counts, files_changed),
            "difficulty": difficulty_bucket(duration, sum(tool_counts.values())),
        }
        tasks.append(rec)
        current = None

    for m in messages:
        ts = parse_ts(m.get("timestamp"))
        mtype = m.get("type")
        if mtype == "user":
            text = extract_user_text(m.get("message"))
            if not is_real_user_prompt(text):
                continue
            close(ts)
            current = {
                "prompt": text.strip(),
                "_start_ts": ts or datetime.now(tz=timezone.utc),
                "_last_ts": ts,
                "_tool_counts": Counter(),
                "_files_changed": [],
                "_final_assistant_chars": 0,
                "_output_tokens": 0,
            }
        elif mtype == "assistant" and current is not None:
            current["_last_ts"] = ts or current["_last_ts"]
            msg = m.get("message", {}) or {}
            usage = msg.get("usage") or {}
            current["_output_tokens"] += int(usage.get("output_tokens") or 0)
            c = msg.get("content")
            text_chars = 0
            if isinstance(c, list):
                for part in c:
                    if not isinstance(part, dict):
                        continue
                    if part.get("type") == "text":
                        text_chars += len(part.get("text") or "")
                    elif part.get("type") == "tool_use":
                        name = part.get("name") or "unknown"
                        current["_tool_counts"][name] += 1
                        if name in EDIT_TOOLS:
                            inp = part.get("input") or {}
                            fp = inp.get("file_path") or inp.get("notebook_path")
                            if fp:
                                current["_files_changed"].append(fp)
            elif isinstance(c, str):
                text_chars += len(c)
            if text_chars:
                current["_final_assistant_chars"] = text_chars
        # attachments / queue ops / system: skip
    close(None)
    return tasks


def main() -> int:
    ap = argparse.ArgumentParser(description="Mine session JSONLs into corpus.jsonl")
    ap.add_argument("--projects-dir", type=Path, default=PROJECTS)
    ap.add_argument("--out", type=Path, default=CORPUS)
    ap.add_argument("--limit", type=int, default=0, help="Stop after N sessions (0=all)")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    corrections = load_corrections()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.out.with_suffix(args.out.suffix + ".tmp")

    if not args.projects_dir.exists():
        # Fresh Claude Code install — no sessions to mine yet. Write an empty
        # corpus and exit 0 so install.sh doesn't die on `set -euo pipefail`.
        # The loop will start producing useful eval data as the user accumulates
        # sessions; miner can be re-run anytime via `nightly/miner.py`.
        tmp.write_text("", encoding="utf-8")
        tmp.replace(args.out)
        if not args.quiet:
            print(f"projects dir not found: {args.projects_dir}")
            print(f"wrote empty corpus: {args.out}")
            print("(run again after a few Claude Code sessions have accumulated)")
        return 0

    sessions_seen = 0
    tasks_total = 0
    by_project: Counter = Counter()
    with tmp.open("w") as out:
        for proj_dir in sorted(args.projects_dir.iterdir()):
            if not proj_dir.is_dir():
                continue
            project = project_from_dir(proj_dir.name)
            for sf in sorted(proj_dir.glob("*.jsonl")):
                sessions_seen += 1
                if args.limit and sessions_seen > args.limit:
                    break
                session_id = sf.stem
                msgs = list(iter_session(sf))
                if not msgs:
                    continue
                tasks = segment_tasks(msgs, session_id, project, corrections)
                for t in tasks:
                    out.write(json.dumps(t, ensure_ascii=False) + "\n")
                    tasks_total += 1
                    by_project[project] += 1
            if args.limit and sessions_seen > args.limit:
                break
    tmp.replace(args.out)

    if not args.quiet:
        print(f"sessions: {sessions_seen}")
        print(f"tasks:    {tasks_total}")
        print(f"by_project (top 10): {by_project.most_common(10)}")
        print(f"wrote:    {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
