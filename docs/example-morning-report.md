# Example: Nightly Report — 2026-06-04 (synthetic)

> This is what a real NIGHTLY morning report looks like after a few weeks of runs. The numbers are illustrative — your scores will start lower and climb.

**Decision:** kept · **Strategy:** hook-tighten · **Score:** 0.876 → 0.901 (Δ +0.025)

## What was tried

Tightened `~/.claude/hooks/design-mode-guard.sh` from ~580 tokens of injected checklist to ~280 tokens, preserving the scan-first signal but removing the verbose example list. Motivated by `corrections.jsonl` entry 2026-06-01 (`hook-too-verbose`) — the user noted the hook output was getting clipped in some sessions because of size.

## Top 3 improvements

- `c99bdc5b-005` · 0.842 → 0.951 · search_first now triggers on a borderline "build me a..." prompt
- `74cf2e18-018` · 0.764 → 0.882 · tool_alignment improved (fewer redundant Read calls before the search)
- `567b6d08-000` · 0.811 → 0.918 · completion held while token cost dropped 22%

## Top 3 regressions

- `455037ed-003` · 0.847 → 0.781 · no_correction fired (the tightened hook missed one design-shape pattern)
- `9123ef8b-009` · 0.901 → 0.870 · completion borderline (one extra back-and-forth before stop)
- `2e761143-000` · 0.892 → 0.876 · tool_alignment slipped (one extra Bash call)

## Budget

Tokens: 14,200 (Haiku) · Estimated cost: $0.71

## Diff (kept)

```
$ git -C ~/.claude show --stat 7af9c2d
nightly 2026-06-04-2200: hook-tighten — score 0.876 → 0.901 (+0.025)

 hooks/design-mode-guard.sh | 23 ++++++++---------------
 1 file changed, 8 insertions(+), 15 deletions(-)
```

## Notes

- Crossed the 0.90 mean for the first time. Six kept changes in the last 14 nights; running success rate on hook-tighten now 2/3.
- One regression flagged where the tightened hook missed a pattern — added the missed pattern to the dead-letter context so next-night's proposal includes it.
- The `corrections-inject` hook didn't fire once across the replay set this run. Healthy signal.
