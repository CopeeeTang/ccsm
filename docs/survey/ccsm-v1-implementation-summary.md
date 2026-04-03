# CCSM v1 实现总结 — Resume 痛点修复

> 完成日期: 2026-04-02 | 基于 Plan: `docs/superpowers/plans/2026-04-02-ccsm-resume-painpoints.md`

## 1. 背景

Claude Code 的 `/resume` 机制存在 8 个痛点（详见原始分析）。CCSM v1 通过 **9 个 Task** 在不修改 Claude Code 数据的前提下，在 `~/.ccsm/` sidecar 层面解决了这些问题。

## 2. 痛点与修复映射

| # | 痛点 | 根因 | 修复模块 | 验证状态 |
|---|------|------|---------|---------|
| 1 | Fork session 名字只多 (branch) | 无 fork 血缘信息 | `lineage.py` fork 检测 + `session_card.py` badge | ✅ 33/33 tests |
| 2 | Rename 后标题回退 | 64KB tail 窗口 + 崩溃丢 re-append | `meta.py:lock_title()` sidecar 锁定 | ✅ |
| 3 | Worktree 下 resume 列表不稳定 | cwd 切换导致 projectDir 变化 | `index.py` worktree-scoped 索引 | ✅ |
| 4 | 搜索形同虚设，数量不稳定 | head/tail 64KB 扫描 + 无统一索引 | `index.py` 全文模糊搜索，无上限 | ✅ |
| 5 | Compact 后产生幽灵 session | compact_boundary + session ID 变化 | `lineage.py` compact 检测 + COMPACT 类型标记 | ✅ |
| 6 | Resume 选错改变时间轴 | 按文件 mtime 排序 | `parser.py:parse_session_timestamps()` 用 last_message_at | ✅ |
| 7 | 多 SSH 终端产生重复 session | 每个进程生成独立 UUID | `discovery.py:detect_duplicates()` | ✅ |
| 8 | 无 session 关系可视化 | 无跨 session 关系存储 | `session_graph.py` DAG 渲染 (v1 基础版) | ✅ |

## 3. 新增模块概览

### 3.1 `core/lineage.py` — 血缘信号检测

**职责：** 从 JSONL 文件检测 fork/compact/duplicate 信号，构建 session DAG。

**核心数据结构：**
```python
@dataclass
class LineageSignals:
    session_id: Optional[str]
    is_fork: bool               # 是否为 fork session
    fork_hint: Optional[str]    # 检测来源 (display_name_branch_suffix / compact_summary_first_message)
    has_compact_boundary: bool  # 是否包含 compact_boundary
    compact_count: int          # compact 次数
    last_message_at: Optional[datetime]   # 最后一条实质消息时间
    first_message_at: Optional[datetime]  # 第一条消息时间
    first_user_content: Optional[str]     # 首条用户消息 (200 chars)
    cwd: Optional[str]          # 工作目录
    git_branch: Optional[str]   # git 分支
```

**两个公开 API：**
- `parse_lineage_signals(jsonl_path, display_name?) → LineageSignals` — 单文件信号提取
- `build_lineage_graph(signals_map) → dict[str, SessionLineage]` — 跨 session DAG 构建

**Fork 检测策略（3种）：**
1. `display_name` 以 `(branch)` 结尾
2. 首条用户消息以 compact summary 前缀开头（如 "Here is a summary of the conversation"）
3. 通过 DAG 的 cwd+branch+时间重叠检测

**Duplicate 检测策略：**
- 同 `(cwd, git_branch)` + 时间间隔 < 300s → 标记为 DUPLICATE，建立 parent→child 关系

### 3.2 `core/index.py` — 持久化搜索索引

**职责：** 提供全文模糊搜索，替代 Claude Code 的 head/tail 64KB 扫描。

**核心结构：**
```python
@dataclass
class IndexEntry:
    session_id: str
    worktree: str
    project: str
    title: str
    intent: str          # AI 生成的一句话摘要
    git_branch: str
    first_user_content: str
    last_message_at: Optional[datetime]
    status: str
    tags: list[str]
```

**搜索逻辑：**
- 空 query → 返回所有条目（按时间倒序）
- 非空 query → 对 `search_text()` 拼接串做 term-by-term matching
  - title 匹配权重 10.0，intent 匹配 5.0，其他 1.0
- 支持 `worktree=`, `project=`, `status=` 过滤器
- 无数量上限（解决痛点 #4）

**持久化：** JSON 格式，`SessionIndex.save(path)` / `SessionIndex.load(path)`

### 3.3 `core/parser.py` 扩展 — `parse_session_timestamps()`

**职责：** 快速提取最后实质消息时间（user/assistant），忽略 metadata 条目。

```python
@dataclass
class SessionTimestamps:
    first_message_at: Optional[datetime]
    last_message_at: Optional[datetime]  # ← 核心：替代文件 mtime
    compact_count: int
```

**设计决策：** 只扫描 `type` 和 `timestamp` 字段，不解析 message content，保持 O(n) 且常数极小。

### 3.4 `core/meta.py` 扩展

**新增 API：**
- `lock_title(session_id, title)` — 设置 `title_locked=True`，写入 sidecar，防止 Claude Code 的 re-append 覆盖

**SessionMeta 新字段：**
- `title_locked: bool` — 标题是否被锁定
- `last_message_at: Optional[datetime]` — 最后实质消息时间
- `last_accessed_at: Optional[datetime]` — 最后访问时间（区分"看了一眼"和"实际发消息"）
- `lineage: Optional[SessionLineage]` — 血缘关系

**序列化格式（JSON）：**
```json
{
  "lineage": {
    "session_id": "abc-123",
    "lineage_type": "fork",
    "parent_id": "parent-456",
    "children": [],
    "fork_label": "refactor",
    "depth": 1
  }
}
```

### 3.5 `core/discovery.py` 扩展 — `detect_duplicates()`

**职责：** 检测因多 SSH 终端产生的重复 session。

**算法：** 按 `(cwd, git_branch)` 分组 → 组内按 `first_message_at` 排序 → 相邻 session 时间间隔 < 300s 的归为一个 cluster。

### 3.6 `models/session.py` 扩展

**新增类型：**
```python
class LineageType(str, Enum):
    ROOT = "root"           # 独立 session
    FORK = "fork"           # 从另一个 session 分支
    COMPACT = "compact"     # compaction 后的延续
    DUPLICATE = "duplicate" # 多 SSH 产生的重复

@dataclass
class SessionLineage:
    session_id: str
    lineage_type: LineageType = LineageType.ROOT
    parent_id: Optional[str] = None
    children: list[str] = field(default_factory=list)
    compact_predecessor: Optional[str] = None
    fork_source: Optional[str] = None
    fork_label: Optional[str] = None
    depth: int = 0
```

### 3.7 TUI 集成变更

- **`main.py`**: 在 `_parse_and_display()` 中集成 lineage scanning → 修正 `last_timestamp` → 构建搜索索引
- **`session_card.py`**: 渲染 lineage badge（fork=蓝, compact=紫, dup=红）
- **`session_list.py`**: `load_sessions()` 接受 `lineage_types` 参数，搜索改用 `SessionIndex`
- **`session_graph.py`**: DAG 渲染（v1 基础版 git-log 风格，将在 v2 中替换为泳道）

## 4. 测试覆盖

```
tests/test_models.py          — 4 tests (LineageType, SessionLineage, SessionMeta 新字段)
tests/test_lineage.py         — 9 tests (fork/compact/dup 检测 + DAG 构建)
tests/test_parser_enhanced.py — 3 tests (timestamp 提取)
tests/test_meta.py            — 2 tests (新字段序列化 + lock_title 往返)
tests/test_index.py           — 8 tests (搜索 + 过滤 + 持久化)
tests/test_discovery_enhanced.py — 3 tests (duplicate 检测)
tests/test_e2e_pipeline.py    — 4 tests (完整 pipeline 冒烟)
────────────────────────────
Total: 33 tests, 0.54s, ALL PASSED
```

## 5. 数据存储

CCSM 永远不修改 Claude Code 的 `~/.claude/` 目录。所有增量数据存储在：

```
~/.ccsm/
├── meta/{session_id}.meta.json     # 含 lineage, title_locked, timestamps
├── summaries/{session_id}.summary.json
└── index/                          # 搜索索引缓存（待实现持久化触发）
```

## 6. 已知限制 & v2 待解决

1. **`session_graph.py` 是纯线性 git-log 风格** — 无法清晰展示 compact 链的因果关系 → v2 泳道设计
2. **无 AI 工作流命名** — compact 链没有语义名称，只有 `title1 → title2` → v2 cluster.py
3. **Lineage 检测是启发式的** — fork 检测依赖 `(branch)` 后缀和 compact summary 前缀，可能漏检
4. **Index 未自动持久化** — 每次启动重建索引，未实现增量更新
5. **未实现跨 session 主题聚类** — 孤立 session 之间的语义关联需要 AI → v2 cluster.py
