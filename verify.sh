#!/usr/bin/env bash
# NIGHTLY — post-install verification.
#
# Runs the full dry-run pipeline and confirms each component works on YOUR
# machine, with YOUR data. Doesn't spend tokens — only exercises the local
# Python/bash side.
#
# Run after install.sh, or anytime to check the loop's still healthy:
#   bash ~/.claude/plugins/nightly/verify.sh
#
# Exit 0 = ready to schedule. Exit non-zero = something's wrong, don't add cron.

set -u
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="${HOME}/.claude/nightly"
SRC="${PLUGIN_DIR}/src"

PASS=0
FAIL=0

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }

bold "NIGHTLY post-install verification"
echo

bold "Files"
for f in "${SRC}/miner.py" "${SRC}/benchmark.py" "${SRC}/scorer.py" \
         "${SRC}/baseline.py" "${SRC}/disapprove.py" "${SRC}/snapshot.sh" \
         "${SRC}/strategy_stats.py" "${SRC}/safety_check.py" \
         "${SRC}/weekly_rollup.py"; do
  if [[ -e "$f" ]]; then ok "$(basename "$f")"; else fail "$(basename "$f") missing"; fi
done
[[ -d "${DATA_DIR}" ]] && ok "data dir ${DATA_DIR}" || fail "data dir missing"
echo

bold "Corpus"
if [[ -f "${DATA_DIR}/corpus.jsonl" ]]; then
  n=$(wc -l < "${DATA_DIR}/corpus.jsonl")
  if (( n > 0 )); then ok "corpus.jsonl ($n tasks)"
  else fail "corpus.jsonl is empty — no session history mined"; fi
else
  fail "corpus.jsonl not found — run miner.py"
fi
echo

bold "Benchmark"
if [[ -f "${DATA_DIR}/benchmark.jsonl" ]]; then
  n=$(wc -l < "${DATA_DIR}/benchmark.jsonl")
  replayable=$(python3 -c "
import json
with open('${DATA_DIR}/benchmark.jsonl') as fh:
    print(sum(1 for l in fh if l.strip() and json.loads(l).get('replayable')))" 2>/dev/null || echo 0)
  if (( replayable > 5 )); then ok "benchmark.jsonl ($n tasks, $replayable replayable)"
  elif (( replayable > 0 )); then fail "benchmark.jsonl has only $replayable replayable tasks — loop will be noisy"
  else fail "benchmark.jsonl has 0 replayable tasks"; fi
else
  fail "benchmark.jsonl not found"
fi
echo

bold "Scorer"
TEST_RUN_DIR="$(mktemp -d -t nightly-verify-XXXXXX)"
python3 - <<PY 2>/dev/null
import json
from pathlib import Path
bench = Path("${DATA_DIR}/benchmark.jsonl")
out = Path("${TEST_RUN_DIR}")
with bench.open() as fh:
    for line in fh:
        line=line.strip()
        if not line: continue
        e = json.loads(line)
        if not e.get("replayable"): continue
        gt = e["ground_truth"]
        (out / f"{e['benchmark_id']}.json").write_text(json.dumps({
            "benchmark_id": e["benchmark_id"],
            "duration_sec": gt["duration_sec"],
            "output_tokens": gt["output_tokens"],
            "response_text": "(verify-fixture)",
            "tools": gt["tools"],
            "files_changed": [None]*gt["files_changed_count"],
            "tool_call_sequence": list(gt["tools"].keys()),
            "completed_cleanly": gt["outcome"] in ("completed","corrected"),
            "correction_hook_fired": gt["correction_logged"],
        }))
PY
score_json=$(python3 "${SRC}/scorer.py" --run-dir "${TEST_RUN_DIR}" 2>/dev/null)
if [[ -n "${score_json}" ]]; then
  mean=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read())['score_mean'])" <<< "${score_json}")
  if [[ "${mean}" != "None" && "${mean}" != "null" ]]; then ok "scorer composed (synthetic mean=${mean})"
  else fail "scorer ran but produced no score"; fi
else
  fail "scorer crashed or produced no output"
fi
rm -rf "${TEST_RUN_DIR}"
echo

bold "Strategy stats"
stats_out=$(python3 "${SRC}/strategy_stats.py" --json 2>/dev/null)
if [[ -n "${stats_out}" ]]; then
  n_runs=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read())['n_total_runs'])" <<< "${stats_out}")
  ok "strategy_stats parsed ($n_runs runs in log)"
else
  fail "strategy_stats failed"
fi
echo

bold "Safety check"
if python3 "${SRC}/safety_check.py" --target ".gitignore" >/dev/null 2>&1; then
  fail "safety_check accepted .gitignore (forbidden) — bug"
else
  ok "safety_check correctly rejects forbidden target"
fi
echo

bold "Snapshot"
if bash "${SRC}/snapshot.sh" 2>&1 | grep -qE "(clean tree|committed|nothing staged)"; then
  ok "snapshot.sh ran cleanly"
else
  fail "snapshot.sh failed — check ~/.claude is a git repo"
fi
echo

bold "Weekly rollup"
if python3 "${SRC}/weekly_rollup.py" --days 7 >/dev/null 2>&1; then
  ok "weekly_rollup rendered"
else
  fail "weekly_rollup crashed"
fi
echo

bold "SessionStart hook"
if bash -n "${PLUGIN_DIR}/hooks/nightly-surface.sh" 2>/dev/null; then
  ok "hooks/nightly-surface.sh syntactically valid"
else
  fail "hooks/nightly-surface.sh has syntax errors"
fi
echo

bold "Summary"
echo "  passed: ${PASS}"
echo "  failed: ${FAIL}"
echo
if (( FAIL == 0 )); then
  printf '\033[32mNIGHTLY is ready.\033[0m Schedule it via crontab / launchd / /schedule.\n'
  exit 0
else
  printf '\033[31m%d check(s) failed.\033[0m Fix before scheduling.\n' "${FAIL}"
  exit 1
fi
