# NIGHTLY test suite - Windows port of tests/run.sh.
#
# Builds synthetic fixtures in a temp dir, runs every Python+PowerShell
# component, and asserts they produce expected outputs.
#
#   pwsh -File tests\run.ps1

$ErrorActionPreference = 'Continue'
$PluginDir = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$SrcDir = Join-Path $PluginDir 'src'
$Python = if (Get-Command python -ErrorAction SilentlyContinue) { 'python' } else { 'python3' }

$pass = 0; $fail = 0
function Pass($m) { Write-Host "  [ok] $m" -ForegroundColor Green; $script:pass++ }
function Fail($m) { Write-Host "  [x ] $m" -ForegroundColor Red;   $script:fail++ }
function Section($m) { Write-Host "`n$m" -ForegroundColor White }

$TestDir = New-Item -ItemType Directory -Path (Join-Path $env:TEMP "nightly-tests-$([guid]::NewGuid().ToString('N').Substring(0,8))")
$TestHome = $TestDir.FullName
try {
    $env:HOME_BAK = $env:HOME
    $env:HOME = $TestHome
    $env:USERPROFILE_BAK = $env:USERPROFILE
    $env:USERPROFILE = $TestHome

    foreach ($d in '.claude\nightly\benchmarks','.claude\nightly\experiments','.claude\nightly\reports',
                   '.claude\nightly\logs','.claude\projects\-home-test') {
        New-Item -ItemType Directory -Force -Path (Join-Path $TestHome $d) | Out-Null
    }

    # Synthesize one session JSONL
    $sessionPath = Join-Path $TestHome '.claude\projects\-home-test\synthetic.jsonl'
    & $Python -c @"
import json, sys
msgs = [
    {'type':'user','timestamp':'2026-05-01T10:00:00.000Z','message':{'content':'add a hook that detects design-mode prompts'},'sessionId':'synthetic'},
    {'type':'assistant','timestamp':'2026-05-01T10:00:30.000Z','message':{'content':[{'type':'text','text':'Took the position - gh search first.'}],'usage':{'output_tokens':1200}}},
    {'type':'assistant','timestamp':'2026-05-01T10:01:00.000Z','message':{'content':[{'type':'tool_use','name':'Bash','input':{'command':'gh search repos'}}],'usage':{'output_tokens':200}}},
    {'type':'assistant','timestamp':'2026-05-01T10:03:00.000Z','message':{'content':[{'type':'tool_use','name':'Edit','input':{'file_path':'C:/tmp/hook.sh'}}],'usage':{'output_tokens':500}}},
    {'type':'user','timestamp':'2026-05-01T10:10:00.000Z','message':{'content':'now write a quick research note'},'sessionId':'synthetic'},
    {'type':'assistant','timestamp':'2026-05-01T10:11:00.000Z','message':{'content':[{'type':'text','text':'Synthesis.'}],'usage':{'output_tokens':1500}}},
    {'type':'assistant','timestamp':'2026-05-01T10:13:00.000Z','message':{'content':[{'type':'tool_use','name':'Write','input':{'file_path':'C:/tmp/notes.md'}}],'usage':{'output_tokens':300}}},
]
with open(r'$sessionPath','w') as fh:
    for m in msgs: fh.write(json.dumps(m)+'\n')
"@

    Section 'miner.py'
    & $Python (Join-Path $SrcDir 'miner.py') --quiet --projects-dir (Join-Path $TestHome '.claude\projects') --out (Join-Path $TestHome '.claude\nightly\corpus.jsonl')
    if ($LASTEXITCODE -eq 0) {
        $n = (Get-Content (Join-Path $TestHome '.claude\nightly\corpus.jsonl') | Measure-Object -Line).Lines
        if ($n -gt 0) { Pass "extracted $n tasks" } else { Fail 'extracted 0 tasks' }
    } else { Fail 'miner crashed' }

    Section 'benchmark.py'
    & $Python (Join-Path $SrcDir 'benchmark.py') --quiet --corpus (Join-Path $TestHome '.claude\nightly\corpus.jsonl') --out-dir (Join-Path $TestHome '.claude\nightly\benchmarks') --size 2 --seed 1
    if ((Test-Path (Join-Path $TestHome '.claude\nightly\benchmark.jsonl'))) { Pass 'benchmark built' } else { Fail 'benchmark missing' }

    Section 'strategy_stats.py'
    New-Item -ItemType File -Path (Join-Path $TestHome '.claude\nightly\experiment-log.jsonl') -Force | Out-Null
    $stats = & $Python (Join-Path $SrcDir 'strategy_stats.py') --json 2>$null
    if ($stats) { Pass 'strategy_stats handles empty log' } else { Fail 'strategy_stats failed on empty log' }

    Section 'safety_check.py (forbidden paths)'
    & $Python (Join-Path $SrcDir 'safety_check.py') --target '.gitignore' 2>$null
    if ($LASTEXITCODE -ne 0) { Pass 'rejects .gitignore' } else { Fail 'accepted .gitignore' }
    & $Python (Join-Path $SrcDir 'safety_check.py') --target 'plugins/foo' 2>$null
    if ($LASTEXITCODE -ne 0) { Pass 'rejects plugins/' } else { Fail 'accepted plugins/' }
    & $Python (Join-Path $SrcDir 'safety_check.py') --target 'projects/anything' 2>$null
    if ($LASTEXITCODE -ne 0) { Pass 'rejects projects/' } else { Fail 'accepted projects/' }

    Section 'weekly_rollup.py'
    & $Python (Join-Path $SrcDir 'weekly_rollup.py') --days 7 *>$null
    if ($LASTEXITCODE -eq 0) { Pass 'weekly_rollup runs' } else { Fail 'weekly_rollup crashed' }

    Section 'python syntax (py_compile)'
    foreach ($py in Get-ChildItem -Path $SrcDir -Filter '*.py') {
        & $Python -m py_compile $py.FullName 2>$null
        if ($LASTEXITCODE -eq 0) { Pass "$($py.Name) compiles" } else { Fail "$($py.Name) syntax error" }
    }

    Section 'Summary'
    Write-Host "  passed: $pass"
    Write-Host "  failed: $fail"
    Write-Host ''
    if ($fail -eq 0) { Write-Host 'All tests pass.' -ForegroundColor Green; exit 0 }
    else             { Write-Host "$fail test(s) failed." -ForegroundColor Red; exit 1 }
}
finally {
    $env:HOME = $env:HOME_BAK
    $env:USERPROFILE = $env:USERPROFILE_BAK
    Remove-Item -Recurse -Force $TestHome -ErrorAction SilentlyContinue
}
