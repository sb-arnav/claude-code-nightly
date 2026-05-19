# NIGHTLY - post-install verification (Windows PowerShell port of verify.sh).
#
# Runs the full dry-run pipeline against YOUR machine, with YOUR data.
# Doesn't spend tokens - only exercises the local Python/PowerShell side.
#
#   pwsh -File $HOME\.claude\plugins\nightly\verify.ps1
#
# Exit 0 = ready to schedule. Exit non-zero = something's wrong.

$ErrorActionPreference = 'Continue'
$PluginDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir   = Join-Path $HOME '.claude\nightly'
$SrcDir    = Join-Path $PluginDir 'src'
$Python    = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }

$pass = 0; $fail = 0
function Pass($m) { Write-Host "  [ok] $m" -ForegroundColor Green; $script:pass++ }
function Fail($m) { Write-Host "  [x ] $m" -ForegroundColor Red;   $script:fail++ }
function Section($m) { Write-Host "`n$m" -ForegroundColor White }

Section 'Files'
$expected = @('miner.py','benchmark.py','scorer.py','baseline.py','disapprove.py','approve.py',
              'reject.py','strategy_stats.py','safety_check.py','weekly_rollup.py')
foreach ($f in $expected) {
    if (Test-Path (Join-Path $SrcDir $f)) { Pass $f } else { Fail "$f missing" }
}
foreach ($f in 'snapshot.sh','snapshot.ps1') {
    if (Test-Path (Join-Path $SrcDir $f)) { Pass $f } else { Fail "$f missing" }
}
if (Test-Path $DataDir) { Pass "data dir $DataDir" } else { Fail 'data dir missing' }

Section 'Corpus'
$corpus = Join-Path $DataDir 'corpus.jsonl'
if (Test-Path $corpus) {
    $n = (Get-Content $corpus | Measure-Object -Line).Lines
    if ($n -gt 0) { Pass "corpus.jsonl ($n tasks)" } else { Fail 'corpus.jsonl is empty' }
} else {
    Fail 'corpus.jsonl not found - run miner.py'
}

Section 'Benchmark'
$bench = Join-Path $DataDir 'benchmark.jsonl'
if (Test-Path $bench) {
    $replayable = & $Python -c @"
import json
with open(r'$bench') as fh:
    print(sum(1 for l in fh if l.strip() and json.loads(l).get('replayable')))
"@
    if ([int]$replayable -gt 5) { Pass "benchmark.jsonl ($replayable replayable)" }
    elseif ([int]$replayable -gt 0) { Fail "benchmark.jsonl has only $replayable replayable tasks" }
    else { Fail 'benchmark.jsonl has 0 replayable tasks' }
} else {
    Fail 'benchmark.jsonl not found'
}

Section 'Scorer'
$tmp = New-TemporaryFile | ForEach-Object { Remove-Item $_; New-Item -ItemType Directory -Path $_ }
& $Python -c @"
import json
from pathlib import Path
bench = Path(r'$bench')
out = Path(r'$($tmp.FullName)')
with bench.open() as fh:
    for line in fh:
        line=line.strip()
        if not line: continue
        e = json.loads(line)
        if not e.get('replayable'): continue
        gt = e['ground_truth']
        (out / f'{e[\"benchmark_id\"]}.json').write_text(json.dumps({
            'benchmark_id': e['benchmark_id'],
            'duration_sec': gt['duration_sec'],
            'output_tokens': gt['output_tokens'],
            'response_text': '(verify)',
            'tools': gt['tools'],
            'files_changed': [None]*gt['files_changed_count'],
            'tool_call_sequence': list(gt['tools'].keys()),
            'completed_cleanly': gt['outcome'] in ('completed','corrected'),
            'correction_hook_fired': gt['correction_logged'],
        }))
"@ 2>$null
$scoreJson = & $Python (Join-Path $SrcDir 'scorer.py') --run-dir $tmp.FullName 2>$null
if ($scoreJson) {
    $mean = ($scoreJson | ConvertFrom-Json).score_mean
    if ($null -ne $mean) { Pass "scorer composed (mean=$mean)" } else { Fail 'scorer ran but no score' }
} else {
    Fail 'scorer crashed'
}
Remove-Item -Recurse -Force $tmp.FullName -ErrorAction SilentlyContinue

Section 'Strategy stats'
$stats = & $Python (Join-Path $SrcDir 'strategy_stats.py') --json 2>$null
if ($stats) {
    $nRuns = ($stats | ConvertFrom-Json).n_total_runs
    Pass "strategy_stats parsed ($nRuns runs in log)"
} else { Fail 'strategy_stats failed' }

Section 'Safety check'
& $Python (Join-Path $SrcDir 'safety_check.py') --target '.gitignore' 2>$null
if ($LASTEXITCODE -ne 0) { Pass 'safety_check rejects forbidden target' }
else { Fail 'safety_check accepted forbidden target' }

Section 'Snapshot'
$snapPs1 = Join-Path $SrcDir 'snapshot.ps1'
if (Test-Path $snapPs1) {
    $snapOut = & pwsh -NoProfile -File $snapPs1 2>&1
    if ($snapOut -match 'clean tree|committed|nothing staged') { Pass 'snapshot.ps1 ran cleanly' }
    else { Fail 'snapshot.ps1 failed' }
} else {
    Fail 'snapshot.ps1 missing'
}

Section 'Weekly rollup'
$null = & $Python (Join-Path $SrcDir 'weekly_rollup.py') --days 7 2>$null
if ($LASTEXITCODE -eq 0) { Pass 'weekly_rollup rendered' } else { Fail 'weekly_rollup crashed' }

Section 'Summary'
Write-Host "  passed: $pass"
Write-Host "  failed: $fail"
Write-Host ''
if ($fail -eq 0) {
    Write-Host 'NIGHTLY is ready.' -ForegroundColor Green
    Write-Host 'Schedule via Task Scheduler, /schedule, or GitHub Actions.'
    exit 0
} else {
    Write-Host "$fail check(s) failed." -ForegroundColor Red
    exit 1
}
