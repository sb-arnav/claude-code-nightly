# NIGHTLY SessionStart hook (PowerShell port).
#
# Prints one-screen status when there's an unread morning report.
# Marks it read after first surface so it only shows once.
# Silent (no output) otherwise.

$ErrorActionPreference = 'SilentlyContinue'
$ReportsDir = Join-Path $HOME '.claude\nightly\reports'
$ExpLog     = Join-Path $HOME '.claude\nightly\experiment-log.jsonl'

if (-not (Test-Path $ReportsDir)) { exit 0 }

$latest = Get-ChildItem -Path $ReportsDir -Filter '*.md' -ErrorAction SilentlyContinue |
          Where-Object { -not $_.Name.StartsWith('weekly-') } |
          Sort-Object Name | Select-Object -Last 1
if (-not $latest) { exit 0 }

$readMarker = "$($latest.FullName).read"
if (Test-Path $readMarker) { exit 0 }

$summary = ''
if (Test-Path $ExpLog) {
    $last = Get-Content $ExpLog -Tail 1
    if ($last) {
        try {
            $o = $last | ConvertFrom-Json
            $delta = if ($null -ne $o.delta) { "  d{0:+0.000;-0.000;0.000}" -f $o.delta } else { '' }
            $summary = "$($o.run_id) - $($o.decision) - $($o.strategy)$delta"
        } catch {}
    }
}

Write-Host '=== NIGHTLY ==='
Write-Host "new report: $($latest.Name)"
if ($summary) { Write-Host "last run: $summary" }
Write-Host "read with: cat $($latest.FullName)"
Write-Host 'review proposed (observation mode): claude -p ''/nightly list-proposals'''
Write-Host '=== END ==='

New-Item -ItemType File -Path $readMarker -Force | Out-Null
exit 0
