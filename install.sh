#!/usr/bin/env bash
# NIGHTLY installer — works on macOS, Linux, WSL.
#
# Run from anywhere:
#   bash <(curl -sSL https://raw.githubusercontent.com/sb-arnav/claude-code-nightly/main/install.sh)
#
# Or after cloning:
#   bash ~/.claude/plugins/nightly/install.sh
#
# This script:
#   1. Verifies prerequisites (python3, git, claude CLI, bash)
#   2. Sets up the data directory at ~/.claude/nightly/
#   3. Registers the SessionStart hook in settings.json (via Claude Code's
#      plugin manifest if installed via plugin marketplace; otherwise edits
#      settings.json directly with the user's confirmation)
#   4. Initializes ~/.claude as a git repo with a tight .gitignore
#   5. Builds the initial corpus + benchmark + bootstrap baseline
#   6. Detects OS and prints the right scheduling instructions
#
# Safe to re-run: every step is idempotent.

set -euo pipefail

# Resolve plugin root (the directory this script lives in)
PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE_DIR="${HOME}/.claude"
DATA_DIR="${CLAUDE_DIR}/nightly"
SRC="${PLUGIN_DIR}/src"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
info() { printf '  • %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
fail() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

bold "NIGHTLY installer"
echo "  plugin: ${PLUGIN_DIR}"
echo "  data:   ${DATA_DIR}"
echo

# ----------------------------------------------------------------------------
# 1. Prerequisites
# ----------------------------------------------------------------------------
bold "[1/6] Checking prerequisites"
MISSING=()
for cmd in python3 git bash; do
  if ! command -v "$cmd" >/dev/null 2>&1; then MISSING+=("$cmd"); else ok "$cmd"; fi
done
if ! command -v claude >/dev/null 2>&1; then
  warn "claude CLI not found in PATH — the loop still installs, but won't run until 'claude' is available"
else
  ok "claude CLI"
fi
if [[ ${#MISSING[@]} -gt 0 ]]; then
  fail "missing required commands: ${MISSING[*]}"
  exit 1
fi
echo

# ----------------------------------------------------------------------------
# 2. Data directory + plugin symlinks
# ----------------------------------------------------------------------------
bold "[2/6] Data directory at ${DATA_DIR}"
mkdir -p "${DATA_DIR}"/{logs,reports,experiments,benchmarks,proposed}
ok "created data directories"

# The loop's agent + slash command + baseline.py reference scripts at
# ~/.claude/nightly/<name> (a stable user-data path), but the source files
# live under ${PLUGIN_DIR}/src/. Symlink them in so both lookup paths resolve.
# Re-running install replaces stale symlinks with -sf.
for f in "${SRC}"/*.py "${SRC}"/*.sh; do
  [[ -e "$f" ]] || continue
  ln -sf "$f" "${DATA_DIR}/$(basename "$f")"
done
ok "symlinked $(ls ${SRC}/*.py ${SRC}/*.sh 2>/dev/null | wc -l) plugin scripts into ${DATA_DIR}/"
echo

# ----------------------------------------------------------------------------
# 3. Hook registration
# ----------------------------------------------------------------------------
bold "[3/6] SessionStart hook"
SETTINGS="${CLAUDE_DIR}/settings.json"
HOOK_CMD="bash \"${PLUGIN_DIR}/hooks/nightly-surface.sh\""

if [[ -f "${PLUGIN_DIR}/.claude-plugin/plugin.json" ]] \
   && [[ "${PLUGIN_DIR}" == "${CLAUDE_DIR}/plugins/"* ]]; then
  ok "installed as a plugin under ~/.claude/plugins/ — hook is registered via plugin.json automatically"
elif [[ -f "${SETTINGS}" ]]; then
  if grep -q "nightly-surface.sh" "${SETTINGS}" 2>/dev/null; then
    ok "hook already registered in settings.json"
  else
    info "settings.json found but hook not registered."
    info "Add this entry to the 'SessionStart' array in ${SETTINGS}:"
    cat <<JSON
    {
      "hooks": [
        { "type": "command", "command": "${HOOK_CMD}" }
      ]
    }
JSON
    info "Or run: claude config set hook SessionStart 'bash ${PLUGIN_DIR}/hooks/nightly-surface.sh'"
  fi
else
  warn "settings.json not found at ${SETTINGS}; skipping hook registration"
fi
echo

# ----------------------------------------------------------------------------
# 4. Git init for ~/.claude
# ----------------------------------------------------------------------------
bold "[4/6] Substrate git repo at ${CLAUDE_DIR}"
cd "${CLAUDE_DIR}"
if [[ ! -d .git ]]; then
  git init -q
  ok "git init"
else
  ok "git repo already initialized"
fi

GITIGNORE_PATH="${CLAUDE_DIR}/.gitignore"
NIGHTLY_MARK="# nightly:managed"
if ! grep -q "${NIGHTLY_MARK}" "${GITIGNORE_PATH}" 2>/dev/null; then
  cat >> "${GITIGNORE_PATH}" <<GITIGNORE

${NIGHTLY_MARK} — do not edit between markers; nightly install regenerates this block
# volatile session/cache state (rewritten every Claude Code session)
projects/
todos/
sessions/
tasks/
shell-snapshots/
file-history/
paste-cache/
session-env/
history.jsonl
learning/

# caches / telemetry
cache/
downloads/
backups/
telemetry/
statsig/
ide/
.credentials.json
mcp-needs-auth-cache.json
security_warnings_state_*.json
*.bak.*

# plugins are re-installable; not tracked as substrate
plugins/

# nightly per-run scratch (reports + experiment-log kept, big artifacts ignored)
nightly/experiments/
nightly/logs/
nightly/corpus.jsonl
nightly/benchmark.jsonl
nightly/benchmarks/
# plugin sources symlinked into nightly/ (sources live in plugins/ which is ignored)
nightly/miner.py
nightly/benchmark.py
nightly/scorer.py
nightly/baseline.py
nightly/disapprove.py
nightly/snapshot.sh
nightly/strategy_stats.py
nightly/safety_check.py
nightly/weekly_rollup.py
nightly/approve.py
nightly/reject.py
nightly/replay.py
nightly/judge.py
nightly/variance.py
nightly/proposed/

# misc
*.tmp
*.swp
.DS_Store
GITIGNORE
  ok "appended nightly gitignore block"
else
  ok ".gitignore already has nightly block"
fi

if ! git rev-parse HEAD >/dev/null 2>&1; then
  git add -A
  git -c user.name="nightly" -c user.email="nightly@localhost" \
      commit -q -m "nightly: initial substrate snapshot"
  ok "initial commit"
else
  ok "already has commits"
fi
echo

# ----------------------------------------------------------------------------
# 5. Build initial corpus + benchmark + baseline
# ----------------------------------------------------------------------------
bold "[5/6] Mining your session history"
if [[ ! -f "${DATA_DIR}/corpus.jsonl" ]] || [[ -n "$(find "${DATA_DIR}/corpus.jsonl" -mtime +7 2>/dev/null)" ]]; then
  python3 "${SRC}/miner.py" --quiet
  ok "corpus built ($(wc -l < "${DATA_DIR}/corpus.jsonl") tasks)"
else
  ok "corpus exists, less than 7 days old"
fi
if [[ ! -L "${DATA_DIR}/benchmark.jsonl" ]] && [[ ! -f "${DATA_DIR}/benchmark.jsonl" ]]; then
  python3 "${SRC}/benchmark.py" --quiet
  ok "benchmark built ($(wc -l < "${DATA_DIR}/benchmark.jsonl") tasks)"
else
  ok "benchmark exists"
fi
if [[ ! -f "${DATA_DIR}/experiment-log.jsonl" ]]; then
  python3 "${SRC}/baseline.py" >/dev/null
  ok "bootstrap baseline seeded"
else
  ok "experiment-log exists"
fi
echo

# ----------------------------------------------------------------------------
# 6. Scheduling instructions (OS-specific)
# ----------------------------------------------------------------------------
bold "[6/6] Scheduling — pick ONE option below"

OS_KIND=$(uname -s)
case "${OS_KIND}" in
  Linux*)
    # Detect WSL vs native
    if grep -qi microsoft /proc/version 2>/dev/null; then OS_KIND="WSL"; fi ;;
  Darwin*) OS_KIND="macOS" ;;
esac

echo
echo "  ── Option A: ${OS_KIND} cron (simplest, runs locally) ──"
echo "     Run \`crontab -e\` and add this line:"
echo
case "${OS_KIND}" in
  macOS|Linux|WSL)
    echo "        # NIGHTLY at 22:00 local time"
    echo "        0 22 * * * cd \$HOME && claude -p '/nightly' >> ${DATA_DIR}/logs/cron.log 2>&1"
    echo
    echo "     And add a weekly rollup every Sunday at 09:00:"
    echo "        0 9 * * 0 python3 ${PLUGIN_DIR}/src/weekly_rollup.py >> ${DATA_DIR}/logs/rollup.log 2>&1"
    ;;
  *)
    echo "        # NIGHTLY at 22:00 (verify cron syntax for your platform)"
    echo "        0 22 * * * cd \$HOME && claude -p '/nightly' >> ${DATA_DIR}/logs/cron.log 2>&1"
    echo "        0 9 * * 0 python3 ${PLUGIN_DIR}/src/weekly_rollup.py >> ${DATA_DIR}/logs/rollup.log 2>&1"
    ;;
esac
echo
if [[ "${OS_KIND}" == "macOS" ]]; then
  echo "  ── Option B: macOS launchd (survives reboots, no terminal needed) ──"
  echo "     The plugin includes a sample launchd plist at:"
  echo "        ${PLUGIN_DIR}/sched/com.nightly.plist"
  echo "     Install with:"
  echo "        cp '${PLUGIN_DIR}/sched/com.nightly.plist' ~/Library/LaunchAgents/"
  echo "        launchctl load ~/Library/LaunchAgents/com.nightly.plist"
  echo
fi
echo "  ── Option C: Claude Code /schedule skill (cloud, no cron needed) ──"
echo "     If your Claude plan includes remote agents, type in any Claude Code session:"
echo "        /schedule add nightly '0 22 * * *' /nightly"
echo
echo "  ── Option D: GitHub Actions (cron in the cloud, free tier OK) ──"
echo "     See ${PLUGIN_DIR}/sched/github-action.yml for a copy-paste workflow."
echo

bold "Done."
echo "  • Test the loop right now (no token spend):"
echo "      claude -p '/nightly --dry-run'"
echo "  • Status check anytime:"
echo "      claude -p '/nightly status'"
echo "  • If you disagree with a kept change tomorrow:"
echo "      claude -p '/nightly disapprove <run_id> \"<your reason>\"'"
echo
echo "  Reports land at ${DATA_DIR}/reports/YYYY-MM-DD.md"
