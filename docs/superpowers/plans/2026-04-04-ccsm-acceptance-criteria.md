# CCSM 全系统评估与验收标准

> 版本: v1.0 | 日期: 2026-04-04
> 测试脚本: `tests/test_acceptance.py`

---

## 一、评估总览

| 维度 | 测试项 | 自动化 | Pass 标准 |
|------|--------|--------|-----------|
| **功能正确性** | 27 项 | ✅ 全自动 | 100% Pass |
| **性能指标** | 5 项 | ✅ 全自动 | 全部在基线内 |
| **集成流程** | 6 项 | 🔸 半自动 | Plugin 结构 Pass + MCP 可调用 |
| **代码质量** | 8 项 | ✅ 全自动 | 0 Critical, 0 High |
| **实际使用** | 4 场景 | 🔸 半自动 | 4 个场景均可完成 |

---

## 二、功能正确性 (27 项)

### 2.1 数据发现层 (`core/discovery.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-01 | 扫描 `~/.claude/projects/` 发现所有项目 | 返回 Project 列表, len > 0 | `test_discovery_finds_projects` |
| F-02 | 编码路径解码正确 | `-home-v-tangxin-GUI` → `GUI` | `test_decode_project_path` |
| F-03 | Worktree 分离解码 | `--claude-worktrees-panel` → wt=`panel` | `test_decode_worktree_path` |
| F-04 | 运行中会话检测 | PID 有效 → `is_running=True` | `test_running_sessions` |
| F-05 | display_name 从 history.jsonl 加载 | session_id → display_name 映射正确 | `test_display_names_loaded` |

### 2.2 JSONL 解析层 (`core/parser.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-06 | 解析 session_id, slug, cwd, git_branch | 非空且类型正确 | *已有: `test_parse_timestamps_basic`* |
| F-07 | 解析消息时间戳 | first_timestamp < last_timestamp | *已有: `test_parse_timestamps_basic`* |
| F-08 | Compact boundary 检测 | `compact_summaries` 非空 | *已有: `test_compact_boundary_detected`* |

### 2.3 元数据层 (`core/meta.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-09 | Meta 读写往返一致 | save → load → 所有字段相等 | *已有: `test_meta_round_trip_new_fields`* |
| F-10 | 标题锁定 | `lock_title()` → `title_locked=True`, `name` 设置 | *已有: `test_meta_lock_title`* |
| F-11 | Summary 缓存读写 | save_summary → load_summary → 内容一致 | `test_summary_round_trip` |
| F-12 | update_meta 增量更新 | add_tags 不重复, remove_tags 生效 | `test_update_meta_incremental` |

### 2.4 状态分类 (`core/status.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-13 | NOISE 检测 | 消息<3 或 用户字符<50 → NOISE | `test_classify_noise` |
| F-14 | ACTIVE 检测 | 24h 内有活动 → ACTIVE | `test_classify_active` |
| F-15 | DONE 检测 | 48h 无活动 → DONE | `test_classify_done` |
| F-16 | Priority 映射 | ACTIVE→FOCUS, NOISE→HIDE | `test_priority_mapping` |

### 2.5 标题系统 (`models/session.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-17 | display_title 优先级: display_name 最高 | 设置 display_name → display_title 返回它 | `test_display_title_prefers_display_name` |
| F-18 | display_title 不过滤 slash command | display_name="/resume" → display_title="/resume" | `test_display_title_keeps_slash_commands` |
| F-19 | display_title fallback 到 slug | 无 display_name → 返回 slug | `test_display_title_fallback_slug` |
| F-20 | display_title 终极 fallback | 全部为空 → 返回 session_id[:8] | `test_display_title_fallback_id` |

### 2.6 SQLite 持久化索引 (`core/index_db.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-21 | Upsert + Get | 写入后读取字段一致 | *已有: `test_upsert_and_get`* |
| F-22 | mtime 差异检测 | mtime 变化 → needs_refresh=True | *已有: `test_needs_refresh_detects_mtime_change`* |
| F-23 | 增量刷新 | 首次全量 > 0, 二次增量 ≤ 首次 | `test_incremental_refresh_reduces_work` |

### 2.7 MCP Server (`mcp/server.py`)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-24 | list_sessions 返回列表 | len > 0, 每项有 session_id 和 title | `test_mcp_list_sessions` |
| F-25 | search_sessions 匹配 | 查询已知关键词 → 返回匹配结果 | `test_mcp_search_sessions` |
| F-26 | enter_session 返回上下文 | 包含 command, status, cwd 字段 | `test_mcp_enter_session` |
| F-27 | resume_session 生成命令 | command = `claude --resume {sid}` | `test_mcp_resume_session` |

### 2.8 Plugin 结构

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| F-28 | plugin.json 有效 | name="ccsm", version 存在 | *已有: `test_plugin_json_exists`* |
| F-29 | .mcp.json 声明 ccsm server | type="stdio" | *已有: `test_mcp_json_exists`* |
| F-30 | hooks.json 有效 | SessionStart 或 SessionEnd 存在 | *已有: `test_hooks_json_exists`* |
| F-31 | mcp-shim.js 存在且引用正确模块 | 包含 "spawn" 和 "ccsm.mcp.server" | *已有: `test_mcp_shim_exists`* |

---

## 三、性能指标 (5 项)

基线标准: **工具级** — 单项操作可接受但能感知

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| P-01 | 项目发现耗时 | `discover_projects()` < 2s | `test_perf_discovery` |
| P-02 | MCP 首次构建 session_map | `_build_session_map(force_refresh=True)` < 90s (3000+ JSONL) | `test_perf_mcp_build` |
| P-03 | MCP TTL 缓存命中 | 二次调用 `_build_session_map()` < 0.01s | `test_perf_mcp_cache_hit` |
| P-04 | SQLite 增量刷新 (warm) | `incremental_refresh()` 第二次 < 5s | `test_perf_incremental_no_change` |
| P-05 | 元数据批量加载 | `load_all_meta()` < 1s | `test_perf_meta_load` |

---

## 四、集成流程 (6 项)

| ID | 测试项 | Pass 标准 | 验证方式 |
|----|--------|-----------|----------|
| I-01 | Plugin 目录结构完整 | `.claude-plugin/`, `.mcp.json`, `hooks/`, `scripts/` 全部存在 | 自动: `test_plugin_directory_structure` |
| I-02 | MCP shim 可启动 Python server | `node scripts/mcp-shim.js` 进程存活 >1s 且无错误退出 | 自动: `test_mcp_shim_starts` |
| I-03 | settings.local.json 注册正确 | ccsm server 在 mcpServers 中 | 自动: `test_settings_registration` |
| I-04 | 全量索引可建立 | `incremental_refresh()` 返回 > 0 且 `~/.ccsm/index.db` 存在 | 自动: `test_full_index_build` |
| I-05 | MCP 7 工具可导入 | server.py 中 7 个函数都可 import | 自动: `test_all_mcp_tools_importable` |
| I-06 | TUI 可启动不崩溃 | `python3 -m ccsm` 进程存活 >2s | 半自动 |

---

## 五、代码质量 (8 项)

| ID | 测试项 | Pass 标准 | 测试函数 |
|----|--------|-----------|----------|
| Q-01 | 所有 .py 文件语法正确 | `py_compile` 全部通过 | `test_all_py_compile` |
| Q-02 | session_id 验证防路径穿越 | `../etc/passwd` → ValueError | `test_session_id_validation` |
| Q-03 | meta.py 原子写入 | 写入中断不损坏文件 | `test_atomic_write_safety` |
| Q-04 | core/ 不依赖 TUI | core/*.py 无 textual/rich widget import | `test_core_no_tui_dependency` |
| Q-05 | 无硬编码 API key | 源码中不含 `sk-ant-`, `sk-proj-` 等真实 key | `test_no_hardcoded_secrets` |
| Q-06 | 现有 77 个测试全部通过 | 0 fail, 0 error | `test_existing_suite_passes` |
| Q-07 | 模块 import 无循环依赖 | 所有 core/ 模块可独立 import | `test_no_circular_imports` |
| Q-08 | summarizer 无直测试 (已知缺口) | 标记为 known gap, 不阻塞验收 | 文档记录 |

---

## 六、实际使用场景 (4 场景)

### 场景 S-01: TUI 全流程
```
步骤:
1. python3 -m ccsm 启动 TUI
2. 左侧 WorktreeTree 显示项目列表
3. 点击一个 worktree → 右侧 SessionList 加载
4. 上下键选择会话 → Enter 打开 Detail Drawer
5. Detail 展示: 标题、AI Digest、Milestones、Last Exchange
6. 按 / 打开搜索 → 输入关键词 → 列表过滤
7. 按 1-4 切换 Active/Back/Idea/Done 标签
8. 按 q 退出

Pass 标准: 以上 8 步均可完成, 无崩溃, 渲染无明显残影
```

### 场景 S-02: MCP 工具调用
```
步骤:
1. 新启动一个 Claude Code 会话
2. 确认 /mcp 显示 ccsm server
3. 让 Claude 调用 list_sessions → 返回会话列表
4. 让 Claude 调用 search_sessions(query="GUI") → 返回匹配结果
5. 让 Claude 调用 enter_session(session_id=...) → 返回上下文
6. 让 Claude 调用 resume_session(session_id=...) → 返回命令

Pass 标准: 步骤 2-6 均有正确 JSON 返回, 无 error
```

### 场景 S-03: Resume 流程
```
步骤:
1. TUI 中选择一个 ACTIVE 会话
2. 按 r → TUI 退出 → 自动启动 claude --resume {sid}
3. Claude Code 成功进入该会话, 上下文恢复

Pass 标准: Claude Code 启动并能看到之前对话历史
```

### 场景 S-04: 增量更新验证
```
步骤:
1. 运行 incremental_refresh() 记录返回值 N1
2. 在 Claude Code 中进行一轮新对话 (创建新 JSONL)
3. 再次运行 incremental_refresh() 记录返回值 N2
4. N2 应该 >= 1 (新会话被检测到)
5. 在 TUI 中刷新, 新会话应出现在列表中

Pass 标准: N2 >= 1, 新会话可见
```

---

## 七、已知缺口 (不阻塞验收)

| 缺口 | 影响 | 计划 |
|------|------|------|
| `core/summarizer.py` 无独立单元测试 | LLM 调用依赖外部 API, 难以离线测试 | 后续增加 mock 测试 |
| TUI widgets 无独立单元测试 | Textual widget 测试需要 App harness | 后续增加 Textual pilot 测试 |
| TUI 渲染帧率未量化 | 用户反馈的"卡顿"需 Textual profiler 分析 | 后续用 `textual devtools` 分析 |
| Plugin 未通过 marketplace 发布 | 目前只能通过 settings.local.json 注册 | 后续打包发布 |

---

## 八、验收判定

| 条件 | 要求 |
|------|------|
| **全部功能测试通过** | 27/27 + 已有 77 个 = 104 个测试全 Pass |
| **全部性能测试通过** | 5/5 在基线内 |
| **集成流程可走通** | 6/6 (I-06 手动确认) |
| **代码质量无 Critical** | 8/8 (Q-08 标记为已知缺口) |
| **实际使用 4 场景通过** | S-01 ~ S-04 (S-02, S-03 需手动确认) |

**验收结论**: 当自动化测试 100% Pass + 手动场景确认后, 系统通过验收。
