# Changelog

All notable changes to NIGHTLY are recorded here. Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.4.3] — 2026-05-19

Fresh-install regression pass, prompted by a thorough external test report (WSL Ubuntu 24.04). All four reported bugs reproduced and fixed.

### Fixed
- **Sev 1 — install.sh never linked `src/*.py` into `~/.claude/nightly/`.** The agent + slash command + `baseline.py` all reference `~/.claude/nightly/<script>` (a stable user-data path) but the source files live under `${PLUGIN_DIR}/src/`. `install.sh` now `ln -sf` each script in step [2/6] so both paths resolve. Without this, the install step that runs `baseline.py` crashed with `FileNotFoundError ~/.claude/nightly/scorer.py` on every fresh install.
- **Sev 1 — `snapshot.sh` blocked every fresh-install `/nightly` run.** `git status --porcelain` collapses untracked directories to one entry (`nightly/`), but the autosafe allowlist contained concrete paths (`nightly/experiment-log.jsonl`, etc.). Result: snapshot saw `nightly/` as "unsafe" and exited 3. Fixed by passing `--untracked-files=all` so git lists every untracked file individually.
- **Sev 2 — `miner.py` crashed on `set -euo pipefail` installs without a populated `~/.claude/projects/`.** Now writes an empty corpus and exits 0 when the projects dir is missing. Same fix applied to `benchmark.py` for the "no eligible tasks" case.
- **Sev 3 — personal scaffolding leaked through the substrate.** Removed `arnav`/`Arnav` references from `agents/nightly-optimizer.md` (including a dangling link to a personal `~/dreaming/` doc), `src/disapprove.py` (correction log + dead-letter entries no longer hardcode "Arnav vetoed"), and `src/snapshot.sh` autosafe list. `src/miner.py`'s project-name parser now uses `getpass.getuser()` instead of hardcoded `arnav`.

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
