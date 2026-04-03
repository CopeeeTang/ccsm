# CCSM TUI Pipeline 集成 — MainScreen 异步编排

> 对应 v1 Plan Task 7 | 更新日期: 2026-04-02

## 1. 概述

CCSM 的 TUI 层是一个 **Textual** 应用，核心是 `MainScreen`（`tui/screens/main.py`，744 行）。它负责将所有 core 模块的输出编排到三面板布局中，使用 **background thread + UI callback** 模式保持界面响应。

本文档说明 v1 新增模块（lineage、index、parser timestamps）如何集成到已有的 TUI pipeline 中。

## 2. MainScreen 实例变量

```python
class MainScreen(Screen):
    def __init__(self):
        # 原有
        self._projects: list[Project]
        self._all_sessions: list[SessionInfo]
        self._current_sessions: list[SessionInfo]
        self._selected_session: Optional[SessionInfo]
        self._all_meta: dict[str, SessionMeta]
        self._last_thoughts: dict[str, str]
        self._running: dict[str, bool]
        self._display_names: dict[str, str]
        # v1 新增
        self._lineage_signals: dict[str, LineageSignals]  # 血缘信号缓存
        self._lineage_types: dict[str, str]               # session_id → "fork"/"compact"/"duplicate"
        self._session_index: SessionIndex                  # 搜索索引
        self._graph_visible: bool                          # Graph 视图开关
```

## 3. 完整数据流

```
用户启动 CCSM
    │
    ▼ on_mount()
    └─ @work _load_data() ────────────────── background thread
        ├─ discover_projects()                 # ~200ms, 扫描 ~/.claude/projects/
        ├─ load_running_sessions()             # ~50ms, 读取 ~/.claude/sessions/*.json
        ├─ load_display_names()                # ~100ms, 解析 ~/.claude/history.jsonl
        ├─ load_all_meta()                     # ~50ms, 读取 ~/.ccsm/meta/*.json
        ├─ 合并: session.display_name ← display_names
        ├─ 合并: session.is_running ← running
        └─ call_from_thread(_on_data_loaded)
            │
            ▼ _on_data_loaded() ──────────── UI thread
            ├─ WorktreeTree.load_projects()    # 左面板: 项目/分支树
            ├─ 自动评分选择最佳 worktree
            │   score = count判断 + named_worktree加分
            └─ _load_worktree_sessions(best)
                │
                ▼ @work _load_worktree_sessions() ── background thread
                └─ _parse_and_display(sessions, label)
                    │
                    ├─ [1] parse_session_info()           # 每个 session ~5ms
                    │      提取: slug, cwd, branch, timestamps, message counts
                    │
                    ├─ [2] ★ parse_lineage_signals()      # 每个 session ~3ms  ← v1 新增
                    │      提取: is_fork, has_compact, last_message_at
                    │      缓存到 self._lineage_signals
                    │
                    ├─ [3] ★ 修正时间戳                    ← 痛点 #6 修复
                    │      if sig.last_message_at:
                    │          session.last_timestamp = sig.last_message_at
                    │
                    ├─ [4] classify_all()                  # ~1ms, 轻量启发式
                    │      NOISE > BACKGROUND > ACTIVE > IDEA > DONE
                    │
                    ├─ [5] ★ 构建搜索索引                  ← 痛点 #3,#4 修复
                    │      for session in sessions:
                    │          IndexEntry(title, intent, branch, content, ...)
                    │      self._session_index.update_entries(entries)
                    │
                    ├─ [6] get_last_assistant_messages()   # 每个非 NOISE session
                    │      缓存到 self._last_thoughts
                    │
                    ├─ [7] ★ 构建 lineage_types dict       ← 痛点 #1 修复
                    │      for session in sessions:
                    │          if sig.is_fork: types[id] = "fork"
                    │          elif sig.has_compact_boundary: types[id] = "compact"
                    │
                    └─ call_from_thread(_on_sessions_parsed)
                        │
                        ▼ _on_sessions_parsed() ──── UI thread
                        └─ SessionListPanel.load_sessions(
                               sessions, meta, thoughts, lineage_types
                           )
                           │
                           ├─ StatusTabBar 更新计数
                           ├─ 过滤当前 tab 的 sessions
                           ├─ 排序: is_running DESC → last_timestamp DESC
                           └─ 渲染 SessionCard (含 lineage badge)
```

## 4. Session 选择流

```
用户点击 session card
    │
    ▼ on_session_list_panel_session_selected()
    ├─ 保存 _selected_session
    ├─ 取消 _auto_summary_timer
    ├─ @work _load_session_detail() ────── background thread
    │   ├─ load_meta()
    │   ├─ get_last_assistant_messages(count=3)
    │   ├─ summarize_session(mode="extract")   # 规则提取, 即时
    │   ├─ call_from_thread(_on_detail_loaded)
    │   └─ ★ _generate_ai_title_for()         # 如果无标题, 异步生成
    │       ├─ generate_ai_title_sync()        # Haiku API
    │       ├─ session.display_name = title
    │       └─ lock_title(session_id, title)   ← 痛点 #2 修复
    │
    └─ 1.5s 定时器 → _try_silent_summary()
        └─ if >8 messages AND not NOISE AND no cached LLM summary:
            _run_llm_summarize(silent=True)
```

## 5. 搜索集成

v1 之前的搜索是内联的 substring match，直接在 `on_input_changed()` 中遍历 `_current_sessions`。v1 替换为 `SessionIndex`：

```
用户按 /
    │
    ▼ action_search()
    ├─ 显示 #search-input Input 框
    └─ focus 到输入框

用户输入关键词
    │
    ▼ on_input_changed()
    ├─ query = event.value.strip()
    ├─ if not query:
    │      恢复完整列表 (_update_session_list)
    │      return
    ├─ ★ results = _session_index.search(query)  ← 全文模糊搜索
    ├─ matched_ids = {r.session_id for r in results}
    ├─ filtered = [s for s in _current_sessions if s.session_id in matched_ids]
    └─ panel.load_sessions(filtered, meta, thoughts, lineage_types)
```

**与 Claude Code `/resume` 搜索的对比：**

| 维度 | Claude Code | CCSM |
|------|------------|------|
| 搜索范围 | head/tail 64KB 窗口内的 customTitle/firstPrompt/lastPrompt | title + intent + content + branch + tags + session_id |
| 结果数量 | 受 `fetchLogs(limit)` 限制，20-50 不等 | 无上限 |
| 稳定性 | 依赖 cwd，resume 后可能变化 | worktree 选择时固定 |
| 搜索质量 | 简单 substring | 加权评分 (title +10, intent +5, term +1) |

## 6. Graph 视图集成

```
用户按 g
    │
    ▼ action_toggle_graph()
    ├─ _graph_visible = !_graph_visible
    ├─ if _graph_visible AND _selected_session:
    │      _show_graph()
    │      ├─ build_lineage_graph(_lineage_signals) → DAG
    │      ├─ 收集 titles 和 timestamps
    │      ├─ SessionDetail.remove_children()
    │      ├─ mount SessionGraph widget
    │      └─ SessionGraph.set_data(graph, titles, timestamps, current_id)
    │          └─ _build_tree() → 扁平化 DAG → _GraphNode 列表
    │          └─ _render() → Unicode 树形图 + Rich markup
    │
    └─ if !_graph_visible:
           _load_session_detail()   # 恢复正常 detail 视图
```

## 7. 异步编排模式

MainScreen 使用 Textual 的 `@work(thread=True)` 装饰器将耗时操作放到后台线程，通过 `call_from_thread()` 回调 UI 线程更新界面。

### 防竞态设计

```python
# 在 _load_session_detail 的 UI 回调中：
def _on_detail_loaded(self, session, meta, summary, replies):
    if session.session_id != self._selected_session.session_id:
        return  # 用户已切换到其他 session，丢弃过期结果
    detail.show_session(session, meta, summary, replies)
```

类似的 guard 在 `_try_silent_summary()` 和 `_generate_ai_title_for()` 中也存在——防止后台线程返回结果时用户已经切换了选中项。

### 线程模型

| 操作 | 线程 | 耗时 |
|------|------|------|
| `_load_data()` | background | ~400ms |
| `_parse_and_display()` | background | ~5ms/session × N |
| `_load_session_detail()` | background | ~50ms |
| `_generate_ai_title_for()` | background | ~2-5s (API) |
| `_run_llm_summarize()` | background | ~12s (API) |
| `_on_*()` callbacks | UI | <1ms |
| `_rebuild()` in widgets | UI | <10ms |

## 8. Keybinding 全表

```python
BINDINGS = [
    ("q", "quit", "Quit"),
    ("r", "resume_session", "Resume"),
    ("s", "summarize_llm", "AI Summary"),
    ("h", "toggle_noise", "Toggle noise"),
    ("/", "search", "Search"),
    ("tab", "focus_next_panel", "Next Panel"),
    ("shift+tab", "focus_previous_panel", "Prev Panel"),
    ("1", "switch_tab_1", "Active"),
    ("2", "switch_tab_2", "Back"),
    ("3", "switch_tab_3", "Idea"),
    ("4", "switch_tab_4", "Done"),
    ("D", "batch_archive", "Archive"),
    ("g", "toggle_graph", "Graph"),       # ← v1 新增
]
```

## 9. Widget 间通信

```
WorktreeTree
    ├─ WorktreeSelected(worktree, project) → MainScreen.on_worktree_tree_worktree_selected()
    └─ ProjectSelected(project)            → MainScreen.on_worktree_tree_project_selected()

SessionCard
    └─ CardSelected(session)               → SessionListPanel.on_session_card_card_selected()

SessionListPanel
    └─ SessionSelected(session)            → MainScreen.on_session_list_panel_session_selected()

StatusTabBar
    └─ TabChanged(status)                  → SessionListPanel.on_status_tab_bar_tab_changed()
```

所有通信使用 Textual 的 `Message` 机制（冒泡事件），不使用直接方法调用，保持 widget 间的解耦。

## 10. v1 修改清单

在 MainScreen 中为集成 v1 模块所做的具体修改：

| 位置 | 修改内容 | 解决痛点 |
|------|---------|---------|
| `__init__()` | 新增 `_lineage_signals`, `_lineage_types`, `_session_index`, `_graph_visible` | — |
| `_parse_and_display()` | 插入 lineage scanning 循环 | #1,#5,#7 |
| `_parse_and_display()` | 用 `sig.last_message_at` 覆盖 `s.last_timestamp` | #6 |
| `_parse_and_display()` | 构建 `SessionIndex` | #3,#4 |
| `_parse_and_display()` | 构建 `_lineage_types` dict | #1 |
| `on_input_changed()` | 替换内联搜索为 `_session_index.search()` | #4 |
| `_generate_ai_title_for()` | 生成标题后调用 `lock_title()` | #2 |
| `action_toggle_graph()` | 新增 Graph 视图切换 | #8 |
| `_show_graph()` | 构建 DAG 并渲染 `SessionGraph` | #8 |
| `SessionListPanel.load_sessions()` | 新增 `lineage_types` 参数 | #1 |
| `SessionCard.__init__()` | 新增 `lineage_type` 参数 | #1 |
| `SessionCard.render()` | 渲染 lineage badge（fork=蓝, compact=紫, dup=红） | #1 |
