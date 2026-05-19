# NIGHTLY installer — Windows PowerShell port.
#
# Run from anywhere (after git clone):
#   pwsh -ExecutionPolicy Bypass -File $HOME\.claude\plugins\nightly\install.ps1
#
# Or in classic PowerShell (5.1+):
#   powershell -ExecutionPolicy Bypass -File $HOME\.claude\plugins\nightly\install.ps1
#
# This mirrors install.sh but uses file copies instead of symlinks (Windows
# symlinks need admin or Developer Mode; copies are reliable everywhere).
# Idempotent — every step skips when already done.

$ErrorActionPreference = 'Stop'

$PluginDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ClaudeDir = Join-Path $HOME '.claude'
$DataDir   = Join-Path $ClaudeDir 'nightly'
$SrcDir    = Join-Path $PluginDir 'src'

function Write-Bold($Text) { Write-Host $Text -ForegroundColor White }
function Write-Ok($Text)   { Write-Host "  [ok] $Text" -ForegroundColor Green }
function Write-Info($Text) { Write-Host "  -   $Text" }
function Write-Warn($Text) { Write-Host "  [!] $Text" -ForegroundColor Yellow }
function Write-Fail($Text) { Write-Host "  [x] $Text" -ForegroundColor Red }

Write-Bold 'NIGHTLY installer (Windows / PowerShell)'
Write-Host "  plugin: $PluginDir"
Write-Host "  data:   $DataDir"
Write-Host ''

# ----------------------------------------------------------------------------
# 1. Prerequisites
# ----------------------------------------------------------------------------
Write-Bold '[1/6] Checking prerequisites'
$missing = @()
foreach ($cmd in 'python', 'git') {
    if (-not (Get-Command $cmd -ErrorAction SilentlyContinue)) {
        # try python3 alias
        if ($cmd -eq 'python' -and (Get-Command 'python3' -ErrorAction SilentlyContinue)) {
            Write-Ok 'python3 (will use as python)'
            continue
        }
        $missing += $cmd
    } else {
        Write-Ok $cmd
    }
}
if (-not (Get-Command 'claude' -ErrorAction SilentlyContinue)) {
    Write-Warn 'claude CLI not in PATH — loop installs, but cron/Task Scheduler runs need it'
} else {
    Write-Ok 'claude CLI'
}
if ($missing.Count -gt 0) {
    Write-Fail "missing required commands: $($missing -join ', ')"
    exit 1
}
Write-Host ''

# Pick the python invocation that exists.
$Python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }

# ----------------------------------------------------------------------------
# 2. Data directory + plugin copies (NOT symlinks — Windows needs Dev Mode)
# ----------------------------------------------------------------------------
Write-Bold "[2/6] Data directory at $DataDir"
foreach ($sub in 'logs', 'reports', 'experiments', 'benchmarks', 'proposed') {
    New-Item -ItemType Directory -Force -Path (Join-Path $DataDir $sub) | Out-Null
}
Write-Ok 'created data directories'

# Copy plugin scripts into the data dir so the stable lookup paths
# (e.g. $HOME\.claude\nightly\scorer.py from baseline.py's subprocess call)
# resolve. Copy is safer than symlink on Windows. Re-runs overwrite.
$copied = 0
foreach ($f in Get-ChildItem -Path $SrcDir -File) {
    Copy-Item -Path $f.FullName -Destination (Join-Path $DataDir $f.Name) -Force
    $copied++
}
Write-Ok "copied $copied plugin scripts into $DataDir"
Write-Host ''

# ----------------------------------------------------------------------------
# 3. SessionStart hook
# ----------------------------------------------------------------------------
Write-Bold '[3/6] SessionStart hook'
$manifest = Join-Path $PluginDir '.claude-plugin\plugin.json'
if ((Test-Path $manifest) -and ($PluginDir -like "$ClaudeDir\plugins\*")) {
    Write-Ok 'installed as a plugin under ~/.claude/plugins/ — hook registered via plugin.json automatically'
} else {
    Write-Info "settings.json hook can be registered manually; see README"
}
Write-Host ''

# ----------------------------------------------------------------------------
# 4. Substrate git repo at ~/.claude
# ----------------------------------------------------------------------------
Write-Bold "[4/6] Substrate git repo at $ClaudeDir"
Push-Location $ClaudeDir
try {
    if (-not (Test-Path '.git')) {
        git init -q
        Write-Ok 'git init'
    } else {
        Write-Ok 'git repo already initialized'
    }

    $gitignorePath = Join-Path $ClaudeDir '.gitignore'
    $marker = '# nightly:managed'
    $needsAppend = $true
    if (Test-Path $gitignorePath) {
        $existing = Get-Content $gitignorePath -Raw
        if ($existing -match [regex]::Escape($marker)) { $needsAppend = $false }
    }
    if ($needsAppend) {
        @"

$marker - do not edit between markers; nightly install regenerates this block
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

# plugins are re-installable
plugins/

# nightly per-run scratch
nightly/experiments/
nightly/logs/
nightly/corpus.jsonl
nightly/benchmark.jsonl
nightly/benchmarks/
# plugin script copies in nightly/ (sources live in plugins/ which is ignored)
nightly/miner.py
nightly/benchmark.py
nightly/scorer.py
nightly/baseline.py
nightly/disapprove.py
nightly/snapshot.sh
nightly/snapshot.ps1
nightly/strategy_stats.py
nightly/safety_check.py
nightly/weekly_rollup.py
nightly/approve.py
nightly/reject.py
nightly/proposed/

# misc
*.tmp
*.swp
.DS_Store
"@ | Add-Content -Path $gitignorePath
        Write-Ok 'appended nightly gitignore block'
    } else {
        Write-Ok '.gitignore already has nightly block'
    }

    $hasCommits = $true
    try { git rev-parse HEAD 2>$null | Out-Null } catch { $hasCommits = $false }
    if (-not $hasCommits) {
        git add -A
        git -c user.name=nightly -c user.email=nightly@localhost commit -q -m 'nightly: initial substrate snapshot'
        Write-Ok 'initial commit'
    } else {
        Write-Ok 'already has commits'
    }
} finally {
    Pop-Location
}
Write-Host ''

# ----------------------------------------------------------------------------
# 5. Mine session history + build benchmark + seed baseline
# ----------------------------------------------------------------------------
Write-Bold "[5/6] Mining your session history"
$corpus = Join-Path $DataDir 'corpus.jsonl'
$needsMine = $true
if (Test-Path $corpus) {
    $age = (Get-Date) - (Get-Item $corpus).LastWriteTime
    if ($age.Days -le 7) { $needsMine = $false }
}
if ($needsMine) {
    & $Python (Join-Path $SrcDir 'miner.py') --quiet
    $lines = if (Test-Path $corpus) { (Get-Content $corpus | Measure-Object -Line).Lines } else { 0 }
    Write-Ok "corpus built ($lines tasks)"
} else {
    Write-Ok 'corpus exists, less than 7 days old'
}

$bench = Join-Path $DataDir 'benchmark.jsonl'
if (-not (Test-Path $bench)) {
    & $Python (Join-Path $SrcDir 'benchmark.py') --quiet
    Write-Ok 'benchmark built'
} else {
    Write-Ok 'benchmark exists'
}

$expLog = Join-Path $DataDir 'experiment-log.jsonl'
if (-not (Test-Path $expLog)) {
    & $Python (Join-Path $SrcDir 'baseline.py') | Out-Null
    Write-Ok 'bootstrap baseline seeded'
} else {
    Write-Ok 'experiment-log exists'
}
Write-Host ''

# ----------------------------------------------------------------------------
# 6. Scheduling instructions (Windows = Task Scheduler)
# ----------------------------------------------------------------------------
Write-Bold '[6/6] Scheduling - pick ONE option below'
Write-Host ''
Write-Host '  -- Option A: Windows Task Scheduler (simplest, runs locally) --'
Write-Host '     Open an admin PowerShell and run:'
Write-Host ''
$claudePath = (Get-Command claude -ErrorAction SilentlyContinue).Source
if (-not $claudePath) { $claudePath = 'claude' }
Write-Host @"
        `$Action = New-ScheduledTaskAction -Execute '$claudePath' -Argument '-p ''/nightly'''
        `$Trigger = New-ScheduledTaskTrigger -Daily -At '10:00PM'
        Register-ScheduledTask -TaskName 'NIGHTLY' -Action `$Action -Trigger `$Trigger -RunLevel Highest
"@
Write-Host ''
Write-Host '  -- Option B: GitHub Actions (cron in the cloud, free tier OK) --'
Write-Host "     See $PluginDir\sched\github-action.yml for a copy-paste workflow."
Write-Host ''
Write-Host '  -- Option C: Claude Code /schedule skill (cloud, no Task Scheduler needed) --'
Write-Host '     If your Claude plan includes remote agents, type in any Claude Code session:'
Write-Host "        /schedule add nightly '0 22 * * *' /nightly"
Write-Host ''
Write-Bold 'Done.'
Write-Host '  - Test the loop right now (no token spend):'
Write-Host "      claude -p '/nightly --dry-run'"
Write-Host '  - Status check anytime:'
Write-Host "      claude -p '/nightly status'"
Write-Host '  - Run the verifier to confirm install:'
Write-Host "      pwsh -File $PluginDir\verify.ps1"
Write-Host ''
Write-Host "  Reports land at $DataDir\reports\YYYY-MM-DD.md"
