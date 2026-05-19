# Example: Weekly Rollup — 2026-06-08 (synthetic)

> What `python3 ~/.claude/nightly/weekly_rollup.py` produces after a real week of running. The aggregate is where compounding becomes visible.

**Period:** last 7 days (2026-06-01 → 2026-06-08)
**Total runs:** 7

## Decisions

| Decision | Count |
|---|---|
| kept | 3 |
| reverted | 2 |
| held | 1 |
| unsafe-rejected | 1 |

## Score trend

**First → last:** 0.864 → 0.912  (Δ +0.048)
**Median:** 0.889

| Date | Score | Decision | Strategy |
|---|---|---|---|
| 2026-06-02 | 0.864 | kept | rule-rewrite |
| 2026-06-03 | 0.871 | held | skill-description-tighten |
| 2026-06-04 | 0.901 | kept | hook-tighten |
| 2026-06-05 | 0.887 | reverted | rule-reorder |
| 2026-06-06 | 0.889 | unsafe-rejected | hook-tighten |
| 2026-06-07 | 0.893 | reverted | memory-add |
| 2026-06-08 | 0.912 | kept | rule-rewrite |

## Strategy effectiveness (this period)

| Strategy | Tried | Kept | Rate |
|---|---|---|---|
| rule-rewrite | 2 | 2 | 100% |
| hook-tighten | 2 | 1 | 50% |
| skill-description-tighten | 1 | 0 | 0% |
| rule-reorder | 1 | 0 | 0% |
| memory-add | 1 | 0 | 0% |

## Kept changes

### `2026-06-02-2200` — rule-rewrite on ~/CLAUDE.md
- **Score:** 0.864
- **Commit:** `a31bc02e`
- **Diff:** Added rule: "When designing, run `gh search` BEFORE drafting"

### `2026-06-04-2200` — hook-tighten on hooks/design-mode-guard.sh
- **Score:** 0.901
- **Commit:** `7af9c2d4`
- **Diff:** -15 lines, +8 lines (verbose checklist tightened, signal preserved)

### `2026-06-08-2200` — rule-rewrite on AGENT_OPERATING_MODE.md
- **Score:** 0.912
- **Commit:** `bf02ee18`
- **Diff:** Promoted "no premature closure on recursive asks" from a sub-bullet to a top-level rule

## Dead-lettered this period

- `hook-tighten` → `hooks/active-tasks-inject.sh` (unsafe-rejected: too-large reduction)
- `memory-add` → `memory/feedback_loop_audit.md` (held: marginal score with no clear win)

## Guidance for next week

- **Prefer:** `rule-rewrite` (proven 100% in this period — concrete correction-derived rules are landing)
- **Explore:** None — all 5 strategies have been tried at least once now.
- **Avoid:** none yet — `skill-description-tighten`, `rule-reorder`, `memory-add` each have one failed run but sample is too small to call.

Net: substrate score climbed from 0.864 to 0.912 across the week. Three kept changes, two reverts (loop working as designed — bad changes were caught), one unsafe-rejected (guard working as designed).
