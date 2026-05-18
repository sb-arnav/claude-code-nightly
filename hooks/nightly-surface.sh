#!/usr/bin/env bash
# NIGHTLY SessionStart hook — surfaces the latest unread nightly report.
#
# Emits a one-screen status only when there's an unread report. Marks it read
# by touching <report>.read so subsequent sessions don't repeat it.
#
# Silent (no output) when:
#   - NIGHTLY isn't installed yet
#   - No reports exist
#   - The latest report is already read

set -u
REPORTS_DIR="${HOME}/.claude/nightly/reports"
EXP_LOG="${HOME}/.claude/nightly/experiment-log.jsonl"

[[ -d "${REPORTS_DIR}" ]] || exit 0

latest=$(ls -1 "${REPORTS_DIR}"/*.md 2>/dev/null | sort | tail -1)
[[ -n "${latest}" ]] || exit 0

read_marker="${latest}.read"
[[ -f "${read_marker}" ]] && exit 0

# Pull a one-line summary from the latest experiment-log entry (if any)
summary_line=""
if [[ -f "${EXP_LOG}" ]]; then
  summary_line=$(tail -1 "${EXP_LOG}" 2>/dev/null | python3 -c '
import json, sys
try:
    o = json.loads(sys.stdin.read())
    decision = o.get("decision") or "?"
    delta = o.get("delta")
    delta_s = f"Δ{delta:+.3f}" if isinstance(delta, (int, float)) else ""
    print(f"{o.get(\"run_id\",\"?\")} · {decision} · {o.get(\"strategy\",\"?\")} {delta_s}".strip())
except Exception:
    pass
' 2>/dev/null)
fi

filename=$(basename "${latest}")
cat <<MSG
=== NIGHTLY ===
new report: ${filename}
${summary_line:+last run: ${summary_line}}
read with: cat ${latest}
disapprove (if you disagree): /nightly disapprove <run_id> "<reason>"
=== END ===
MSG

# Mark as read so this only surfaces once.
touch "${read_marker}"
exit 0
