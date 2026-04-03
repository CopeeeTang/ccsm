# CCSM Lineage 检测系统 — 跨 Session 关系识别

> 更新日期: 2026-04-02 | 基于 ccsm/core/lineage.py 实现

## 1. 概述

Claude Code 的每个 session 是独立的 JSONL 文件（存储在 `~/.claude/projects/{encoded}/` 下），文件之间**没有存储任何跨 session 关系**。用户执行 `/branch`、触发 compact、或从多台终端 SSH 到同一服务器时，Claude Code 不会在 JSONL 中记录这些操作产生的 session 间关联。

CCSM 的 lineage 系统通过**启发式规则**从 JSONL 文件中检测三种跨 session 关系：

| 关系类型 | 含义 | 典型场景 |
|----------|------|----------|
| **Fork** | 从另一个 session 分支出来 | 用户执行 `/branch` |
| **Compact** | 同一 session 内的压缩延续 | 长对话触发 context compaction |
| **Duplicate** | 同一工作的并行重复 | 两台笔记本 SSH 到同一服务器 |

关键设计约束：**不使用 AI**。Lineage 检测的规则是确定性的，所有信号都可以从 JSONL 结构中直接提取。对于 TUI 渲染场景，每个 session 需要在 ~3ms 内完成信号提取，AI 推理的延迟完全不可接受。

## 2. 三种关系类型

### 2.1 Fork（分支）

Fork 表示一个 session 是从另一个 session 分支出来的。Claude Code 的 `/branch` 命令会创建新 session，并将原 session 的 compact summary 作为首条消息注入。

**检测策略（二选一命中即标记）：**

**策略 A — `display_name` 后缀检测**

Claude Code 为 fork session 自动设置 `display_name`，格式为 `"原标题 (branch)"`。CCSM 检查 `display_name` 是否以 `(branch)` 结尾：

```python
if display_name and display_name.endswith("(branch)"):
    signals.is_fork = True
    signals.fork_hint = "display_name_branch_suffix"
```

`display_name` 来源于 `~/.claude/history.jsonl`，不在 session JSONL 文件内。`parse_lineage_signals()` 接受 `display_name` 作为可选参数。

**策略 B — 首条消息内容检测**

当 fork session 没有 `display_name`（或 `display_name` 被用户改名）时，CCSM 检查首条 user 消息是否以 compact summary 前缀开头：

```python
_COMPACT_SUMMARY_PREFIXES = (
    "Here is a summary of the conversation",
    "Here's a summary of the conversation",
    "Here is a summary of our conversation",
    "Here's a summary of our conversation",
)
```

匹配时标记：
```python
signals.is_fork = True
signals.fork_hint = "compact_summary_first_message"
```

注意：此策略使用 `startswith` 匹配（非精确匹配），因为 compact summary 前缀后面紧跟实际的总结内容。

### 2.2 Compact（压缩延续）

Compact 表示一个长期 session 在 JSONL 文件内部经历了 context 压缩。Claude Code 在触发 compaction 时会在 JSONL 中插入一条特殊的分隔条目。

**检测策略：**

扫描 JSONL 文件，查找 `compact_boundary` 条目：

```json
{"type": "system", "subtype": "compact_boundary"}
```

每发现一条，`compact_count` 加 1：

```python
if entry_type == "system" and entry_subtype == "compact_boundary":
    signals.compact_count += 1
    signals.has_compact_boundary = True
```

`compact_count` 记录该 session 经历的 compact 次数。例如，一个极长的 session 可能有 `compact_count = 3`，意味着它经历了三次压缩。

Compact 是**同一 JSONL 文件内部**的关系（不跨文件），因此在 DAG 构建时标记为 `compact_predecessor = sid`（自引用）。

### 2.3 Duplicate（重复）

Duplicate 表示两个 session 实际上在做同一件事，通常是用户从多个终端同时操作导致的。

**检测策略：**

1. 按 `(cwd, git_branch)` 对所有 session 分组
2. 跳过 `cwd` 和 `git_branch` 都为 None 的分组
3. 组内按 `first_message_at` 升序排序
4. 相邻 session 检查时间间隔：`curr.first_message_at - prev.last_message_at < 300s`
5. 间隔小于 300 秒（5 分钟）的标记为 DUPLICATE

```python
_DUPLICATE_GAP_THRESHOLD = 300  # 5 minutes

gap = (curr_first - prev_last).total_seconds()
if gap < _DUPLICATE_GAP_THRESHOLD:
    graph[curr_sid].lineage_type = LineageType.DUPLICATE
    graph[curr_sid].parent_id = prev_sid
    graph[prev_sid].children.append(curr_sid)
```

注意：负 gap（时间重叠）也会被捕获，因为 `gap < 300` 涵盖了所有负值。已标记为 Fork 的 session 不会被覆盖为 Duplicate（Fork 优先级更高）。

**典型场景：** 用户通过两台笔记本 SSH 到同一台开发服务器，各自启动 `claude`，在相同的 cwd 和 git branch 下工作。两个 session 的时间窗口会高度重叠。

## 3. 数据流

```
JSONL 文件 + display_name
    |
    | parse_lineage_signals(jsonl_path, display_name)
    v
LineageSignals（单文件信号提取）
    |
    | 对每个 session 重复上述步骤，收集 dict[session_id, LineageSignals]
    v
signals_map: dict[str, LineageSignals]
    |
    | build_lineage_graph(signals_map)
    v
dict[str, SessionLineage]（DAG — 有向无环图）
    |
    | 传入 TUI 组件
    v
SessionGraph 渲染 + SessionCard badge 显示
```

完整调用链路（在 `main.py` 中）：

```
_parse_and_display()                     # 后台线程
    |-- parse_session_info()             # 基础 JSONL 解析
    |-- parse_lineage_signals()          # lineage 信号提取（每 session ~3ms）
    |-- classify_all()                   # 状态分类
    |-- build search index               # 搜索索引构建
    |-- get_last_assistant_messages()     # 最后回复提取
    v
_on_sessions_parsed()                    # UI 线程
    |-- lineage_types 传给 SessionListPanel
    |-- lineage_signals 存入 _lineage_signals 供 graph 使用
```

## 4. LineageSignals 数据结构

`LineageSignals` 是从**单个 JSONL 文件**提取的原始信号，不包含跨文件推理：

```python
@dataclass
class LineageSignals:
    session_id: Optional[str] = None          # JSONL 中的 sessionId 字段
    is_fork: bool = False                     # 是否检测为 fork
    fork_hint: Optional[str] = None           # fork 检测来源
                                              #   "display_name_branch_suffix"
                                              #   "compact_summary_first_message"
    has_compact_boundary: bool = False         # 是否包含 compact_boundary 条目
    compact_count: int = 0                    # compact_boundary 出现次数
    last_message_at: Optional[datetime] = None   # 最后一条 user/assistant 消息的时间戳
    first_message_at: Optional[datetime] = None  # 第一条 user/assistant 消息的时间戳
    first_user_content: Optional[str] = None     # 首条 user 消息的文本内容
    cwd: Optional[str] = None                 # 工作目录（最后出现的值）
    git_branch: Optional[str] = None          # Git 分支（最后出现的值）
```

字段提取规则：

| 字段 | 提取策略 | 说明 |
|------|----------|------|
| `session_id` | 首次出现的 `sessionId` | 与文件名 stem 可能不同 |
| `cwd` | 最后出现的 `cwd` 字段 | session 可能切换目录 |
| `git_branch` | 最后出现的 `gitBranch` 字段 | session 可能切换分支 |
| `first_message_at` | 所有 user/assistant 消息中最早的 timestamp | - |
| `last_message_at` | 所有 user/assistant 消息中最晚的 timestamp | 用于替代文件 mtime 排序 |
| `first_user_content` | 第一条 `type=user` 消息的 content | 用于 fork 策略 B 检测 |

`_extract_content()` 辅助函数处理两种 content 格式：
- 纯字符串：直接返回
- content block 数组：提取所有 `type=text` 块并拼接

`_parse_timestamp()` 辅助函数处理多种时间戳格式：
- Unix epoch 秒（整数/浮点）
- Unix epoch 毫秒（`> 1e12` 时自动除以 1000）
- ISO 8601 字符串

## 5. DAG 构建算法

`build_lineage_graph()` 从 `signals_map` 构建有向无环图，分四个阶段：

### Phase 1: 创建节点，标记 Fork

遍历 `signals_map`，为每个 session 创建 `SessionLineage` 节点：
- 如果 `sig.is_fork == True` -> `lineage_type = FORK`，`fork_label = sig.fork_hint`
- 否则 -> `lineage_type = ROOT`

```python
for sid, sig in signals_map.items():
    lineage_type = LineageType.FORK if sig.is_fork else LineageType.ROOT
    node = SessionLineage(
        session_id=sid,
        lineage_type=lineage_type,
        fork_label=sig.fork_hint if sig.is_fork else None,
    )
    graph[sid] = node
```

### Phase 1.5: 标记 Compact Sessions

遍历 `signals_map`，将包含 `compact_boundary` 且尚未标记为 FORK 的 session 标记为 COMPACT：

```python
for sid, sig in signals_map.items():
    if sig.has_compact_boundary and graph[sid].lineage_type == LineageType.ROOT:
        graph[sid].lineage_type = LineageType.COMPACT
        graph[sid].compact_predecessor = sid  # 自引用：在同一文件内压缩
```

优先级规则：**FORK > COMPACT > ROOT**。如果一个 session 同时是 fork 又有 compact boundary，保留 FORK 标记。

### Phase 2: 检测 Duplicate

1. 按 `(cwd, git_branch)` 分组
2. 跳过两个字段都为 None 的分组
3. 组内按 `first_message_at` 排序（None 排到末尾）
4. 相邻 session 检查：
   - 跳过已标记为 FORK 的 session（保留 fork 信号）
   - 计算 `gap = curr.first_message_at - prev.last_message_at`
   - 如果 `gap < 300s`，标记 `curr` 为 DUPLICATE，建立 parent-child 边

```python
gap = (curr_first - prev_last).total_seconds()
if gap < _DUPLICATE_GAP_THRESHOLD:
    graph[curr_sid].lineage_type = LineageType.DUPLICATE
    graph[curr_sid].parent_id = prev_sid
    graph[prev_sid].children.append(curr_sid)
```

优先级规则：**FORK > DUPLICATE**。已标记为 FORK 的 session 不会被覆盖。

### Phase 3: 分配 Depth

从所有没有 `parent_id` 的根节点开始，递归深度优先遍历，为每个节点分配 `depth`：

```python
def _assign_depth(sid, depth):
    if sid in visited: return
    visited.add(sid)
    graph[sid].depth = depth
    for child_id in graph[sid].children:
        _assign_depth(child_id, depth + 1)

# 从根节点开始
for sid, node in graph.items():
    if node.parent_id is None:
        _assign_depth(sid, 0)

# 未访问的孤立节点 depth = 0
for sid in graph:
    if sid not in visited:
        graph[sid].depth = 0
```

## 6. 已知限制

1. **Fork 检测覆盖不完整** — 策略 A 依赖 `(branch)` 后缀，策略 B 依赖 4 种 compact summary 前缀。如果 Claude Code 更新了前缀格式、用户自定义了 fork 方式、或 `display_name` 被手动修改，fork 可能漏检。

2. **缺少 `"Summary of conversation so far"` 前缀** — 用户规格中提到了 `"Summary of conversation so far"` 和 `"Conversation summary:"` 两个前缀，但当前实现中未包含。如果 Claude Code 使用这些变体，fork 会漏检。

3. **Duplicate 阈值固定** — 300 秒（5 分钟）是硬编码的，不可配置。对于某些场景（如用户快速切换但间隔超过 5 分钟）会漏检，对另一些场景（如恰好在 5 分钟内启动的独立 session）会误检。

4. **不检测跨 worktree 关系** — Lineage 检测在单个 worktree 的 session 集合上运行。如果用户在 worktree A 的 session 中 fork 出一个在 worktree B 的 session，无法检测。

5. **Fork 无法追溯源 session** — 当前检测只能标记"这是一个 fork"，但无法确定它是从哪个 session fork 出来的（`fork_source` 字段始终为 None）。这是因为 Claude Code 不在 JSONL 中记录 fork 来源。

6. **Compact 无跨文件关系** — Compact boundary 是同一 JSONL 文件内的标记，`compact_predecessor` 设为自引用。如果未来 Claude Code 将 compact 后的内容写入新文件，当前检测无法关联。

7. **v2 计划** — 通过 AI 聚类补充语义关联检测（`cluster.py`），解决启发式规则的覆盖盲区。Workflow 聚类（`workflow.py`）将 compact chain 串联为逻辑工作流。

## 7. TUI 集成

### session_card.py — Lineage Badge

SessionCard 在标题行显示 lineage 类型的彩色 badge：

```python
_LINEAGE_BADGES = {
    "fork":      ("[#60a5fa]fork_icon[/]", 2),    # 蓝色 fork 图标
    "compact":   ("[#a78bfa]compact_icon[/]", 2),  # 紫色 compact 图标
    "duplicate": ("[#f87171]dup_icon[/]", 2),      # 红色 duplicate 图标
}
```

颜色语义：
- **蓝色** (`#60a5fa`) — Fork：从其他 session 分支
- **紫色** (`#a78bfa`) — Compact：经历了 context 压缩
- **红色** (`#f87171`) — Duplicate：疑似重复 session

`lineage_type` 参数从 `main.py` 的 `_lineage_types` 字典传入 `SessionCard` 构造函数。

### session_graph.py — DAG 渲染

SessionGraph widget 将 lineage DAG 渲染为 Unicode 树形结构：

```
  fix-login-bug                    Apr 1 10:00
  |
  +-- add-auth fork                Apr 1 12:00
  |   \-- auth-debug compact       Apr 1 14:00
  |
  +-- fix-login-v2 dup             Apr 1 10:28
  |
  \-- refactor-api                 Apr 2 09:00
```

节点图标映射：

| LineageType | 图标 | 含义 |
|-------------|------|------|
| ROOT | `●` | 独立 session（无父节点） |
| FORK | `◆` | 分支 session |
| COMPACT | `◇` | 压缩延续 session |
| DUPLICATE | `◉` | 重复 session |

DAG 渲染算法：
1. 找到所有根节点（`parent_id is None`）
2. 按 timestamp 排序根节点
3. 深度优先遍历，用 `├──` / `└──` / `│` 构建缩进前缀
4. 当前选中的 session 用橙色高亮（`#fb923c`）
5. 时间戳右对齐到 60 列

### main.py — 编排层

`MainScreen._parse_and_display()` 是 lineage 系统的入口点：

1. 对每个 session 调用 `parse_lineage_signals()`，收集 `lineage_signals_local`
2. 根据信号填充 `lineage_types` 字典（`"fork"` / `"compact"`）
3. 将 `lineage_types` 传给 `SessionListPanel.load_sessions()` 供 card badge 显示
4. 将 `lineage_signals` 存入 `self._lineage_signals` 供 graph 按需构建

用户按 `g` 键时，`action_toggle_graph()` 调用 `build_lineage_graph()` 从 `_lineage_signals` 构建 DAG，然后用 `SessionGraph.set_data()` 渲染到 detail panel。

注意：`_parse_and_display()` 中只标记了 fork 和 compact 类型，**duplicate 检测延迟到 `build_lineage_graph()` 中执行**（因为需要跨 session 比较）。这意味着 session card 上的 badge 不会显示 duplicate 标记，只有 graph 视图中才会出现 duplicate 节点。
