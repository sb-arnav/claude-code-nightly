#!/usr/bin/env bash
# Unit tests for decide.py — exercise every decision branch with synthetic
# score/judge/variance/corrections fixtures.
#
# Runs against a tmpdir so it doesn't touch the user's real ~/.claude/.
# Asserts the returned `decision` string matches the expected branch.

set -u
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DECIDE="${PLUGIN_DIR}/src/decide.py"

PASS=0
FAIL=0
ok()   { printf '  \033[32m[ok]\033[0m %s\n' "$*"; PASS=$((PASS+1)); }
fail() { printf '  \033[31m[xx]\033[0m %s\n' "$*"; FAIL=$((FAIL+1)); }

TMP=$(mktemp -d -t decide-tests-XXXXXX)
trap "rm -rf ${TMP}" EXIT

setup_run() {
    local name="$1"
    local rdir="${TMP}/${name}"
    mkdir -p "${rdir}/responses"
    cat > "${TMP}/${name}.log" <<JSONL
{"run_id":"base-0","ts":"2026-05-01T00:00:00+00:00","decision":"kept","strategy":"x","score_mean":0.85,"new_commit":"abc"}
JSONL
    echo "${rdir}"
}

write_score()      { echo "{\"score_mean\":$2}" > "$1/score.json"; }
write_judge()      { echo "{\"judge_composite\":$2,\"n_failed\":${3:-0}}" > "$1/judge-scores.json"; }
write_variance()   { echo "{\"noise_threshold_1_5_sigma\":$2}" > "$1/variance.json"; }
write_corrections(){ echo "{\"n_matched\":$2,\"corrections_composite\":$3}" > "$1/corrections-score.json"; }

run_decide() {
    local rdir="$1"; shift
    local logname=$(basename "$rdir")
    python3 "${DECIDE}" --run-dir "${rdir}" --exp-log "${TMP}/${logname}.log" "$@" 2>/dev/null
}

assert_decision() {
    local out="$1" expected="$2" label="$3"
    local got=$(echo "$out" | python3 -c "import sys,json; print(json.load(sys.stdin).get('decision'))" 2>/dev/null)
    if [[ "$got" == "$expected" ]]; then ok "$label → $got"
    else fail "$label: expected $expected, got $got"; fi
}

# 1. Δ > floor, all gates pass → kept
rd=$(setup_run "t1_kept")
write_score "$rd" 0.90
write_judge "$rd" 0.75 0
write_variance "$rd" 0.01
write_corrections "$rd" 2 0.7
assert_decision "$(run_decide "$rd" --mode auto-commit)" "kept" "all gates pass"

# 2. Δ < -floor → reverted
rd=$(setup_run "t2_reverted")
write_score "$rd" 0.80
assert_decision "$(run_decide "$rd" --mode auto-commit --skip-judge --skip-variance)" "reverted" "Δ below revert threshold"

# 3. |Δ| < floor → delta-below-floor
rd=$(setup_run "t3_held")
write_score "$rd" 0.86
assert_decision "$(run_decide "$rd" --mode auto-commit --skip-judge --skip-variance)" "delta-below-floor" "marginal Δ"

# 4. variance floor blocks marginal-pass
rd=$(setup_run "t4_noise")
write_score "$rd" 0.88
write_variance "$rd" 0.05
assert_decision "$(run_decide "$rd" --mode auto-commit --skip-judge)" "noise-rejected" "Δ within sampling noise"

# 5. judge composite below floor
rd=$(setup_run "t5_judge")
write_score "$rd" 0.90
write_judge "$rd" 0.4 0
assert_decision "$(run_decide "$rd" --mode auto-commit --skip-variance)" "judge-rejected" "judge composite below floor"

# 6. too many judge failures
rd=$(setup_run "t6_judge_fail")
write_score "$rd" 0.90
write_judge "$rd" 0.75 3
assert_decision "$(run_decide "$rd" --mode auto-commit --skip-variance)" "judge-rejected" "too many judge failures"

# 7. corrections misaligned (response closer to what_i_did)
rd=$(setup_run "t7_corr")
write_score "$rd" 0.90
write_judge "$rd" 0.75 0
write_variance "$rd" 0.01
write_corrections "$rd" 3 0.2
assert_decision "$(run_decide "$rd" --mode auto-commit)" "corrections-misaligned" "response wrong-direction vs labels"

# 8. corrections gate vacuous when no matches
rd=$(setup_run "t8_corr_none")
write_score "$rd" 0.90
write_judge "$rd" 0.75 0
write_variance "$rd" 0.01
write_corrections "$rd" 0 0
assert_decision "$(run_decide "$rd" --mode auto-commit)" "kept" "corrections gate vacuous with n_matched=0"

# 9. observation + would-keep → proposed-kept
rd=$(setup_run "t9_obs_kept")
write_score "$rd" 0.90
assert_decision "$(run_decide "$rd" --mode observation --skip-judge --skip-variance)" "proposed-kept" "observation mode + would-keep"

# 10. observation + would-revert → proposed-reverted
rd=$(setup_run "t10_obs_revert")
write_score "$rd" 0.86
assert_decision "$(run_decide "$rd" --mode observation --skip-judge --skip-variance)" "proposed-reverted" "observation mode + would-revert"

# 11. No baseline + score >= 0.5 → first-real-baseline
rdir="${TMP}/t11"
mkdir -p "${rdir}/responses"
echo '{"run_id":"seed","ts":"2026-05-01T00:00:00+00:00","decision":"seed","score_mean":0.98}' > "${TMP}/t11.log"
write_score "${rdir}" 0.7
out=$(python3 "${DECIDE}" --run-dir "${rdir}" --exp-log "${TMP}/t11.log" --mode auto-commit --skip-judge --skip-variance 2>/dev/null)
assert_decision "$out" "first-real-baseline" "no baseline, sanity passes"

# 12. No baseline + score < 0.5 → sanity-floor-rejected
rdir="${TMP}/t12"
mkdir -p "${rdir}/responses"
echo '{"run_id":"seed","ts":"2026-05-01T00:00:00+00:00","decision":"seed","score_mean":0.98}' > "${TMP}/t12.log"
write_score "${rdir}" 0.3
out=$(python3 "${DECIDE}" --run-dir "${rdir}" --exp-log "${TMP}/t12.log" --mode auto-commit --skip-judge --skip-variance 2>/dev/null)
assert_decision "$out" "sanity-floor-rejected" "no baseline, below sanity floor"

# 13. Missing score.json → error
rdir="${TMP}/t13"
mkdir -p "${rdir}/responses"
out=$(python3 "${DECIDE}" --run-dir "${rdir}" --exp-log /dev/null --mode auto-commit --skip-judge --skip-variance 2>/dev/null)
assert_decision "$out" "error" "missing score.json"

# 14. Null score_mean → error with helpful pointer
rdir="${TMP}/t14"
mkdir -p "${rdir}/responses"
echo '{"score_mean": null}' > "${rdir}/score.json"
out=$(python3 "${DECIDE}" --run-dir "${rdir}" --exp-log /dev/null --mode auto-commit --skip-judge --skip-variance 2>/dev/null)
assert_decision "$out" "error" "null score_mean"
if echo "$out" | grep -q "n=0 replayable"; then ok "null-score error message points at likely cause"
else fail "null-score error message doesn't mention n=0 replayable"; fi

echo
echo "passed: ${PASS}"
echo "failed: ${FAIL}"
if (( FAIL == 0 )); then exit 0; else exit 1; fi
