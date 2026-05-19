# NIGHTLY pre-run snapshot - PowerShell port of snapshot.sh.
#
# Commits ONLY the append-only / auto-generated files that may have drifted
# during the day. Anything else dirty is treated as real WIP and blocks the
# snapshot.
#
# Idempotent. Safe to run before every /nightly invocation.

$ErrorActionPreference = 'Stop'
$ClaudeDir = Join-Path $HOME '.claude'

Set-Location $ClaudeDir
if (-not (Test-Path '.git')) {
    Write-Error 'snapshot: not a git repo - run nightly/install.ps1 first'
    exit 2
}

# Auto-snapshotted paths (relative to ~/.claude). Anything outside this list
# that is dirty will block the snapshot.
$Autosafe = @(
    'memory/',
    'corrections.jsonl',
    'session-state.md',
    'nightly/experiment-log.jsonl',
    'nightly/dead-letter.jsonl',
    'nightly/reports/',
    '.last-cleanup'
)

# --untracked-files=all so untracked dirs expand to individual files
$status = git status --porcelain --untracked-files=all
if ([string]::IsNullOrWhiteSpace($status)) {
    Write-Host 'snapshot: clean tree, nothing to do'
    exit 0
}

$dirty = $status -split "`n" | ForEach-Object {
    $line = $_.Trim()
    if ($line) {
        # `XY path` - take everything after the first space
        $parts = $line -split '\s+', 2
        if ($parts.Length -eq 2) { $parts[1] }
    }
}

$unsafe = @()
foreach ($f in $dirty) {
    $match = $false
    foreach ($pat in $Autosafe) {
        if ($f -like "$pat*") { $match = $true; break }
    }
    if (-not $match) { $unsafe += $f }
}

if ($unsafe.Count -gt 0) {
    Write-Host 'snapshot: refusing to commit - unexpected dirty files (not in autosnap allowlist):' -ForegroundColor Yellow
    foreach ($u in $unsafe) { Write-Host "  - $u" -ForegroundColor Yellow }
    Write-Host 'Inspect with: cd ~/.claude && git status' -ForegroundColor Yellow
    exit 3
}

# Commit just the autosafe paths
git add -- $Autosafe 2>$null
$staged = git diff --staged --quiet; $exitCode = $LASTEXITCODE
if ($exitCode -eq 0) {
    Write-Host 'snapshot: nothing staged after filtering'
    exit 0
}

git -c user.name=nightly-snapshot -c user.email=nightly@localhost commit -q -m @'
nightly: auto-snapshot memory + corrections before run

These paths are append-only during normal Claude Code use. Committed before
a nightly experiment so the loop has a clean baseline. Triggered by
nightly/snapshot.ps1.
'@
$sha = git rev-parse --short HEAD
Write-Host "snapshot: committed $sha"
