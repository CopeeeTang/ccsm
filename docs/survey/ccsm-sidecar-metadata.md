# CCSM Sidecar 元数据系统 — Title Lock 与 Lineage 序列化

> 对应 v1 Plan Task 4 | 更新日期: 2026-04-02

## 1. 设计原则

CCSM **永远不修改** Claude Code 的 `~/.claude/` 目录。所有增量数据存储在独立的 `~/.ccsm/` sidecar 目录中：

```
~/.ccsm/
├── meta/
│   └── {session_id}.meta.json     # 用户元数据 + lineage + title_locked
└── summaries/
    └── {session_id}.summary.json  # 里程碑 + breakpoint 缓存
```

这个设计的核心原因：**数据安全**。Claude Code 的 JSONL 文件是 append-only 的，如果 CCSM 往里面写数据，一旦格式出错会破坏整个会话历史。Sidecar 模式完全避免了这个风险。

## 2. 解决的问题

### 痛点 #2: Rename 后标题回退

**根因**: Claude Code 使用 `readHeadAndTail()` 只读 JSONL 文件的首尾各 64KB。标题优先级链是：

```
customTitle(tail) > customTitle(head) > aiTitle(tail) > aiTitle(head) > lastPrompt > firstPrompt
```

当消息持续追加，`custom-title` 条目被推出 64KB 窗口后，`reAppendSessionMetadata()` 理论上会在退出时重写到文件末尾。但如果进程异常退出（SSH 断连、OOM、Ctrl+C 时机不对），re-append 不会执行，标题 fallback 到 `lastPrompt` 或 `firstPrompt`。

**CCSM 的解决方案**: `lock_title()` 将标题存储在 sidecar JSON 文件中，设置 `title_locked = True` 标志。CCSM 的显示逻辑优先读取 sidecar 中的锁定标题，完全绕过 Claude Code 的 64KB 窗口限制。

### 痛点 #6: Resume 选错改变时间轴

**根因**: Claude Code 按文件 mtime 排序 session 列表。仅仅 resume 一个 session（即使立即退出），`reAppendSessionMetadata()` 也会写入元数据，更新 mtime，把该 session 推到列表最前。

**CCSM 的解决方案**: `SessionMeta` 中区分 `last_message_at`（最后实质消息时间）和 `last_accessed_at`（最后访问时间）。列表排序使用 `last_message_at`，"看一眼"不影响排序。

## 3. SessionMeta 数据结构

```python
@dataclass
class SessionMeta:
    session_id: str
    # 原有字段
    name: Optional[str] = None                    # 用户/AI 设置的标题
    status_override: Optional[Status] = None      # 手动状态覆盖
    priority_override: Optional[Priority] = None  # 手动优先级覆盖
    tags: list[str] = field(default_factory=list)
    pinned_messages: list[str] = field(default_factory=list)
    notes: Optional[str] = None
    ai_intent: Optional[str] = None               # AI 生成的一句话摘要
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    # v1 新增字段
    title_locked: bool = False                    # 标题是否被锁定
    last_message_at: Optional[datetime] = None    # 最后实质消息时间
    last_accessed_at: Optional[datetime] = None   # 最后访问时间
    lineage: Optional[SessionLineage] = None      # 血缘关系
```

## 4. Title Lock 机制

### lock_title() API

```python
def lock_title(session_id: str, title: str) -> SessionMeta:
    """设置永久标题，不会被 AI 或 Claude Code 覆盖。"""
    meta = load_meta(session_id)
    meta.name = title
    meta.title_locked = True
    save_meta(meta)
    return meta
```

### 标题优先级链（CCSM 内部）

```
CCSM meta.name (title_locked=True)   ← 最高优先级
  > CCSM meta.name (title_locked=False)
    > Claude Code customTitle (from JSONL tail)
      > AI-generated title
        > display_name (from history.jsonl)
          > slug
            > session_id[:8]              ← fallback
```

### 触发时机

1. **手动**: 用户通过 TUI 操作（目前未暴露 UI，通过 MCP 或 CLI 调用）
2. **自动**: `_generate_ai_title_for()` 在 MainScreen 中生成 AI 标题后，自动调用 `lock_title()` 锁定

## 5. Lineage 序列化

### JSON 格式

```json
{
  "session_id": "abc-123",
  "name": "CCSM架构设计",
  "title_locked": true,
  "last_message_at": "2026-04-02T12:00:00+00:00",
  "last_accessed_at": "2026-04-02T14:00:00+00:00",
  "lineage": {
    "session_id": "abc-123",
    "lineage_type": "fork",
    "parent_id": "parent-456",
    "children": ["child-789"],
    "compact_predecessor": null,
    "fork_source": "parent-456",
    "fork_label": "refactor-branch",
    "depth": 1
  },
  "tags": ["ccsm", "architecture"],
  "ai_intent": "设计 CCSM 的三面板 TUI 架构",
  "created_at": "2026-04-02T10:00:00+00:00",
  "updated_at": "2026-04-02T14:00:00+00:00"
}
```

### 序列化实现

`_meta_to_dict()` 和 `_dict_to_meta()` 处理以下类型转换：

| 字段 | Python 类型 | JSON 类型 | 转换函数 |
|------|------------|----------|---------|
| `title_locked` | `bool` | `boolean` | 直接映射 |
| `last_message_at` | `Optional[datetime]` | `string \| null` | `_dt_to_iso()` / `_iso_to_dt()` |
| `status_override` | `Optional[Status]` | `string \| null` | `_enum_to_str()` / `_str_to_status()` |
| `lineage` | `Optional[SessionLineage]` | `object \| null` | 嵌套 dict 展开/重建 |
| `lineage.lineage_type` | `LineageType` | `string` | `.value` / `LineageType()` |

### 向后兼容

新字段均有默认值（`title_locked=False`、`lineage=None`、`last_message_at=None`），旧的 `.meta.json` 文件不包含这些字段时，`_dict_to_meta()` 使用 `d.get(field, default)` 优雅降级。

## 6. 原子写入

所有 sidecar 文件使用原子写入，防止进程崩溃导致数据损坏：

```python
def _atomic_write_json(path: Path, data: dict) -> None:
    # 1. 写入同目录临时文件
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())  # 确保数据落盘
    # 2. 原子重命名（POSIX 保证原子性）
    os.rename(tmp, path)
```

**为什么用 `os.rename` 而非 `shutil.move`？** POSIX 规范保证同一文件系统内的 `rename()` 是原子操作——要么全部成功，要么不发生。即使在 `rename` 执行过程中断电，也不会出现"写了一半"的损坏文件。

## 7. 批量加载

`load_all_meta()` 在 CCSM 启动时一次性加载所有 sidecar 文件到内存：

```python
def load_all_meta() -> dict[str, SessionMeta]:
    """加载 ~/.ccsm/meta/ 下所有 .meta.json 文件。"""
    meta_dir = get_ccsm_dir() / "meta"
    result = {}
    for path in meta_dir.glob("*.meta.json"):
        session_id = path.stem.replace(".meta", "")
        meta = load_meta(session_id)
        result[session_id] = meta
    return result
```

典型性能：500 个 session 的元数据加载耗时 ~50ms。

## 8. update_meta() 便捷 API

支持增量更新而非全量覆盖：

```python
# 直接字段设置
update_meta("sess-1", name="新标题", notes="重要会话")

# 标签管理（自动去重）
update_meta("sess-1", add_tags=["bug", "urgent"])
update_meta("sess-1", remove_tags=["urgent"])

# 状态覆盖
update_meta("sess-1", status_override="done")
update_meta("sess-1", status_override=None)  # 清除覆盖，恢复自动推断
```

## 9. 安全性

- **路径穿越防护**: Session ID 通过 `^[a-zA-Z0-9_-]+$` 正则验证，防止 `../../etc/passwd` 类攻击
- **原子写入**: 防止崩溃导致的文件损坏
- **权限隔离**: `~/.ccsm/` 目录只存储 CCSM 自己的元数据，不接触 Claude Code 数据

## 10. 测试覆盖

`tests/test_meta.py` 覆盖以下场景：

| 测试 | 验证内容 |
|------|---------|
| `test_meta_round_trip_new_fields` | `title_locked`、`last_message_at`、`lineage` 的 save/load 往返 |
| `test_meta_lock_title` | `lock_title()` 设置 name 和 title_locked |
| `test_save_load_roundtrip` | 含中文字符的完整 CRUD |
| `test_corrupt_json_recovery` | 损坏 JSON 文件的优雅降级 |
| `test_null_none_preservation` | None 值在序列化后保持为 None |
