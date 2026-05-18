---
description: NIGHTLY autoresearch loop against ~/.claude/. Default = run one experiment. Subcommands: status, diff, disapprove.
---

Arguments: `$ARGUMENTS`

## Subcommand routing

Look at the first token of `$ARGUMENTS`. Route as follows:

### (default — no subcommand, or starts with `--`)
Run the snapshot script then invoke the `nightly-optimizer` agent:

```bash
bash ~/.claude/nightly/snapshot.sh
```

If snapshot exits non-zero, surface the message and abort — do NOT run the optimizer. The user has WIP that must be resolved first.

Then dispatch to the `nightly-optimizer` agent with the full `$ARGUMENTS` string. The agent reads its workflow from its own definition.

Recognized flags (pass-through):
- `--dry-run` — skip benchmark replay, use corpus ground-truth as synthetic substitute
- `--budget <usd>` — override default $3 cap
- `--n <count>` — override default 10 replayable tasks

### `status`
Print a one-screen status:
- Last 5 entries of `~/.claude/nightly/experiment-log.jsonl` with run_id, decision, delta
- Current baseline (latest entry with `decision in ("kept","first-real-baseline")`)
- Count of dead-lettered (strategy, target_file) pairs
- Whether `~/.claude/nightly/reports/<today>.md` exists (i.e., did tonight's run happen)

### `diff <run_id>`
Show the diff of a kept run:
```bash
sha=$(jq -r 'select(.run_id=="<run_id>")|.new_commit' ~/.claude/nightly/experiment-log.jsonl | tail -1)
git -C ~/.claude show "$sha"
```

### `disapprove <run_id> "<reason>"`
Veto a kept run. Invoke:
```bash
python3 ~/.claude/nightly/disapprove.py "<run_id>" "<reason>"
```
The reason becomes a new `corrections.jsonl` entry — write it the way you'd correct Claude in chat (e.g. *"don't add hedging to the operating mode, that's the opposite of position-first"*).

## Notes

- The cron entry at 22:00 IST runs `claude -p '/nightly'` headless. Output: one summary line plus a written morning report.
- Subcommand args are positional after `/nightly`. Example: `/nightly disapprove 2026-05-18-2200 "reason"`.
- If `~/.claude/` is not a git repo, route to install: print `bash ~/.claude/nightly/install.sh` and stop.
