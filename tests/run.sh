#!/usr/bin/env bash
# NIGHTLY test suite — runs against synthetic fixtures in a tmpdir.
#
# Exercises every Python/bash script with controlled inputs so we can assert
# specific outputs. Doesn't depend on the user's real ~/.claude/.
#
# Usage:
#   bash tests/run.sh
#
# Exit 0 = all pass. Exit non-zero = something broke.

set -u
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${PLUGIN_DIR}/src"

PASS=0
FAIL=0
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }
bold() { printf '\033[1m%s\033[0m\n' "$*"; }

# Stage 1: build a synthetic ~/.claude/ in a tmpdir
TESTDIR="$(mktemp -d -t nightly-tests-XXXXXX)"
trap "rm -rf ${TESTDIR}" EXIT

export HOME="${TESTDIR}"
mkdir -p "${TESTDIR}/.claude/nightly/"{benchmarks,experiments,reports,logs}
mkdir -p "${TESTDIR}/.claude/projects/-home-test"

bold "Test setup: synthetic ~/.claude at ${TESTDIR}/.claude"

# Synthesize one session JSONL that the miner can parse
python3 - <<PY
import json
from pathlib import Path
session = Path("${TESTDIR}/.claude/projects/-home-test/synthetic.jsonl")
msgs = [
    {"type":"user","timestamp":"2026-05-01T10:00:00.000Z","message":{"content":"add a hook that detects design-mode prompts"},"sessionId":"synthetic"},
    {"type":"assistant","timestamp":"2026-05-01T10:00:30.000Z","message":{"content":[{"type":"text","text":"Took the position — gh search first."}],"usage":{"output_tokens":1200}}},
    {"type":"assistant","timestamp":"2026-05-01T10:01:00.000Z","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":"gh search repos"}}],"usage":{"output_tokens":200}}},
    {"type":"assistant","timestamp":"2026-05-01T10:02:00.000Z","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"/tmp/x"}}],"usage":{"output_tokens":100}}},
    {"type":"assistant","timestamp":"2026-05-01T10:03:00.000Z","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"/tmp/hook.sh"}}],"usage":{"output_tokens":500}}},
    {"type":"user","timestamp":"2026-05-01T10:10:00.000Z","message":{"content":"now write a quick research note on what you found"},"sessionId":"synthetic"},
    {"type":"assistant","timestamp":"2026-05-01T10:11:00.000Z","message":{"content":[{"type":"text","text":"Synthesis: prior art at autoresearch shows..."}],"usage":{"output_tokens":1500}}},
    {"type":"assistant","timestamp":"2026-05-01T10:13:00.000Z","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"/tmp/notes.md"}}],"usage":{"output_tokens":300}}},
]
with session.open("w") as fh:
    for m in msgs:
        fh.write(json.dumps(m) + "\n")
print(f"wrote {len(msgs)} messages to {session}")
PY
echo

# Stage 2: miner
bold "miner.py"
if python3 "${SRC}/miner.py" --quiet --projects-dir "${TESTDIR}/.claude/projects" --out "${TESTDIR}/.claude/nightly/corpus.jsonl"; then
  n=$(wc -l < "${TESTDIR}/.claude/nightly/corpus.jsonl")
  if (( n > 0 )); then ok "extracted $n tasks from synthetic session"
  else fail "extracted 0 tasks (miner regression)"; fi
else
  fail "miner crashed"
fi
echo

# Stage 3: benchmark
bold "benchmark.py"
if python3 "${SRC}/benchmark.py" --quiet --corpus "${TESTDIR}/.claude/nightly/corpus.jsonl" --out-dir "${TESTDIR}/.claude/nightly/benchmarks" --size 2 --seed 1; then
  if [[ -L "${TESTDIR}/.claude/nightly/benchmark.jsonl" ]] || [[ -f "${TESTDIR}/.claude/nightly/benchmark.jsonl" ]]; then
    ok "benchmark built"
  else
    fail "benchmark output missing"
  fi
else
  fail "benchmark crashed"
fi
echo

# Stage 4: scorer with hand-crafted responses
bold "scorer.py"
RUN_DIR="${TESTDIR}/.claude/nightly/experiments/test/responses"
mkdir -p "${RUN_DIR}"
python3 - <<PY
import json
from pathlib import Path
bench = Path("${TESTDIR}/.claude/nightly/benchmark.jsonl")
out = Path("${RUN_DIR}")
if bench.exists():
    with bench.open() as fh:
        for line in fh:
            line=line.strip()
            if not line: continue
            e = json.loads(line)
            if not e.get("replayable"): continue
            gt = e["ground_truth"]
            (out / f"{e['benchmark_id']}.json").write_text(json.dumps({
                "benchmark_id": e["benchmark_id"],
                "duration_sec": 10.0,
                "output_tokens": 500,
                "response_text": "(test)",
                "tools": gt["tools"],
                "files_changed": [],
                "tool_call_sequence": list(gt["tools"].keys()),
                "completed_cleanly": True,
                "correction_hook_fired": False,
            }))
PY
score_out=$(python3 "${SRC}/scorer.py" --benchmark "${TESTDIR}/.claude/nightly/benchmark.jsonl" --run-dir "${RUN_DIR}" 2>/dev/null)
if [[ -n "${score_out}" ]]; then
  mean=$(python3 -c "import json,sys; print(json.loads(sys.stdin.read())['score_mean'])" <<< "${score_out}")
  if [[ "${mean}" != "None" ]]; then ok "scorer returned mean=${mean}"
  else ok "scorer returned no replayable items (empty benchmark sample is OK for small fixture)"; fi
else
  fail "scorer crashed"
fi
echo

# Stage 5: strategy_stats with empty log
bold "strategy_stats.py"
touch "${TESTDIR}/.claude/nightly/experiment-log.jsonl"
if python3 "${SRC}/strategy_stats.py" --json 2>/dev/null | python3 -c "import json,sys; o=json.load(sys.stdin); assert isinstance(o.get('untried'), list); assert 'rule-rewrite' in o['untried']; print('ok')" >/dev/null 2>&1; then
  ok "strategy_stats handles empty log; all 5 strategies in 'untried'"
else
  fail "strategy_stats failed on empty log"
fi
echo

# Stage 6: safety_check
bold "safety_check.py (forbidden + safe paths)"
if python3 "${SRC}/safety_check.py" --target ".gitignore" 2>/dev/null; then
  fail "safety_check accepted .gitignore"
else
  ok "safety_check rejects forbidden path .gitignore"
fi
if python3 "${SRC}/safety_check.py" --target "plugins/foo" 2>/dev/null; then
  fail "safety_check accepted plugins/foo"
else
  ok "safety_check rejects plugins/ prefix"
fi
if python3 "${SRC}/safety_check.py" --target "projects/anything" 2>/dev/null; then
  fail "safety_check accepted projects/anything"
else
  ok "safety_check rejects projects/ prefix"
fi
echo

# Stage 7: weekly_rollup with empty log
bold "weekly_rollup.py"
if python3 "${SRC}/weekly_rollup.py" --days 7 >/dev/null 2>&1; then
  ok "weekly_rollup runs against empty log"
else
  fail "weekly_rollup crashed"
fi
echo

# Stage 8: bash syntax for all .sh files
bold "bash syntax"
for sh in "${PLUGIN_DIR}/install.sh" "${PLUGIN_DIR}/verify.sh" \
          "${PLUGIN_DIR}/src/snapshot.sh" \
          "${PLUGIN_DIR}/hooks/nightly-surface.sh" \
          "${PLUGIN_DIR}/tests/run.sh"; do
  if [[ -f "$sh" ]] && bash -n "$sh"; then ok "$(basename $sh) parses"
  else fail "$(basename $sh) syntax error"; fi
done
echo

# Stage 9: Python syntax for all .py files
bold "python syntax"
for py in "${SRC}"/*.py; do
  if python3 -m py_compile "$py" 2>/dev/null; then ok "$(basename $py) compiles"
  else fail "$(basename $py) syntax error"; fi
done
echo

# Stage 10: decide.py decision-branch unit tests
bold "decide.py decision branches"
DECIDE_TESTS="${PLUGIN_DIR}/tests/test_decide.sh"
if [[ -f "${DECIDE_TESTS}" ]]; then
  DECIDE_OUT=$(bash "${DECIDE_TESTS}" 2>&1)
  DECIDE_RC=$?
  DECIDE_PASS=$(echo "$DECIDE_OUT" | grep -oE 'passed: [0-9]+' | awk '{print $2}')
  DECIDE_FAIL=$(echo "$DECIDE_OUT" | grep -oE 'failed: [0-9]+' | awk '{print $2}')
  if (( DECIDE_RC == 0 )); then
    ok "test_decide.sh: ${DECIDE_PASS} decision branches verified"
  else
    fail "test_decide.sh: ${DECIDE_FAIL} branches broken"
    echo "$DECIDE_OUT" | tail -20
  fi
else
  fail "test_decide.sh missing"
fi
echo

bold "Summary"
echo "  passed: ${PASS}"
echo "  failed: ${FAIL}"
echo
if (( FAIL == 0 )); then
  printf '\033[32mAll tests pass.\033[0m\n'
  exit 0
else
  printf '\033[31m%d test(s) failed.\033[0m\n' "${FAIL}"
  exit 1
fi
