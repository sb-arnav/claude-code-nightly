# SPEC: `skill-lifecycle` Strategy

> Add a 6th mutation strategy to the nightly optimizer that archives unused skills and recalls demanded archived skills, using session transcript analysis as the usage signal.

## Motivation

Skills loaded into the Claude Code system prompt consume tokens on every turn. An unused skill wastes prompt budget without providing value. Conversely, a skill archived too aggressively may be needed again — session transcripts reveal latent demand via keyword hits and Skill tool invocations.

The existing 5 strategies (rule-rewrite, hook-tighten, memory-add, skill-description-tighten, rule-reorder) optimize substrate *content*. `skill-lifecycle` optimizes substrate *composition* — which skills are active at all.

## Design

### Usage Signal Detection

The adapter scans `.jsonl` session transcripts from multiple sources:

```
~/.claude/projects/           # Claude Code sessions
~/.codex/sessions/            # Codex sessions (if present)
~/.agents/sessions/           # Other agent harnesses
```

Three signal types per skill:
1. **Skill tool call**: `"tool": "Skill"` with `"input": {"skill": "<name>"}` in transcript
2. **SKILL.md loaded**: `"Base directory for this skill:"` string in assistant messages
3. **Keyword match**: skill name appears in assistant content

### Adapter Script: `src/skill_lifecycle.py`

Self-contained Python script with three modes:

```
python3 src/skill_lifecycle.py --propose
python3 src/skill_lifecycle.py --apply --name X --action archive|recall --source claude|agents
python3 src/skill_lifecycle.py --revert --name X --action archive|recall --source claude|agents
```

**Exit codes** (aligned with `safety_check.py` protocol):
- `0` — action taken / candidate found
- `1` — no candidate available / revert failed
- `3` — safety rejected (protected skill, minimum active count, symlink)

**Directory layout:**

| Source | Active path | Archive path |
|---|---|---|
| `claude` | `~/.claude/skills/` | `~/.claude/_archive_skills/` |
| `agents` | `~/.agents/skills/` | `~/.agents/_archive_skills/` |

### `--propose` Logic

1. Scan sessions from last 30 days, count per-skill usage
2. **Archive candidates**: active skills with 0 hits in 30d, excluding protected set
3. **Recall candidates**: archived skills with ≥3 hits in last 14d
4. **Selection priority**: recall (highest hits) > archive (largest file size = most token savings)
5. Output: single JSON object with `name`, `source`, `action`, `hits`, `size_bytes`

### Safety Guards

Built into `--apply`:
- Skills in the hardcoded `ALWAYS_KEEP` set (`skill-router`, `context-mode`, `oh-my-claudecode`) are never archived
- User-defined `protected_skills.json` entries are never archived
- Symlink skills are never moved (they point to canonical locations)
- Archive is rejected if total active skill count would drop below 5

### Proposal Structure

```json
{
  "run_id": "2026-05-27-0300",
  "baseline_commit": "abc123",
  "strategy": "skill-lifecycle",
  "target_file": "~/.claude/skills/<name>/SKILL.md",
  "action": "archive",
  "skill_name": "some-skill",
  "skill_source": "claude",
  "change_summary": "Archive skill 'some-skill' (0 hits in 30d, 14KB) to reduce prompt noise",
  "evidence": {
    "days_analyzed": 30,
    "hit_count": 0,
    "size_bytes": 14497,
    "archive_pool": 3,
    "recall_pool": 0
  },
  "motivating_corrections": [],
  "proposed_at": "2026-05-27T03:00:00Z"
}
```

### Integration with Agent Workflow

**Step 2 (Propose):**
```bash
python3 ~/.claude/plugins/nightly/src/skill_lifecycle.py --propose
```
If exit 0 → use output to fill `proposal.json`. If exit 1 → no candidate, pick another strategy.

**Step 3 (Apply):**
```bash
python3 ~/.claude/plugins/nightly/src/skill_lifecycle.py \
  --apply --name <name> --action <archive|recall> --source <claude|agents>
```
Exit 3 → treat as safety_check failure (revert, dead-letter, stop).

**Step 7 (Revert — both observation mode and auto-commit revert):**
```bash
python3 ~/.claude/plugins/nightly/src/skill_lifecycle.py \
  --revert --name <name> --action <archive|recall> --source <claude|agents>
```
Must run BEFORE `git reset --hard` since the filesystem move is not tracked by git.

### Scoring Considerations

Standard replay + mechanical scorer applies. Additional context for the report:

```json
{
  "prompt_token_delta": -1847,
  "active_skill_count_before": 52,
  "active_skill_count_after": 51
}
```

A token savings >1000 with score parity (Δ within noise floor) can be treated as positive signal — less prompt noise without quality regression.

### Strategy Stats Integration

`skill-lifecycle` participates in `strategy_stats.py` effectiveness tracking as a single strategy bucket. Sub-types (archive vs recall) are not tracked separately — the sample size would be too small for meaningful signal.

## Hard Rule 5 Exception

The existing hard rule "Never touch `~/.claude/plugins/`" remains. `skill-lifecycle` operates on `~/.claude/skills/` and `~/.agents/skills/` — these are substrate, not plugin/cache state. The `safety_check.py` path allowlist should whitelist:

```
~/.claude/skills/
~/.claude/_archive_skills/
~/.agents/skills/
~/.agents/_archive_skills/
```

Only for `strategy == "skill-lifecycle"`, and only for directory moves (not content edits or deletions).

## Out of Scope

- Batch archive/recall (violates one-change-per-run principle)
- Modifying `settings.json` (skill activation is filesystem-level, not settings-level)
- Rebuilding search indexes after moves (handled lazily on next skill invocation)
- Changing the usage detection heuristics (signal quality is independent of this strategy)

## Acceptance Criteria

1. `--propose` correctly identifies archive candidates (30d zero usage) and recall candidates (≥3 hits in 14d)
2. `--apply` with a protected skill exits 3
3. `--apply` with a non-existent skill exits 3
4. `--revert` restores original filesystem state (including symlink repointing)
5. Dead-letter blocks re-trying the same `(skill-lifecycle, skill_name)` pair
6. `strategy_stats.py` tracks `skill-lifecycle` kept/tried ratios correctly
7. Observation mode always reverts the filesystem move after scoring
