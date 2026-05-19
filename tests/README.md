# Tests

`bash tests/run.sh` runs the full synthetic-data test suite locally.

## What it tests

21 assertions across:
- **miner.py** — extracts tasks from a synthesized session JSONL
- **benchmark.py** — produces a stratified sample
- **scorer.py** — composes a score from hand-crafted responses
- **strategy_stats.py** — handles empty log; all 5 strategies categorized as `untried`
- **safety_check.py** — rejects each of three forbidden path classes
- **weekly_rollup.py** — runs against an empty log
- **bash syntax** for all 5 `.sh` files
- **python compile** for all 8 `.py` files

Test fixtures are built in `mktemp -d` and torn down on exit; nothing
touches your real `~/.claude/`.

## CI

The v0.4.2 commit was supposed to include a GitHub Actions workflow at
`.github/workflows/test.yml` that runs this suite on every push and PR. A
local Claude Code security hook (compound-engineering's
`security_reminder_hook.py`) blocked writes to `.github/workflows/*.yml`
from inside the agent session — the hook is generic and triggers on path,
not content, so I couldn't override it from this side.

To wire it up, drop this file at `.github/workflows/test.yml` in your fork:

```yaml
name: tests

on:
  push:
    branches: [main]
  pull_request:
    branches: [main]

jobs:
  test:
    runs-on: ubuntu-latest
    strategy:
      matrix:
        python-version: ["3.10", "3.11", "3.12"]
    steps:
      - uses: actions/checkout@v4

      - name: Set up Python ${{ matrix.python-version }}
        uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}

      - name: Run test suite
        run: bash tests/run.sh
```

That's it. No secrets, no user-controlled inputs in `run:` commands, no
permissions beyond default. Safe to commit as-is.

## Adding a test

`tests/run.sh` follows a "stages" pattern. Each stage:

1. Picks a section header via `bold "Stage name"`
2. Runs the code under test
3. Uses `ok "message"` or `fail "message"` to record outcomes
4. Increments `PASS` or `FAIL` automatically

If you add a new component to `src/`, add a stage that builds the minimum
fixture it needs and asserts at least one positive case + one negative
case (matching the safety_check pattern, which has three forbidden-path
assertions).
