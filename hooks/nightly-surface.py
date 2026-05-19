#!/usr/bin/env python3
"""NIGHTLY SessionStart hook — cross-platform Python version.

Prints one-screen status when there's an unread morning report.
Marks it read after first surface so it only shows once.
Silent otherwise.
"""

from __future__ import annotations

import json
from pathlib import Path

NIGHTLY = Path.home() / ".claude" / "nightly"
REPORTS = NIGHTLY / "reports"
EXP_LOG = NIGHTLY / "experiment-log.jsonl"


def main() -> int:
    if not REPORTS.exists():
        return 0

    # Find latest non-weekly report
    candidates = sorted(
        (p for p in REPORTS.glob("*.md") if not p.name.startswith("weekly-")),
        key=lambda p: p.name,
    )
    if not candidates:
        return 0
    latest = candidates[-1]
    read_marker = latest.with_suffix(latest.suffix + ".read")
    if read_marker.exists():
        return 0

    summary = ""
    if EXP_LOG.exists():
        try:
            last_line = EXP_LOG.read_text().rstrip("\n").rsplit("\n", 1)[-1]
            if last_line:
                o = json.loads(last_line)
                delta = o.get("delta")
                delta_s = f"  Δ{delta:+.3f}" if isinstance(delta, (int, float)) else ""
                summary = (
                    f"{o.get('run_id','?')} · "
                    f"{o.get('decision','?')} · "
                    f"{o.get('strategy','?')}{delta_s}"
                )
        except Exception:
            pass

    print("=== NIGHTLY ===")
    print(f"new report: {latest.name}")
    if summary:
        print(f"last run: {summary}")
    print(f"read with: cat {latest}")
    print("review proposed (observation mode): claude -p '/nightly list-proposals'")
    print("=== END ===")

    read_marker.touch()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
