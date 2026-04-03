# CCSM 搜索索引系统：SessionIndex 技术文档

> 更新日期: 2026-04-02 | 基于 CCSM v1 实现 (`ccsm/core/index.py`)

## 1. 问题背景

Claude Code 的 `/resume` 搜索功能存在三个核心痛点，严重影响多 session 场景下的工作效率。

### 1.1 搜索范围极窄

Claude Code 在构建 session 列表时，只读取 JSONL 文件的 head/tail 各 64KB 窗口，从中提取三个字段用于展示和匹配：

- `customTitle` — 用户手动设置的标题（大多数 session 没有）
- `firstPrompt` — 第一条用户消息
- `lastPrompt` — 最后一条用户消息

这意味着一个持续数小时、包含上百轮对话的 session，实际可搜索的内容只有开头和结尾的两句话。中间的关键讨论、重要决策、错误排查过程全部不可搜索。

### 1.2 数量上限不稳定

`loadMessageLogs()` 的 `limit` 参数在不同调用场景下取值不同：

- `/resume` 交互式选择时传入一个值
- `--continue` 自动恢复时传入另一个值
- 不同版本之间参数可能变化

用户无法确信自己能看到所有 session，也无法控制展示范围。

### 1.3 没有全文索引

Claude Code 没有任何索引机制。每次 `/resume` 都是对原始 JSONL 文件的顺序扫描，不支持关键词搜索、标签过滤或模糊匹配。当 session 数量超过几十个时，用户只能靠记忆和时间顺序来定位目标。

## 2. CCSM 的解决方案：SessionIndex

CCSM 引入了 `SessionIndex` 类（`ccsm/core/index.py`），提供完整的内存搜索索引。

### 设计原则

| 特性 | Claude Code | CCSM SessionIndex |
|------|------------|-------------------|
| 搜索范围 | head/tail 64KB 的 3 个字段 | 全字段：title + intent + branch + first_user_content + tags |
| 数量限制 | fetchLogs(limit) 不稳定 | 无上限，索引全部 session |
| 搜索算法 | 无（顺序展示） | 加权评分 + 模糊匹配 |
| 过滤器 | 无 | worktree / project / status |
| 持久化 | 无 | JSON 序列化/反序列化 |

### 构建时机

索引在 TUI 中选择 worktree 时构建。`MainScreen._scan_sessions()` 解析该 worktree 下所有 session 的元数据和 lineage 信号，然后将结果写入 `self._session_index`：

```python
# ccsm/tui/screens/main.py — 索引构建
index_entries = []
for s in parsed:
    meta = self._all_meta.get(s.session_id)
    index_entries.append(IndexEntry(
        session_id=s.session_id,
        worktree=label,
        project=s.project_dir,
        title=meta.name if meta and meta.name else s.display_title,
        intent=meta.ai_intent if meta else "",
        git_branch=s.git_branch or "",
        first_user_content=s.first_user_content or "",
        last_message_at=s.last_timestamp,
        status=s.status.value if s.status else "",
        tags=meta.tags if meta else [],
    ))
self._session_index.update_entries(index_entries)
```

## 3. IndexEntry 数据结构

```python
@dataclass
class IndexEntry:
    session_id: str
    worktree: str = ""
    project: str = ""
    title: str = ""
    intent: str = ""            # AI 生成的一句话摘要
    git_branch: str = ""
    first_user_content: str = ""
    last_message_at: Optional[datetime] = None
    status: str = ""            # NOISE / BG / ACTIVE / IDEA / DONE
    tags: list[str] = field(default_factory=list)
```

字段来源说明：

| 字段 | 来源 |
|------|------|
| `title` | 用户设置的 display name，或 parser 提取的 `display_title` |
| `intent` | AI summarizer 生成的一句话意图摘要 |
| `git_branch` | 从 session 元数据中提取的 git 分支名 |
| `first_user_content` | 第一条 user 消息的文本内容 |
| `last_message_at` | 通过 `parse_session_timestamps()` 从实际消息中提取（见第 5 节） |
| `status` | 5 级状态分类器的输出（NOISE > BG > ACTIVE > IDEA > DONE） |
| `tags` | 用户或 AI 标注的标签列表 |

## 4. 搜索算法

### 4.1 搜索文本构建

`IndexEntry.search_text()` 将所有可搜索字段拼接为一个小写字符串：

```python
def search_text(self) -> str:
    parts = [
        self.title,
        self.intent,
        self.git_branch,
        self.first_user_content,
        self.session_id[:8],    # session ID 前 8 位，支持 ID 片段搜索
        " ".join(self.tags),
    ]
    return " ".join(parts).lower()
```

### 4.2 搜索流程

`SessionIndex.search()` 方法分三步执行：

**第一步：过滤器**

```python
if worktree is not None:
    candidates = [e for e in candidates if e.worktree == worktree]
if project is not None:
    candidates = [e for e in candidates if e.project == project]
if status is not None:
    candidates = [e for e in candidates if e.status == status]
```

过滤器使用精确匹配，在评分之前缩小候选集。

**第二步：排序**

- 空 query 时：返回所有匹配过滤器的条目，按 `last_message_at` 降序排列
- 非空 query 时：计算加权评分

**第三步：评分规则**

```
title 精确子串匹配:       +10.0 分
intent 精确子串匹配:      +5.0 分
每个 query term 出现在 search_text 中:  +1.0 分
```

评分逻辑：

```python
q = query.lower()
terms = q.split()
for entry in candidates:
    score = 0
    if q in (entry.title or "").lower():
        score += 10
    if q in (entry.intent or "").lower():
        score += 5
    st = entry.search_text()
    for term in terms:
        if term in st:
            score += 1
    if score > 0:
        scored.append((score, _ts(entry), entry))
```

最终排序：先按 score 降序，同分时按 `last_message_at` 降序。score 为 0 的条目被过滤掉。

### 4.3 搜索示例

假设搜索 `"streaming eval"`：

1. 将 query 拆分为 terms: `["streaming", "eval"]`
2. 对每个候选 entry：
   - 如果 title 包含 `"streaming eval"` 子串 → +10
   - 如果 intent 包含 `"streaming eval"` 子串 → +5
   - `"streaming"` 出现在 search_text 中 → +1
   - `"eval"` 出现在 search_text 中 → +1
3. 一个 title 为 "Streaming Eval Pipeline" 的 session 得分 = 10 + 1 + 1 = 12
4. 一个 intent 中提到 "run streaming eval" 的 session 得分 = 5 + 1 + 1 = 7
5. 一个仅在 first_user_content 中出现两个词的 session 得分 = 1 + 1 = 2

## 5. 时间戳语义修复

### 问题：文件 mtime 不可靠

Claude Code 的 session 列表排序依赖文件系统的修改时间（mtime），但这个时间戳存在语义偏差：

1. Claude Code 的 `reAppendSessionMetadata()` 在 session 退出时写入元数据行（custom-title、last-prompt 等）
2. 这些写入操作更新了 JSONL 文件的 mtime
3. 当用户通过 `/resume` 误选了一个 session，看了一眼就退出时，`reAppendSessionMetadata()` 仍然会触发
4. 该 session 的 mtime 被更新为当前时间，导致它跑到列表最前面
5. 结果：一个实际上几天前就停止使用的 session，因为被误点了一次，排序位置发生跳变

### CCSM 的修复

CCSM 使用 `parse_session_timestamps()` 从消息内容中提取真实的 `last_message_at`：

```python
def parse_session_timestamps(jsonl_path: Path) -> SessionTimestamps:
    """只读取 timestamp 和 type 字段 -- 跳过消息内容解析。"""
    for raw_line in lines:
        data = json.loads(raw_line)
        entry_type = data.get("type", "")

        # 只有 user/assistant 消息贡献时间戳
        if entry_type not in ("user", "assistant"):
            continue

        ts = _parse_timestamp(data.get("timestamp"))
        # 更新 first_message_at 和 last_message_at
```

关键设计：

- 只看 `user` 和 `assistant` 类型的消息的 `timestamp` 字段
- 忽略 `system`、`worktree-state`、`custom-title`、`last-prompt` 等元数据行
- 元数据行的写入不影响 `last_message_at` 的值
- 结果：误点一次 `/resume` 不会改变 session 的排序位置

在 TUI 中，lineage 扫描阶段用提取的 `last_message_at` 覆盖默认的文件 mtime：

```python
# Fix pain point #6: use last_message_at from actual messages
if sig.last_message_at:
    s.last_timestamp = sig.last_message_at
```

## 6. Worktree 稳定性

### 问题：Claude Code 的 session 列表随 cwd 漂移

Claude Code 的 `loadMessageLogs()` 依赖 `getProjectDir(getOriginalCwd())` 来确定当前项目目录，只加载该目录下的 session 文件。问题在于：

1. 用户在 `/home/v-tangxin/GUI` 下执行 `/resume`，看到一组 session 列表
2. 选择某个 session 进入后，该 session 的工作目录可能是 `/home/v-tangxin/GUI/.claude/worktrees/panel`
3. 此时如果再次执行 `/resume`，`getOriginalCwd()` 返回的路径变了
4. `getProjectDir()` 计算出不同的 project 目录
5. 显示的 session 集合可能完全不同

这导致用户在多次 `/resume` 之间看到的 session 列表不一致。

### CCSM 的修复

CCSM 的 `SessionIndex` 在 worktree 选择时一次性构建，将所有 session 的 `worktree` 字段写入索引。后续搜索通过 `search(worktree=...)` 参数显式过滤：

```python
# 索引构建时记录 worktree 归属
index_entries.append(IndexEntry(
    session_id=s.session_id,
    worktree=label,     # worktree 标签，在构建时确定
    ...
))

# 搜索时显式按 worktree 过滤
results = self._session_index.search(query, worktree="panel")
```

worktree 的归属关系在索引构建时确定，不依赖当前 cwd，因此搜索结果是稳定的。

## 7. TUI 集成

搜索功能通过 Textual TUI 框架的 Input 组件集成到主界面。

### 交互流程

1. 用户按 `/` 键 → 触发 `action_toggle_search()`
2. 搜索框从隐藏状态变为可见，获得焦点
3. 用户输入每个字符 → 触发 `on_input_changed()` 事件
4. 事件处理器调用 `self._session_index.search(query)` 获取匹配结果
5. 用匹配的 session ID 过滤当前显示列表 `self._current_sessions`
6. 更新 `SessionListPanel` 的内容

### 核心代码

```python
def on_input_changed(self, event: Input.Changed) -> None:
    """Filter session list as user types in search input."""
    if event.input.id != "search-input":
        return
    query = event.value.strip()
    if not query:
        self._update_session_list()      # 空 query → 恢复完整列表
        return

    # 使用索引进行全文搜索
    results = self._session_index.search(query)
    matched_ids = {r.session_id for r in results}
    filtered = [s for s in self._current_sessions if s.session_id in matched_ids]

    panel = self.query_one(SessionListPanel)
    panel.load_sessions(filtered, ...)
```

### 设计细节

- **实时更新**：每次按键都重新搜索，无需按 Enter 确认
- **双重过滤**：索引搜索返回匹配的 session_id 集合，然后用这个集合过滤当前视图的 `_current_sessions` 列表。这样保证了搜索结果仍然携带完整的 SessionInfo 对象（包含 UI 渲染所需的所有字段）
- **空查询恢复**：清空搜索框时，调用 `_update_session_list()` 恢复原始列表

## 8. 持久化

SessionIndex 支持 JSON 格式的序列化与反序列化。

### 保存

```python
def save(self, path: Path) -> None:
    records: list[dict] = []
    for entry in self._entries.values():
        d = asdict(entry)
        if d["last_message_at"] is not None:
            d["last_message_at"] = d["last_message_at"].isoformat()
        records.append(d)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records, indent=2), encoding="utf-8")
```

### 加载

```python
@classmethod
def load(cls, path: Path) -> "SessionIndex":
    # 处理缺失文件、损坏的 JSON、未知字段
    idx = cls()
    if not path.exists():
        return idx
    data = json.loads(path.read_text(encoding="utf-8"))

    known_fields = {f.name for f in IndexEntry.__dataclass_fields__.values()}
    for d in data:
        # 过滤未知字段，防止新旧版本不兼容导致 TypeError
        filtered = {k: v for k, v in d.items() if k in known_fields}
        entries.append(IndexEntry(**filtered))
    idx.update_entries(entries)
    return idx
```

### 容错设计

`load()` 方法包含多层容错：

| 异常场景 | 处理方式 |
|---------|---------|
| 文件不存在 | 返回空索引 |
| JSON 解析失败 | 返回空索引 |
| 顶层不是 list | 返回空索引 |
| 某个 entry 不是 dict | 跳过该条目 |
| 包含未知字段 | 过滤掉未知字段后构建 |
| datetime 解析失败 | 跳过该条目 |

### 当前状态与规划

- **v1（当前）**：每次启动时完整重建索引，不使用持久化缓存
- **v2（计划）**：增量更新 -- 比较文件 mtime 与索引中的 `last_message_at`，只解析有变化的 session 文件
