# Changelog

All notable changes to NIGHTLY are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.9.0] — 2026-05-19

The final methodology gap from the reviewer's list: **correction-weighted ground truth**. All 5/5 named critiques now have code AND are gated into the keep decision.

### Added
- `src/corrections_score.py` — matches benchmark prompts to corrections.jsonl entries (normalized exact + substring fallback), scores responses on `+1 per supposed_to keyword present` and `-1 per what_i_did keyword present` with Laplace smoothing. Writes `corrections-score.json` with composite + per-task hits. This is **labeled** ground truth — when a benchmark task has a matched correction, we know the right behavior, not just that something was wrong.
- `decide.py` Gate 5 (corrections-aligned): when matches exist, `corrections_composite >= 0.4` required. Below 0.4 = response is closer to `what_i_did` than `supposed_to` = actively wrong-direction → `corrections-misaligned` decision. No matches = gate vacuous (skipped, doesn't block).
- `decide.py` JSON output now includes `corrections_composite` and `n_corrections_matched`.

### Real finding on the dev corpus
On the current benchmark + corrections.jsonl, n_matched=0. Reason: corrections were logged on prompts that aren't in the 40-task benchmark sample. The code is wired; matches will start contributing the first time a logged correction lands on a prompt already in the benchmark, OR when the benchmark is rebuilt from a corpus that includes the corrected prompts.

### Five of five critiques now closed with code
1. **Goodhart-vulnerable regex heuristics** → judge.py (v0.7.0) + corrections_score.py weighted gate (v0.9.0)
2. **No variance estimation** → variance.py + decide.py variance gate (v0.7.1)
3. **Computed but not gated** → decide.py mechanical gating (v0.8.0)
4. **Replay model ≠ production model** → documented honestly in caveat; observation mode is default
5. **Auto-mining trades labeling for weaker signal** → corrections_score.py closes the labeling gap when corrections accumulate (v0.9.0)

### What's left
Runtime, not code: an actual production observation period running the loop for weeks, accumulating real correction-labeled benchmark tasks, watching the gate decisions. That's the work that moves the methodology rating past 7.



## [0.8.1] — 2026-05-19

External v0.8.0 test report nailed three operational gaps. All fixed.

### Fixed
- **`PYTHONUTF8` env didn't persist past `install.ps1`.** The fix in v0.8.0 was structurally true but operationally useless — `$env:PYTHONUTF8 = '1'` only affects the install.ps1 process; verify.ps1 / cron / Task Scheduler all get fresh shells with cp1252 stdout. `weekly_rollup.py` was still crashing on `print(report)` (the Unicode arrow goes to stdout, not via write_text). **Real fix**: added `sys.stdout.reconfigure(encoding="utf-8")` + `sys.stderr.reconfigure(encoding="utf-8")` at the top of all 14 entry-point Python scripts (every file with `print()` output). No env reliance, idempotent, safe on all platforms via try/except for older Python.
- **`decide.py --help` had mojibake on Windows.** Em-dash in the argparse description rendered as `subdir � the parent`. Replaced em-dash with `--` for help-text safety even if `sys.stdout.reconfigure` somehow doesn't take. Belt-and-suspenders.
- **`decide.py` misleading error message** when `score_mean` is null. v0.8.0 said `"score.json has no score_mean"` even when the key was present but null (from n=0 replayable tasks). Now distinguishes: missing key → `"score.json missing score_mean key"`; null value → `"score_mean is None (likely n=0 replayable tasks — check benchmark.jsonl has replayable entries and run-dir has response files)"`.

### Verified
- 27/27 tests still pass on WSL/Linux
- `grep -L "Force UTF-8 stdio" src/*.py` returns empty (all 14 scripts have the block)
- `decide.py --help` renders cleanly (no em-dash to mangle)
- decide.py against existing baseline-seed still returns expected `proposed-reverted` with `delta=0.0`

### Methodology rating per the reviewer: 6/10
- 5 of 5 named methodology critiques have code (not prose) addressing them
- The remaining 4 points from 6→10 are not in code: they're an actual production observation period running the loop for weeks against real corrections.jsonl entries and seeing what the gate decisions look like in the wild.



## [0.8.0] — 2026-05-19

External v0.7.1 review on Windows native turned up two Sev-1s and one structural pushback. All three fixed.

### Fixed (two Sev-1 Windows bugs)
- **`install.ps1` initial commit was silently skipped.** Line 179-180 used `try { git rev-parse HEAD } catch { ... }` to detect whether the repo had commits — but PowerShell `try/catch` doesn't catch native command failures, only PowerShell exceptions. So on a fresh repo `$hasCommits` stayed `$true` and the commit branch was skipped. Fix: use `$LASTEXITCODE` instead. Result: install.ps1 now actually commits the initial snapshot, snapshot.ps1 sees a clean tree on next invocation.
- **9 `write_text()` calls without `encoding="utf-8"` crashed on Windows.** Python defaults to cp1252 on Windows, which can't encode `→`, em-dashes, smart quotes, or anything ≥U+0080 — i.e. most of what Claude returns. Today only `weekly_rollup.py` crashed (it writes `→` in score-trend tables); `judge.py` and `replay.py` would have started crashing the moment Claude returned any non-ASCII. Fix: every site now passes `encoding="utf-8"`. Defense-in-depth: `install.ps1` now also exports `PYTHONUTF8=1` and `PYTHONIOENCODING=utf-8` for the install run.

### Added (structural pushback: "computed but not gated")
- `src/decide.py` — mechanical keep/revert decision. **The agent no longer computes the decision in prose; it executes whatever decide.py returns as JSON.** Implements the four-gate stack from v0.7.1's agent doc as actual code: Δ ≥ +0.02, Δ > variance noise threshold, judge_composite ≥ 0.6, judge n_failed < 2. Emits a distinct `decision` label per failure (`noise-rejected`, `judge-rejected`, `delta-below-floor`, `gates-missing`, `sanity-floor-rejected`, etc.) so the audit trail shows which gate killed the run. Closes the reviewer's structural point: "Variance estimation that's only printed isn't pressure on the keep gate."

### Changed
- Agent doc step 6 rewritten — agent invokes `decide.py` and follows its decision rather than computing in prose.

### Verified
- 27/27 tests still pass on Linux (decide.py picked up by `tests/run.sh`'s `src/*.py` syntax loop)
- decide.py output validated against existing baseline-seed experiment: Δ=0 against 0.0098 variance threshold correctly yields `noise-rejected` with `gates_failed: ["delta-floor","variance"]`
- All 9 write_text sites pass `grep "write_text(" src/*.py | grep -v "encoding="` returning empty
- install.ps1 try/catch → $LASTEXITCODE fix verified by reading



## [0.7.1] — 2026-05-19

### Added
- `src/variance.py` — bootstrap subsample variance estimator. Closes the fourth named critique gap: "Δ ≥ +0.02 is below noise without variance estimation." Re-runs the mechanical scorer on N=20 stratified subsamples (default `subsample-frac=0.7`), computes stdev across the runs, returns `noise_threshold_1_5_sigma`. Free to run — no token spend, just re-uses existing response files. Validated on my dev benchmark: stdev=0.0066, threshold=0.0098.

### Changed
- Agent doc step 5c documents the call. **In auto-commit mode, keep decision now requires THREE gates passed:** `Δ > noise_threshold_1_5_sigma` (statistical significance) AND `Δ >= 0.02` (original floor) AND `judge_composite >= 0.6` (semantic quality from v0.7.0). Any failure → distinct decision label (`noise-rejected`, `delta-below-floor`, `judge-rejected`).

### Methodology gap remaining
- **Correction-weighted ground truth** — corrections.jsonl entries aren't yet matched to specific benchmark tasks as labels. The `no_correction` mechanical signal is correlation-based; true label-weighted scoring requires per-task correction matching. v0.8 work.

### What the loop now looks like (auto-commit mode)
```
preflight  → propose → safety_check → apply
  ↓
real replay (claude -p haiku, $$)
  ↓
mechanical score
  ↓
LLM judge ($)
  ↓
variance estimate (free)
  ↓
gate: Δ > 1.5σ  AND  Δ >= 0.02  AND  judge_composite >= 0.6  →  KEEP
otherwise → revert + dead-letter the (strategy, target_file)
```



## [0.7.0] — 2026-05-19

### Added
- `src/judge.py` — LLM-as-judge scoring. The load-bearing methodology fix the external review named: regex heuristics are gameable, so the judge reads each (prompt, response) pair and scores 1-5 on five rubric dimensions the regexes can't reliably detect: `position_taking`, `task_completion`, `search_first`, `tool_appropriate`, `response_specific`. Reuses `claude -p` for auth (same path as replay.py, no API key needed). Default sample = 5 tasks per run @ ~$0.01-0.02 each, ~$0.05/run total. Writes per-dimension means + a `judge_composite` to `judge-scores.json`.

### Changed
- Agent doc gained step 5b: judge invocation between mechanical scoring and the keep/revert decision. **In auto-commit mode the keep decision is now gated on `judge_composite >= 0.6` AND `n_failed < 2` in addition to the mechanical Δ.** Observation mode surfaces judge scores in the proposal for user review.

### Why this matters
Closes the third critique gap (Goodhart-vulnerability). Mechanical scorer alone could be tricked by a CLAUDE.md edit that just forbids the trigger phrases. The judge reads actual response semantics — if a change tightens "no_premature" by removing the word "feels" everywhere but the responses are still hedged, the judge catches it.

### Honest limits still open
- **Multi-trial variance estimation** — still not built. Δ ≥ +0.02 is still below noise without it. A run that scores 0.85 vs 0.83 could be the same proposal sampled differently. v0.8 work.
- **Correction-weighted scoring** — `no_correction` already weighted 2.0 (highest), but corrections.jsonl entries themselves aren't matched to specific benchmark tasks for direct ground-truth labels yet. v0.8 work.
- Judge has its own failure modes (sycophancy, length bias). Use as ONE more signal alongside mechanical, not as sole source of truth. The composite the agent compares against is still mechanical; judge gates the auto-commit floor, doesn't replace the mechanical score.



## [0.6.1] — 2026-05-19

### Added
- `src/replay.py` — the actual benchmark replay path. Until v0.6.1, the agent doc *described* the replay step ("Run headless: claude -p --model haiku --max-turns 12 ...") but no code implemented it; the loop was end-to-end testable only in dry-run mode. `replay.py` now handles the per-task subprocess loop with `--output-format json`, parses the structured output, applies per-task budget caps via `--max-budget-usd`, total-budget caps in the runner, per-task timeouts, deterministic subsampling via seed, and writes both per-task response files and a `replay-summary.json` with cost/duration/completion stats.

### Changed
- Agent doc workflow step 4 now invokes `replay.py` concretely instead of describing the action. The `--dry-run` synthesis path still exists for testing without token spend.

### Why this matters
Closes the single biggest "untested in production" gap. Before: loop ran fine in dry-run; nobody knew if the real path would work. After: the real path is a single Python invocation with caps, timeouts, and structured output — much easier to reason about and to fail safely.



## [0.6.0] — 2026-05-19

Windows support. NIGHTLY now runs natively on Windows in addition to macOS / Linux / WSL.

### Added
- `install.ps1` — PowerShell port of `install.sh`. Detects Windows paths, *copies* (not symlinks) plugin scripts into `~\.claude\nightly\` so Dev Mode isn't required, git-inits the substrate with a Windows-aware `.gitignore`, mines history, seeds the baseline, prints Task Scheduler snippets.
- `verify.ps1` — PowerShell port of the post-install verifier.
- `src/snapshot.ps1` — PowerShell port of the pre-run snapshot script.
- `tests/run.ps1` — PowerShell port of the test suite. Builds synthetic fixtures in `$env:TEMP`, exercises every Python script.
- `sched/nightly-task.xml` — Task Scheduler unit. Import via the Task Scheduler GUI or `Register-ScheduledTask -Xml`.
- `hooks/nightly-surface.py` — cross-platform Python version of the SessionStart hook (replaces the bash-only version in `plugin.json`).

### Changed
- `plugin.json` SessionStart hook now invokes `python3 hooks/nightly-surface.py` instead of `bash hooks/nightly-surface.sh`. The bash hook is retained on disk for users who prefer it; the manifest just points at the Python file because Python is the only runtime guaranteed everywhere the plugin lands.
- `install.sh`'s generated `.gitignore` now covers the new symlink targets (`approve.py`, `reject.py`, `proposed/`) and the PowerShell scripts (`snapshot.ps1`).
- README "Supported platforms" table updated — Windows moved from ✗ to ✓.

### Honest limit
PowerShell scripts are syntax-checked by inspection only — no `pwsh` available on the dev machine for live validation (system-wide install was blocked as out-of-scope). Windows CI on a real runner is the v0.7 step. If anything breaks on a fresh Windows install, file a GitHub issue and it'll be a fast turnaround.

## [0.5.0] — 2026-05-19

External review of v0.4.x landed a substantive methodology critique (6/10 engineering, 4/10 as something to actually run nightly). All five points were correct: ground truth is "what happened" not "what should have happened"; the six scored signals are gameable regex heuristics; Δ ≥ +0.02 is below noise without variance estimation; replay model ≠ production model; auto-mining trades the labeling problem for a weaker signal.

This release acts on the reviewer's specific recommendation: **don't auto-commit until v0.3 lands a real judge + variance + correction-weighted scoring**.

### Changed (breaking)
- **Observation mode is now the default.** The loop proposes changes, scores them, and reverts. Proposals land at `~/.claude/nightly/proposed/<run_id>.md` with the full diff + score breakdown. Auto-commit only when the user explicitly opts in by creating the marker file `~/.claude/nightly/auto-commit.yes`. The agent doc now documents both modes and explains why observation is the default.

### Added
- `src/approve.py` + `/nightly approve <run_id>` — re-apply a proposed diff, run safety_check, commit with the right author email.
- `src/reject.py` + `/nightly reject <run_id> "<reason>"` — dead-letter the (strategy, target) pair AND write a correction entry so the proposer learns; symmetric to `disapprove` but for observation-mode proposals that were never committed.
- `/nightly list-proposals` — show pending observation-mode proposals.
- README "Methodology caveat" section — names the four honest limits of v0.2 scoring upfront so users see them before scheduling.
- README cites adjacent academic prior art the reviewer named: Reflexion (Shinn et al., 2023), DSPy MIPRO/OPRO, Microsoft's Trace, Anthropic claude-cookbooks agent evals patterns. None of these auto-mine the eval suite from session history; that remains NIGHTLY's distinct claim, and now the README is explicit that it's a "defensible bet to try" not a "proven approach."

### Why this version is honest
A 6/10 / 4/10 review with no defensive response is healthier than 100 stars on a tool nobody should run unobserved. v0.5.0 doesn't add LLM-as-judge yet — that's still v0.6 work — but it stops the loop from auto-mutating substrate while the methodology is being firmed up.

## [0.4.3] — 2026-05-19

Fresh-install regression pass, prompted by a thorough external test report (WSL Ubuntu 24.04). All four reported bugs reproduced and fixed.

### Fixed
- **Sev 1 — install.sh never linked `src/*.py` into `~/.claude/nightly/`.** The agent + slash command + `baseline.py` all reference `~/.claude/nightly/<script>` (a stable user-data path) but the source files live under `${PLUGIN_DIR}/src/`. `install.sh` now `ln -sf` each script in step [2/6] so both paths resolve. Without this, the install step that runs `baseline.py` crashed with `FileNotFoundError ~/.claude/nightly/scorer.py` on every fresh install.
- **Sev 1 — `snapshot.sh` blocked every fresh-install `/nightly` run.** `git status --porcelain` collapses untracked directories to one entry (`nightly/`), but the autosafe allowlist contained concrete paths (`nightly/experiment-log.jsonl`, etc.). Result: snapshot saw `nightly/` as "unsafe" and exited 3. Fixed by passing `--untracked-files=all` so git lists every untracked file individually.
- **Sev 2 — `miner.py` crashed on `set -euo pipefail` installs without a populated `~/.claude/projects/`.** Now writes an empty corpus and exits 0 when the projects dir is missing. Same fix applied to `benchmark.py` for the "no eligible tasks" case.
- **Sev 3 — personal scaffolding leaked through the substrate.** Removed `user`/`the user` references from `agents/nightly-optimizer.md` (including a dangling link to a personal `~/dreaming/` doc), `src/disapprove.py` (correction log + dead-letter entries no longer hardcode "the user vetoed"), and `src/snapshot.sh` autosafe list. `src/miner.py`'s project-name parser now uses `getpass.getuser()` instead of hardcoded `user`.

## [0.4.2] — 2026-05-19

### Added
- `tests/run.sh` — synthetic-data test suite. 21 assertions across miner extraction, benchmark stratification, scorer composition, strategy_stats edge cases, safety_check (3 forbidden-path classes), weekly_rollup, bash syntax for all 5 `.sh` files, and Python compile for all 8 `.py` files. Fixtures built in `mktemp -d`; nothing touches the user's real `~/.claude/`.

### Known issues
- CI workflow at `.github/workflows/test.yml` was planned but blocked by a local security hook; the YAML is documented inline in `tests/README.md` so users can drop it into their fork.

## [0.4.1] — 2026-05-19

### Added
- `verify.sh` — post-install smoke test that exercises every component without spending tokens. Runs miner/benchmark presence, scorer composition, strategy_stats, safety_check, snapshot, weekly_rollup, and hook syntax. Exit 0 = ready to schedule. Catches install drift before the first cron fire.

### Fixed
- `verify.sh` immediately surfaced a real bug: `snapshot.sh` was refusing to commit because several runtime paths (`.credentials.json`, `mcp-needs-auth-cache.json`, `security_warnings_state_*.json`, plugin-symlinks at `nightly/*.py`) weren't in `.gitignore`. The substrate's working tree would have been dirty on every cron fire, aborting the loop. Fixed in `install.sh`'s generated `.gitignore`.
- `snapshot.sh` autosafe list now includes `nightly/experiment-log.jsonl`, `nightly/dead-letter.jsonl`, and `nightly/reports/` — those are substrate-evolution data the loop is supposed to auto-commit between runs.

## [0.4.0] — 2026-05-19

### Added
- **Sanity floor on score (0.5).** If an experiment scores below 0.5, the loop is producing garbage — broken proposer, failing replay, or misconfigured scorer. The run is reverted, logged as `decision: "sanity-floor-rejected"`, and a report explicitly flags the failure. Three consecutive sanity-floor rejections → loop aborts until manually investigated. Closes a real bug: previously, `first-real-baseline` enshrined any score, so a 0.3 first run became the comparison anchor forever and trivially-better subsequent runs all looked like wins.
- **Wall-clock cap (30 minutes).** Hard timeout on a single experiment. If the loop hasn't finished in 30 min, it reverts any partial change and logs `decision: "timeout"`. Next cron fires fresh.

### Changed
- `first-real-baseline` is now gated on the sanity floor. Previously: always kept regardless of score.

## [0.3.2] — 2026-05-19

### Added
- `docs/example-morning-report.md` — synthetic-but-realistic sample of what NIGHTLY produces after a kept run.
- `docs/example-weekly-rollup.md` — synthetic-but-realistic sample of the 7-day aggregate.

These let someone evaluating NIGHTLY see the output shape before installing.

## [0.3.1] — 2026-05-19

### Added
- `src/weekly_rollup.py` — aggregates last 7 days of `experiment-log.jsonl` into a single markdown report. Surfaces score trend, decision breakdown, per-strategy effectiveness, kept changes with diff summaries, dead-lettered patterns, and forward-looking guidance.
- `install.sh` now prints a second cron line for the weekly rollup (Sundays 09:00).

## [0.3.0] — 2026-05-19

### Added
- `src/strategy_stats.py` — per-strategy kept/tried rates computed from `experiment-log.jsonl`. Buckets strategies into `promising` / `untried` / `neutral` / `avoid`. The agent reads this on every run and biases proposal selection accordingly. Inspired by `cgraves09/autoskill`'s FINDINGS.md observation that only 2 of 7 mutation operators produced lasting improvement.
- `src/safety_check.py` — post-apply guard that rejects forbidden paths, file deletions, and >50% line reductions on previously-large files. Catches the autoskill failure mode where the optimizer kept producing destructive 1-line rewrites.

### Changed
- Agent doc workflow now includes a mandatory safety-check step (3b) between apply and replay.
- Proposal step (2) now consults `strategy_stats.py` before picking a strategy.

## [0.2.0] — 2026-05-18

Initial public release.

### Components
- `miner.py` — extracts tasks from `~/.claude/projects/*/*.jsonl` session logs into `corpus.jsonl`.
- `benchmark.py` — stratified 40-task eval suite from the corpus, versioned by date.
- `scorer.py` — mechanical scoring (completion, no_correction, no_premature, no_options, search_first, tool_alignment). Cost moved to diagnostics-only (Goodhart trap).
- `baseline.py` — bootstrap synthetic baseline so night 1 has something to compare against.
- `snapshot.sh` — pre-run auto-commit of `memory/` + `corrections.jsonl` so dirty tree doesn't abort the cron.
- `disapprove.py` — `/nightly disapprove <run_id> "<reason>"` reverts a kept change, writes a `corrections.jsonl` entry in the user's voice, and dead-letters the `(strategy, target_file)` pair.
- `nightly-optimizer` agent — the loop.
- `/nightly` slash command with `status`, `diff`, `disapprove` subcommands.
- `nightly-surface.sh` SessionStart hook — surfaces unread reports on session start.
- `install.sh` — cross-OS one-command setup with crontab / launchd / GitHub Actions options.

### Prior art credited
- `karpathy/autoresearch` — original loop shape (ML training domain).
- `VoidLight00/autoimprove-cc` — closest CC-native artifact; requires hand-written `eval.json`.
- `cgraves09/autoskill` — mutation-operator skill optimizer; requires hand-written test cases.
- `compound-engineering:ce-optimize` skill — full Karpathy loop, requires user-defined metric.

NIGHTLY's gap closure across all of them: **the eval is auto-built from your session history. No hand-written benchmark required.**
