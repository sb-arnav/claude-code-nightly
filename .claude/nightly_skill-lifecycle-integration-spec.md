# SPEC: skill-lifecycle — NIGHTLY × skill-router/manage.py 集成

## 背景

`manage.py` 提供基于 session 使用频率的 skill 归档/召回能力：
- **归档**: N 天内零活动的 skill → `_archive_skills/`
- **召回**: 已归档但 session 中仍被引用 ≥ min_hits 次 → 移回 active
- **保护**: 标记永不归档的 skill（`protected_skills.json`）
- **信号源**: `~/.claude/projects/` session transcripts + `route_log.jsonl`

当前 NIGHTLY 的 5 种策略（rule-rewrite, hook-tighten, memory-add, skill-description-tighten, rule-reorder）均不涉及 skill 启停。将 manage.py 的归档/召回逻辑作为第 6 种策略接入 NIGHTLY 循环。

## 目标

新增策略 `skill-lifecycle`：每晚基于 session 使用数据，提出**一个** skill 的归档或召回操作，经 replay-score 验证后决定 keep/revert。

## 设计

### 策略定义

```
| Strategy | When to use |
| skill-lifecycle | manage.py --status 显示：(a) 活跃 skill 30d 零使用，或 (b) 归档 skill 14d ≥3 hits。提出单个归档或召回。|
```

### Proposal 结构

```json
{
  "run_id": "...",
  "baseline_commit": "...",
  "strategy": "skill-lifecycle",
  "target_file": "~/.claude/skills/<name>/SKILL.md",
  "action": "archive" | "recall",
  "skill_name": "some-skill",
  "skill_source": "claude" | "agents",
  "change_summary": "Archive skill 'X' (0 hits in 30d) to reduce prompt noise",
  "evidence": {
    "days_analyzed": 30,
    "hit_count": 0,
    "total_sessions_scanned": 142
  },
  "motivating_corrections": [],
  "proposed_at": "<iso8601>"
}
```

### 执行流程

```
1. Preflight
   python3 ~/.agents/skills/skill-router/scripts/manage.py --status --days 30
   → 获取 active/archived 概览 + usage

2. Propose
   IF 存在 30d 零活动的非保护 skill → 选 token 占用最大的一个 → action=archive
   ELIF manage.py --recall --days 14 --min-hits 3 有推荐 → 选 hits 最高的 → action=recall
   ELSE → skip, 选其他策略

3. Apply
   IF archive: python3 manage.py --archive --days 30 (仅移动目标 skill，非 batch)
   IF recall:  python3 manage.py --recall --days 14 --min-hits 3 --apply (仅目标)
   注: manage.py 当前是 batch 操作，需要扩展为支持 --name <skill> 的单目标模式

4. Safety check
   - skill 在 ALWAYS_KEEP / protected_skills.json 中 → exit 3
   - skill 是 symlink → exit 3（不动 symlink skill）
   - archive 后剩余 active skill 数 < 5 → exit 3（防止清空）

5. Replay + Score
   标准 NIGHTLY 流程：replay benchmark，对比 baseline 分数

6. Decide
   与其他策略相同的 keep/revert 规则：
   - score ≥ baseline → keep（skill 归档降噪有正收益）
   - score < baseline - threshold → revert（该 skill 被隐式依赖）

7. Revert 机制
   IF revert:
     对 archive 操作: move_skill(name, source, 'archive', 'active')
     对 recall 操作: move_skill(name, source, 'active', 'archive')
   + 将 (skill-lifecycle, skill_name) 写入 dead-letter
```

### manage.py 需要的改动

| 改动 | 原因 |
|---|---|
| 新增 `--name <skill>` 参数 | NIGHTLY 每次只操作一个 skill，不要 batch |
| `--json` 输出模式 | agent 解析结构化数据，不解析中文 print |
| `cmd_archive` / `cmd_recall` 支持单目标 | 配合 `--name` |
| 返回 exit code 区分：0=成功, 1=无操作, 3=安全拒绝 | 对齐 NIGHTLY safety_check 协议 |

### safety_check.py 改动

放开对 skill 目录的移动操作（当前 `plugins/` 是禁区）：

```python
# 新增白名单规则
if strategy == 'skill-lifecycle':
    allowed_paths = [
        '~/.claude/skills/',
        '~/.claude/_archive_skills/',
        '~/.agents/skills/',
        '~/.agents/_archive_skills/',
    ]
    # 仅允许 skill 目录间的移动，不允许删除或内容修改
```

### 评估指标扩展

标准 replay score 之外，额外记录：

```json
{
  "prompt_token_delta": -1847,
  "active_skill_count_before": 52,
  "active_skill_count_after": 51
}
```

token_delta 作为辅助信号：即使 replay 分数持平，显著的 token 节省（>1000）也可视为正收益。

### 与 strategy_stats.py 的集成

`skill-lifecycle` 作为独立策略参与 effectiveness tracking：
- 按正常 kept/tried 比率计算 promising/neutral/avoid
- 子类型（archive vs recall）不单独追踪，统一为一个策略桶

## 不做的事

- 不修改 manage.py 的核心 scan_usage 逻辑（信号质量是 skill-router 的事）
- 不同时归档/召回多个 skill（NIGHTLY 原则：one change per run）
- 不触碰 `settings.json`（skill 的 active/archive 是文件系统级操作，不走 settings）
- 不自动重建 `build_index.py`（归档/召回后由下次 skill-router 使用时自动触发）

## 依赖

- `~/.agents/skills/skill-router/scripts/manage.py` 已安装且可执行
- Python 3.10+（已有）
- session transcripts 存在于 `~/.claude/projects/`

## 验收标准

1. `nightly --observation` 能生成 `skill-lifecycle` 类型的 proposal
2. dry-run 模式正确识别归档/召回候选
3. revert 能完整还原 skill 位置（包括 symlink 修复）
4. dead-letter 阻止重复操作同一 skill
5. strategy_stats 正确追踪 skill-lifecycle 的 kept/tried
