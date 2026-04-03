# CCSM 架构概览 — 完整 Pipeline 参考

> 更新日期: 2026-04-02 | 涵盖 v1 (已实现) + v2 (进行中)

## 系统全景

```
                        CCSM — Claude Code Session Manager
                        
  ┌─── DATA SOURCES (read-only) ─────────────────────────────────────┐
  │  ~/.claude/projects/{encoded}/  ← JSONL session files            │
  │  ~/.claude/sessions/*.json      ← running process registry       │
  │  ~/.claude/history.jsonl        ← display names                  │
  └──────────────────────────────────────────────────────────────────┘
                            │
  ┌─── DISCOVERY LAYER ─────▼─────────────────────────────────────────┐
  │  discovery.py                                                      │
  │  ├─ discover_projects()   → Project/Worktree/SessionInfo stubs    │
  │  ├─ load_running_sessions()  → {session_id: True}                 │
  │  ├─ load_display_names()     → {session_id: display_name}         │
  │  └─ detect_duplicates()      → [[dup_group_1], [dup_group_2]]     │
  └───────────────────────────────────────────────────────────────────┘
                            │
  ┌─── PARSING LAYER ───────▼─────────────────────────────────────────┐
  │  parser.py                                                         │
  │  ├─ parse_session_info(jsonl)    → SessionInfo (fast head/tail)   │
  │  ├─ parse_session_timestamps()   → first/last_message_at          │
  │  ├─ parse_session_messages()     → list[JSONLMessage] (full)      │
  │  └─ get_last_assistant_messages()→ last N replies (tail-read)     │
  └───────────────────────────────────────────────────────────────────┘
                            │
  ┌─── SIGNAL LAYER ────────▼─────────────────────────────────────────┐
  │  lineage.py         status.py         milestones.py               │
  │  ├─ LineageSignals  ├─ classify_all() ├─ extract_milestones()     │
  │  ├─ build_lineage_  │  (5-level:      ├─ extract_breakpoint()     │
  │  │  graph() → DAG   │   NOISE>BG>     │  (6 signal types)         │
  │  └─ fork/compact/   │   ACTIVE>IDEA>  └─────────────────────      │
  │     dup detection    │   DONE)                                     │
  └───────────────────────────────────────────────────────────────────┘
                            │
  ┌─── INDEX LAYER ─────────▼─────────────────────────────────────────┐
  │  index.py                                                          │
  │  ├─ SessionIndex.update_entries()  → build in-memory index        │
  │  ├─ SessionIndex.search(query, worktree=, project=, status=)      │
  │  └─ SessionIndex.save() / .load()  → JSON persistence            │
  └───────────────────────────────────────────────────────────────────┘
                            │
  ┌─── AI LAYER ────────────▼──────────────────────────── (async) ────┐
  │  summarizer.py                    cluster.py (v2, planned)        │
  │  ├─ summarize_session()           ├─ cluster_workflows()          │
  │  │  mode="extract" (instant)      │  (AI naming + orphan          │
  │  │  mode="llm" (haiku, ~12s)      │   clustering)                 │
  │  └─ generate_ai_title_sync()      └──────────────────────         │
  │                                                                    │
  │  workflow.py (v2, planned)                                         │
  │  └─ extract_workflows() → WorkflowCluster (deterministic)         │
  └───────────────────────────────────────────────────────────────────┘
                            │
  ┌─── SIDECAR STORAGE ─────▼─────────────────────────────────────────┐
  │  meta.py                ~/.ccsm/                                   │
  │  ├─ load/save_meta()    ├─ meta/{id}.meta.json                    │
  │  ├─ load/save_summary() ├─ summaries/{id}.summary.json            │
  │  ├─ lock_title()        └─ workflows/{proj}_{wt}.json (v2)        │
  │  └─ load_all_meta()                                                │
  └───────────────────────────────────────────────────────────────────┘
                            │
  ┌─── TUI LAYER ───────────▼─────────────────────────────────────────┐
  │                                                                    │
  │  ┌──────────────┬─────────────────────┬──────────────────────┐    │
  │  │ WorktreeTree │ SessionListPanel    │ SessionDetail        │    │
  │  │ (left)       │ (middle)            │ (right)              │    │
  │  │              │                     │                      │    │
  │  │ Projects     │ StatusTabBar        │ 📋 SESSION           │    │
  │  │  └ Worktrees │  ACTIVE|BG|IDEA|DONE│ 🧭 MILESTONES       │    │
  │  │              │ SessionCard[]       │ 📍 BREAKPOINT        │    │
  │  │              │  └ lineage badge    │ 💬 LAST REPLY        │    │
  │  │              │  └ intent           │ 🔗 WORKFLOWS (v2)   │    │
  │  └──────────────┴─────────────────────┴──────────────────────┘    │
  │                                                                    │
  │  MainScreen — async orchestration                                  │
  │  ├─ _load_data()         → discovery (background thread)          │
  │  ├─ _parse_and_display() → parsing + lineage + index (thread)     │
  │  ├─ _load_session_detail()→ meta + summary + replies (thread)     │
  │  ├─ _generate_ai_title() → haiku API (thread, lazy)              │
  │  └─ _try_silent_summary()→ LLM summary (1.5s delay, silent)      │
  └───────────────────────────────────────────────────────────────────┘
```

## 数据流时序

```
用户启动 CCSM
    │
    ▼ on_mount() ──── background thread
    ├─ discover_projects()        # ~200ms, filesystem only
    ├─ load_running_sessions()    # ~50ms, small JSON files
    ├─ load_display_names()       # ~100ms, history.jsonl
    └─ load_all_meta()            # ~50ms, sidecar JSON files
    │
    ▼ _on_data_loaded() ──── UI thread
    ├─ WorktreeTree.load_projects()
    └─ auto-select best worktree (scoring: named > main, 1-100 sessions preferred)
    │
    ▼ _load_worktree_sessions() ──── background thread
    ├─ parse_session_info()       # ~5ms/session, head+tail scan
    ├─ parse_lineage_signals()    # ~3ms/session, type+timestamp only  ← v1 新增
    ├─ overwrite last_timestamp with last_message_at                   ← 痛点 #6 修复
    ├─ classify_all()             # ~1ms total, lightweight heuristics
    ├─ build search index         # ~1ms, in-memory                    ← v1 新增
    └─ get_last_assistant_messages(count=1) per session
    │
    ▼ _on_sessions_parsed() ──── UI thread
    └─ SessionListPanel.load_sessions()
    │
    ▼ 用户点击 session
    ├─ _load_session_detail() ──── background thread
    │   ├─ load_meta()
    │   ├─ get_last_assistant_messages(count=3)
    │   ├─ summarize_session(mode="extract")  # instant, rule-based
    │   └─ generate_ai_title (if missing, lazy)
    │
    └─ 1.5s 后静默触发 LLM summary (if >8 messages, not cached)
```

## 模块依赖图

```
models/session.py ──────────────────────────── 无依赖（纯数据类）
    ↑
    ├── core/parser.py ─────────────────────── 依赖 models
    ├── core/status.py ─────────────────────── 依赖 models
    ├── core/lineage.py ────────────────────── 依赖 models
    ├── core/index.py ──────────────────────── 无 ccsm 依赖（独立）
    ├── core/milestones.py ─────────────────── 依赖 models
    ├── core/meta.py ───────────────────────── 依赖 models
    │       ↑
    │       └── core/summarizer.py ─────────── 依赖 meta, milestones, parser
    │
    └── core/discovery.py ──────────────────── 依赖 models, lineage
            ↑
            └── tui/screens/main.py ────────── 依赖 全部 core 模块
                    ↑
                    └── tui/widgets/*.py ────── 依赖 models, main.py events
```

## 关键设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 数据安全 | 只读 `~/.claude/`，写入 `~/.ccsm/` | 永不破坏 Claude Code 数据 |
| 解析策略 | 延迟解析（先 stub，选中 worktree 才 parse） | 200+ session 的项目需要 <3s 启动 |
| Lineage 检测 | 启发式规则，非 AI | 延迟不可接受，且信号是确定性的 |
| 搜索 | 内存索引，非数据库 | 规模小（<1000 sessions），JSON 持久化足够 |
| AI 调用 | Haiku via 本地代理，异步静默 | 成本低（~$0.01/session），用户无感 |
| 标题持久化 | `title_locked` flag in sidecar | 绕过 Claude Code 的 64KB tail 窗口限制 |
| 排序 | `last_message_at` 替代文件 mtime | 防止"看一眼就排到最前" |
