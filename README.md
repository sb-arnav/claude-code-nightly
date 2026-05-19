# NIGHTLY

> A Karpathy-shape autoresearch loop that improves your Claude Code substrate (CLAUDE.md, hooks, skills, memory) overnight, using your own session history as the evaluation suite.

```
propose → snapshot → apply → replay → score → keep or revert → log
```

One small experiment per night, kept in git, measurably better each morning.

## Why this exists

Every other "self-improve your agent" tool — `karpathy/autoresearch`, `autoimprove-cc`, `compound-engineering:ce-optimize`, the various Claude Code autoresearch ports — needs **you** to hand-write a benchmark first. You write the eval cases. You write the pass/fail metric.

That's wrong for personal-agent evolution. Your real work isn't a fixed benchmark — it's the hundreds of hours of Claude Code sessions sitting in `~/.claude/projects/` right now.

NIGHTLY mines your session history into a benchmark automatically. The eval suite writes itself. The loop runs each night against *your work*, not a synthetic test set.

## What you'll see

Every morning a one-screen status surfaces on session start:

```
=== NIGHTLY ===
new report: 2026-05-18.md
last run: 2026-05-18-2200 · kept · rule-rewrite Δ+0.034
read with: cat /home/you/.claude/nightly/reports/2026-05-18.md
disapprove (if you disagree): /nightly disapprove <run_id> "<reason>"
=== END ===
```

If you disagree with the change it kept, you veto it. Your veto becomes a `corrections.jsonl` entry **in your voice** plus a dead-letter entry that prevents the loop from ever proposing the same change again.

## What it looks like in practice

- **A morning report** after each nightly run: [`docs/example-morning-report.md`](docs/example-morning-report.md)
- **A weekly rollup** showing trend, kept changes with diffs, and per-strategy effectiveness: [`docs/example-weekly-rollup.md`](docs/example-weekly-rollup.md)

These are synthetic (the real ones live on your machine at `~/.claude/nightly/reports/`), but show the actual output shape you'll see after a week of runs.

## Install

> **Methodology caveat** (read before scheduling). v0.2 uses six regex/heuristic signals over historical replay. Honest limits:
> - **Ground truth is "what the past assistant did", not "what should have happened."** Optimizing toward replay similarity can entrench past mistakes.
> - **All six signals are surface heuristics.** A CLAUDE.md edit that simply forbids the trigger phrases (`feels balanced`, `in summary`, `Option A/B/C`) would score great without improving reasoning. Goodhart is the failure mode.
> - **The Δ ≥ +0.02 keep threshold is below noise** without variance estimation or repeated trials. A "kept" decision and a "reverted" decision a week apart could be the same proposal with different sampling luck.
> - **Replay model ≠ production model.** Haiku replay matching Sonnet ground truth ≠ Sonnet got better.
>
> Therefore: **observation mode is the default**. The loop proposes changes and scores them but does not commit them. You review and `/nightly approve` or `/nightly reject`. Auto-commit is opt-in via `touch ~/.claude/nightly/auto-commit.yes`, and is not recommended at v0.2. v0.3 will add LLM-as-judge agreement, multi-trial variance, and correction-weighted scoring — gate auto-commit on those.

### Supported platforms

| Platform | Status | Notes |
|---|---|---|
| **macOS** | ✓ Fully supported | bash + crontab + launchd templates |
| **Linux** | ✓ Fully supported | bash + crontab |
| **WSL (Windows Subsystem for Linux)** | ✓ Fully supported | Treated as Linux; bash + crontab |
| **Native Windows (PowerShell)** | ✓ Supported as of v0.6 | PowerShell ports of install/verify/snapshot/tests + Task Scheduler XML. Use `install.ps1`. **Note:** Windows path tested locally via syntax review only; CI on a real Windows runner is on the v0.7 list. File a GitHub issue if anything breaks on your install — fast turnaround. |
| **GitHub Actions (any host)** | ✓ Cloud option | See `sched/github-action.yml` — runs the loop in a Linux container against a backed-up `~/.claude/` repo. |

**Python scripts** under `src/` are platform-agnostic — they use `Path.home()`, `getpass.getuser()`, `subprocess` for git, and no shell-specific assumptions. The orchestration (install/verify/snapshot/tests) has parallel bash + PowerShell versions; the SessionStart hook is a single Python file (`hooks/nightly-surface.py`) that runs everywhere.

### Install — macOS / Linux / WSL
```bash
git clone https://github.com/sb-arnav/claude-code-nightly ~/.claude/plugins/nightly
bash ~/.claude/plugins/nightly/install.sh
```

### Install — Windows (PowerShell)
```powershell
git clone https://github.com/sb-arnav/claude-code-nightly $HOME\.claude\plugins\nightly
pwsh -ExecutionPolicy Bypass -File $HOME\.claude\plugins\nightly\install.ps1
```
The Windows installer copies plugin scripts into `~\.claude\nightly\` (instead of symlinking — Windows symlinks need admin/Dev Mode), git-inits the substrate, mines your session history, seeds the baseline, and prints the Task Scheduler snippet. Schedule with `Register-ScheduledTask` or import `sched/nightly-task.xml`.

The installer:

1. Verifies prerequisites (python3, git, claude CLI, bash)
2. Creates `~/.claude/nightly/` for data (corpus, benchmark, reports, experiment log)
3. Registers a SessionStart hook so reports surface automatically
4. Runs `git init` inside `~/.claude/` with a `.gitignore` that excludes session logs, plugin caches, telemetry — only your actual config substrate is tracked
5. Mines your session history into a corpus, builds a 40-task stratified benchmark, seeds a bootstrap baseline
6. Prints scheduling instructions for your OS

It's idempotent — safe to re-run.

## Scheduling (pick one)

### macOS / Linux / WSL — cron
```bash
crontab -e
# add:
0 22 * * * cd $HOME && claude -p '/nightly' >> $HOME/.claude/nightly/logs/cron.log 2>&1
```

### macOS — launchd (survives reboots without your terminal open)
```bash
cp ~/.claude/plugins/nightly/sched/com.nightly.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.nightly.plist
```

### Claude Code remote schedule (no cron, runs in the cloud)
Requires a Claude plan with remote agents.
```
/schedule add nightly '0 22 * * *' /nightly
```

### GitHub Actions
See [`sched/github-action.yml`](sched/github-action.yml). Useful if you want to run NIGHTLY against a backed-up `~/.claude` repo without leaving your laptop on.

## Daily use

```bash
# Test the loop without spending tokens (uses corpus ground truth as synthetic replay)
claude -p '/nightly --dry-run'

# Run one experiment (~$0.50–$2 in Haiku tokens)
claude -p '/nightly'

# What did the loop do recently?
claude -p '/nightly status'

# Inspect a kept change's diff
claude -p '/nightly diff 2026-05-18-2200'

# Veto a kept change you disagree with — it becomes a correction + dead-letter
claude -p '/nightly disapprove 2026-05-18-2200 "don't add hedging to operating mode; that's the opposite of position-first"'

# Weekly rollup — trend across last 7 nights with diffs and strategy guidance
python3 ~/.claude/nightly/weekly_rollup.py
```

## What gets scored

The mechanical scorer (v0.2 — no LLM-as-judge yet) rates each replayed prompt on:

| Component | Weight | What it detects |
|---|---|---|
| `no_correction` | 2.0 | The `corrections-inject` hook didn't fire — i.e., behavior didn't trigger a known anti-pattern |
| `completion` | 1.5 | Task reached a coherent stop, not abandoned |
| `no_premature` | 1.5 | Response avoided premature-closure phrases ("feels balanced," "deferred until," "in summary") |
| `no_options` | 1.5 | Response took a position, didn't offer Option A/B/C menu |
| `search_first` | 1.0 | For design-shaped prompts, the first non-Read action was a search (`WebSearch`, `gh search`, `Tavily`) |
| `tool_alignment` | 1.0 | Tool call count was within 0.5x–2.0x of the ground truth |
| `cost` (diagnostic) | 0 | Logged but not scored — Goodhart-vulnerable since replay model differs from ground truth |

Composite score per task, mean across the replayed sample = run's score.

## Keep/revert rule

Compared against the last `kept` or `first-real-baseline` entry in `experiment-log.jsonl`:

- Δ ≥ +0.02 → **kept** (git commits the change)
- Δ ≤ −0.02 → **reverted** (git reset, change discarded)
- otherwise → **held** (reverted + dead-lettered so the same `(strategy, target)` isn't retried)
- if proposed `(strategy, target)` is already in the dead-letter → **blocked** before any work

## Learning between runs

Two pieces keep nightly from being a random walk:

**Strategy effectiveness tracking** (`src/strategy_stats.py`). Walks `experiment-log.jsonl` and computes per-strategy kept/tried rates. The agent reads this on every run and biases proposal selection: prefer `promising` (≥40% rate over ≥3 tries) > `untried` (no data yet) > `neutral` > `avoid` (<10% over ≥5 tries). Inspired by `cgraves09/autoskill`'s FINDINGS.md observation that only 2 of 7 named mutation operators ever produced lasting improvement — without per-strategy tracking, the loop wastes runs on strategies that empirically don't work.

```bash
python3 ~/.claude/nightly/strategy_stats.py        # human-readable table
python3 ~/.claude/nightly/strategy_stats.py --json # what the agent reads
```

**Safety guard** (`src/safety_check.py`). Runs after the agent applies a change, before scoring. Rejects:
- Forbidden paths (`.gitignore`, `.git/`, `settings.json`, `projects/`, `plugins/`, `statsig/`, `sessions/`, etc.)
- File deletion of any tracked substrate file
- >50% line reduction on a previously-large file (the autoskill destructive-rewrite failure mode)
- Files originally ≥50 lines that ended up <20 lines

Exit 3 → the run is auto-reverted, the `(strategy, target_file)` pair gets dead-lettered, no score is recorded, no commit is made.

## Inspiration / prior art

- [`karpathy/autoresearch`](https://github.com/karpathy/autoresearch) — original loop shape, ML-training-specific.
- [`VoidLight00/autoimprove-cc`](https://github.com/VoidLight00/autoimprove-cc) — closest Claude-Code-native artifact. Optimizes a single SKILL.md against hand-written `eval.json` assertions. NIGHTLY's gap closure: the eval is auto-built from your session history.
- [`cgraves09/autoskill`](https://github.com/cgraves09/autoskill) — Karpathy loop applied to one skill at a time with named mutation operators. NIGHTLY borrows the `strategy_stats.py` effectiveness-tracking pattern and the `safety_check.py` minimum-line-count guard from their published FINDINGS.md (60+ iterations, 45% → 90% on a real skill).
- `compound-engineering:ce-optimize` skill — full Karpathy loop with worktree isolation, persistence, judge mode. NIGHTLY cribs its append-only-log discipline and keep/revert decision shape, not the 659-line scaffolding.

### Adjacent academic / framework prior art

- **Reflexion** (Shinn et al., 2023) — same modify→verify→keep loop shape applied to agent trajectories. Self-reflection on failed runs → stored memory → next attempt. NIGHTLY's `corrections.jsonl` → `proposed_rule` → next-night proposer pipeline is structurally similar; Reflexion's scoring is task-success, NIGHTLY's is replay-similarity (weaker signal, which the v0.2 caveat above acknowledges).
- **DSPy + MIPRO / OPRO** (Stanford) — automatic prompt optimization with bootstrapped few-shot examples and a real labeled metric. Closer to the "rigorous" version of what NIGHTLY does, but needs labeled examples. NIGHTLY trades the labeling burden for a weaker auto-mined signal — the open methodology gap.
- **Trace** (Microsoft, [`microsoft/trace`](https://github.com/microsoft/trace)) — generalized backprop-through-LLM-agents that optimizes any text parameter against a metric. NIGHTLY is a narrow instance with a fixed metric and a fixed parameter space (the substrate).
- **Anthropic claude-cookbooks agent evals** + **OpenAI evals** — reference patterns for grading agent outputs with LLM-as-judge. NIGHTLY's v0.3 plan adopts the judge pattern; until then, the scorer is mechanical and the methodology caveat above stands.

NIGHTLY's distinct claim across all of these remains: **the eval suite is auto-mined from the user's own session history.** None of the above mine the eval from session traces. Whether that novelty is worth the ground-truth weakness it introduces is the open question — answer empirically by running in observation mode and reviewing proposals.

## Files

```
~/.claude/plugins/nightly/         # plugin code (this repo)
├── .claude-plugin/plugin.json     # manifest — Claude Code wires up agents/commands/hooks
├── agents/nightly-optimizer.md    # the loop's agent definition
├── commands/nightly.md            # /nightly slash command + subcommands
├── hooks/nightly-surface.sh       # SessionStart hook — surfaces new reports
├── src/                           # python + bash supporting scripts
│   ├── miner.py                   # sessions → corpus.jsonl
│   ├── benchmark.py               # corpus → versioned 40-task eval suite
│   ├── scorer.py                  # benchmark + replay responses → score
│   ├── baseline.py                # seeds synthetic bootstrap baseline
│   ├── snapshot.sh                # pre-run auto-commit of memory + corrections
│   ├── disapprove.py              # /nightly disapprove implementation
│   ├── strategy_stats.py          # per-strategy kept/tried rates → bias proposal selection
│   ├── safety_check.py            # apply-time guard against destructive rewrites
│   └── weekly_rollup.py           # aggregates 7 days of runs → trend/diff/guidance report
├── sched/                         # scheduler templates per platform
│   ├── com.nightly.plist          # macOS launchd
│   └── github-action.yml          # cloud cron via GitHub Actions
└── install.sh                     # one-command setup

~/.claude/nightly/                 # user data — survives plugin updates
├── corpus.jsonl                   # tasks extracted from session history
├── benchmark.jsonl                # current eval suite (symlink to latest dated)
├── benchmarks/benchmark-YYYY-MM-DD.jsonl  # regression history
├── experiments/<run_id>/          # per-run scratch + responses + scores
├── experiment-log.jsonl           # append-only history of every run
├── dead-letter.jsonl              # (strategy, target_file) pairs blocked from retry
├── reports/YYYY-MM-DD.md          # morning reports
└── logs/cron.log                  # scheduler stdout/stderr
```

## What it isn't

- Not RLHF or fine-tuning. The model is fixed; the prompt-substrate evolves.
- Not generic autoresearch. Existing tools optimize one skill against one synthetic benchmark; NIGHTLY optimizes your whole config against your real history.
- Not a chatbot. Cron-driven, file-output, calm-tech.
- Not multi-tenant. Your benchmark is yours. There's no marketplace of evolved configs.

## Status

v0.2. Mechanical scoring only — LLM-as-judge integration is the v0.3 step, added after the mechanical baseline is empirically stable.

## License

MIT (or pick what you want — this is reference scaffolding meant to be adapted).
