# CCSM — Claude Code Session Manager

> Design Spec | 2026-04-01 | Status: Draft

## 1. Problem

Claude Code 的会话管理存在以下痛点：

1. **找不到历史会话** — `/resume` 只显示近期扁平列表，253+ 归档会话无法搜索
2. **命名被覆盖** — `--name` 仅设置显示名，新输入会在 history.jsonl 生成新条目覆盖关联
3. **不按 worktree 组织** — 底层已按 worktree 分目录存储，但 UI 不按此分组展示
4. **无状态分类** — JSONL 中没有 todo/in-progress/done 等状态字段
5. **噪音污染** — 插件（claude-mem observer）和重复 SSH 连接产生大量无效会话

这是一个**空白市场** — 不存在专门管理 Claude Code session 的轻量工具。Vibe-Kanban 定位为全栈 AI 协作平台，过重且 UI 复杂。

## 2. Solution

构建一个轻量的 TUI 会话管理器，按 worktree 分组展示会话，自动推断状态，支持一键恢复、会话摘要和 MCP 集成。

### 产品定位

个人工具起步，架构预留开源扩展性。

### 核心价值

- 3 秒内找到任意 worktree 下的历史会话
- 自动过滤噪音会话，只展示有价值的内容
- 通过回顾（决策路径 + Claude 原文 + 任务进度）快速进入工作状态

## 3. Architecture

### 3.1 分层架构

```
┌─────────────┐  ┌─────────────┐  ┌──────────┐
│   TUI App   │  │ MCP Server  │  │   CLI    │
│  (Textual)  │  │  (stdio)    │  │ (click)  │
└──────┬──────┘  └──────┬──────┘  └────┬─────┘
       │                │              │
       └────────────────┼──────────────┘
                        │
                ┌───────┴───────┐
                │  Core Library │
                │  (ccsm.core)  │
                └───────┬───────┘
                        │
          ┌─────────────┼─────────────┐
          │             │             │
   ┌──────┴──────┐ ┌───┴────┐ ┌─────┴─────┐
   │ JSONL Parser│ │ Status │ │ Summarizer│
   │             │ │ Engine │ │           │
   └──────┬──────┘ └───┬────┘ └─────┬─────┘
          │             │             │
          └─────────────┼─────────────┘
                        │
              ┌─────────┴─────────┐
              │ ~/.claude/projects│ (read-only)
              │ .ccsm/meta/      │ (read-write, sidecar)
              └───────────────────┘
```

### 3.2 层职责

| 层 | 职责 | 依赖 |
|---|---|---|
| **ccsm.core.parser** | 解析 JSONL 文件，提取 session 元数据（时间戳、消息数、slug、cwd、gitBranch） | 无 |
| **ccsm.core.status** | 自动推断 Status + 默认 Priority | parser |
| **ccsm.core.summarizer** | 会话摘要生成（结构化提取 + 可选 LLM） | parser |
| **ccsm.core.meta** | sidecar 元数据读写（tags、priority 覆盖、pin、名称） | 无 |
| **ccsm.mcp** | MCP Server，暴露 list/search/resume/summarize 工具 | core |
| **ccsm.tui** | Textual TUI 应用 | core |
| **ccsm.cli** | Click CLI 入口 | core, tui |

### 3.3 数据安全边界

对 Claude Code 原始数据**完全只读**。所有用户自定义元数据存储在独立的 sidecar 目录：

```
~/.ccsm/
├── meta/
│   ├── {session_id}.meta.json    # 每个 session 的元数据
│   └── ...
├── summaries/
│   ├── {session_id}.summary.json # LLM 生成的摘要缓存
│   └── ...
└── config.toml                   # 用户配置
```

**sidecar meta.json 结构：**

```json
{
  "session_id": "b061c17d-1cce-4164-adfe-34347d6e945c",
  "name": "ccsm 设计 brainstorm",
  "status_override": null,
  "priority": "focus",
  "tags": ["design", "plugin"],
  "pinned_messages": ["uuid-of-key-response"],
  "created_at": "2026-04-01T17:32:00Z",
  "updated_at": "2026-04-01T18:17:00Z"
}
```

## 4. Data Model

### 4.1 Session 数据来源

| 来源 | 路径 | 用途 |
|---|---|---|
| JSONL 会话文件 | `~/.claude/projects/{encoded-path}/{uuid}.jsonl` | 消息内容、时间戳、工具调用 |
| 归档会话 | `~/.claude/projects/{encoded-path}/.archive/{uuid}.jsonl` | 历史会话 |
| 运行时进程 | `~/.claude/sessions/{pid}.json` | 判断会话是否正在运行 |
| 输入历史 | `~/.claude/history.jsonl` | 会话显示名、最后输入 |

### 4.2 JSONL 行类型

解析器需处理三种行：

| type 字段 | 内容 | 提取信息 |
|---|---|---|
| 含 `worktreeSession` | 会话元数据 | sessionId |
| 含 `snapshot` | 状态快照 | 忽略（内部状态） |
| `user` / `assistant` | 消息 | timestamp, message.content, cwd, gitBranch, slug |

### 4.3 Worktree 发现

项目目录编码规则：路径中 `/` 替换为 `-`。解析 `~/.claude/projects/` 目录名即可还原 worktree 树：

```
-home-v-tangxin-GUI                              → GUI (main)
-home-v-tangxin-GUI--claude-worktrees-panel      → GUI / panel
-home-v-tangxin-GUI--claude-worktrees-memory     → GUI / memory
-home-v-tangxin-GUI--claude-worktrees-streamIT   → GUI / streamIT
```

分组逻辑：
1. 识别 `--claude-worktrees-` 模式 → 提取父项目和 worktree 名称
2. 无该模式的路径视为独立项目（如 `VLM-Router`），作为顶层节点
3. 同一项目路径前缀下的 worktree 归入同一父节点

## 5. Classification System

### 5.1 三层正交分类

```
Layer 1: STATUS    — 是什么（自动推断，互斥）
Layer 2: PRIORITY  — 注意力在哪（默认映射 + 手动覆盖）
Layer 3: TAGS      — 关于什么（自由标签，多选）
```

### 5.2 Status（自动推断规则）

| Status | 规则 | 默认 Priority |
|---|---|---|
| **ACTIVE** | 最近 24h 有活动 且 有实质性用户消息（> 3 条） | FOCUS |
| **BACKGROUND** | 有 cron 任务 / 运行时间 > 2h 且 工具调用密集 / 包含 loop/experiment 关键词 / sessions/ 目录有对应进程 | WATCH |
| **IDEA** | 使用了 source-first/brainstorm 等探索性 skill / 会话 < 30min 且讨论性质 | PARK |
| **DONE** | 超过 48h 无活动 且 不满足 BACKGROUND 条件 | 无 |
| **NOISE** | 会话 < 3 条消息 且 无实质 user 输入 / 来自 claude-mem observer session 目录 / 重复 sessionId 的并发连接 | HIDE |

推断优先级：NOISE > BACKGROUND > ACTIVE > IDEA > DONE（先排除噪音，再识别特殊模式，最后按时间兜底）。

所有自动推断结果可被用户通过 `status_override` 手动覆盖。

### 5.3 Priority（注意力标签）

| Priority | 含义 | TUI 行为 |
|---|---|---|
| **FOCUS** | 需要主动投入精力 | 默认展示、高亮 |
| **WATCH** | 偶尔关注进度 | 默认展示、次要高亮 |
| **PARK** | 暂时搁置 | 默认折叠 |
| **HIDE** | 隐藏不显示 | 默认隐藏，按 `h` 切换可见 |

### 5.4 Tags（自定义标签）

自由文本标签，多选。内置建议（不强制）：

```
#research  #eval  #training  #debug  #design  #plugin  #refactor
```

## 6. TUI Design

### 6.1 技术选型

- **框架**: Textual (Python)
- **配色**: Style A — Claude Native（白橙暖色 + stone 灰系背景）
- **主色**: `#fb923c` (orange-400)，辅色: `#fbbf24` (amber-400)
- **背景**: `#1c1917` (stone-900)，面板: `#292524` (stone-800)
- **文本**: `#e7e5e4` (stone-200) 主文本，`#a8a29e` (stone-400) 次要，`#78716c` (stone-500) 弱

### 6.2 三面板布局

```
┌──────────┬──────────────────┬────────────────────┐
│ WORKTREES│ SESSIONS · panel │ SESSION DETAIL     │
│   18%    │      38%         │       44%          │
│          │                  │                    │
│ ▼ GUI    │ ● ACTIVE         │ ① Session 描述     │
│  ▸ panel │ ┌──────────────┐ │ ② Claude 最后回复  │
│  ▸ memory│ │ ccsm brainstm│ │ ③ 决策路径         │
│  ▸ strIT │ │ 18:17 · 45min│ │ ④ 关键洞察         │
│  ▸ claw  │ │ #design FOCUS│ │ ⑤ 回顾             │
│          │ │ 确认三层分...│ │                    │
│ ▶ VLM-Rt │ └──────────────┘ │ [r] Resume         │
│          │                  │ [s] Summarize      │
│          │ ◎ BACKGROUND     │ [t] Tag            │
│          │ ...              │ [p] Priority       │
└──────────┴──────────────────┴────────────────────┘
```

### 6.3 左面板：Worktree 树

- 顶层按项目分组（GUI、VLM-Router 等）
- 展开后显示 worktree 列表，附带 session 数量
- `●` 标记当前有 ACTIVE 会话的 worktree
- 底部可折叠 archived 分组

### 6.4 中面板：会话列表

每张卡片包含：
- **标题** + Priority 徽章（右上角）
- **时间** + 时长 + 消息轮次
- **Tags**（小标签）
- **LAST THOUGHT** — 最后一次对话思路的单行摘要（分隔线下方）
- BACKGROUND 类型额外显示进度条

按 Status 分组排列：ACTIVE → BACKGROUND → IDEA → DONE（可折叠）

### 6.5 右面板：Session Detail

五个区块，从上到下：

**① Session 描述**
- 高维语义概括：这个会话在做什么
- 元数据行：ID、分支、时长、轮次、起止时间
- 来源：LLM 摘要生成 / 用户手动编辑

**② Claude 最后回复**
- 直接展示 Claude 的原文回复（默认折叠 ~6 行，底部渐隐）
- 三种浏览模式：last（默认）/ ← → 翻看历史 / 📌 pinned（用户标记的关键回复）
- 快捷键：`[e]` 全屏展开、`[p]` pin 标记、`[← →]` 浏览
- 来源：JSONL assistant message 直接提取（零 LLM 成本）
- pin 信息持久化到 sidecar meta.json

**③ 决策路径**
- 对话输出内容的凝练，关键决策步骤序列
- ✓ 已确认的决策 / → 当前进行中的决策
- 来源：LLM 从 JSONL 提取决策点

**④ 关键洞察**
- 讨论中发现的要点、insight、重要结论
- 不是操作记录，而是认知收获
- 来源：LLM 提取 / 识别 ★ Insight 标记

**⑤ 回顾**
三个子区块：
- **完成了什么** — 任务清单 + 完成状态（☑/☐）
- **代码改动** — git diff 摘要（文件名 + 改动性质），通过时间戳关联 git log
- **上次停在** — 最后几轮对话的浓缩，离开时在做什么

设计理念：面板目的是帮用户**回忆**，不是帮用户规划。看完五个区块后自然知道下一步。

### 6.6 快捷键

| 键 | 作用 | 上下文 |
|---|---|---|
| `↑↓` | 导航 | 所有面板 |
| `Tab` | 切换面板焦点 | 全局 |
| `Enter` | 展开/选中 | 树/列表 |
| `/` | 搜索（模糊匹配） | 全局 |
| `f` | 按 Priority 过滤 | 列表 |
| `h` | 切换 HIDE/NOISE 可见 | 列表 |
| `r` | Resume 选中会话 | 详情 |
| `s` | 生成/刷新摘要 | 详情 |
| `t` | 编辑 Tags | 详情 |
| `p` | 设置 Priority | 详情 |
| `n` | 重命名 | 详情 |
| `e` | 展开 Claude 回复全文 | 详情 |
| `q` | 退出 | 全局 |

## 7. Summarization

### 7.1 双模式摘要

**模式 A：结构化提取（无需 API）**
- 从 JSONL 提取最后 N 条 user/assistant 消息的关键信息
- 统计 skill 使用（source-first/brainstorm/experiment-runner 等）
- 提取 git 关联（时间窗口内的 commit message）
- 生成 Session 描述 + 上次停在

**模式 B：LLM 摘要（需 API Key）**
- 调用 haiku/flash 级别模型
- 生成决策路径、关键洞察、任务清单
- 摘要缓存到 `~/.ccsm/summaries/{session_id}.summary.json`
- 仅在用户按 `[s]` 时触发，或后台定时生成

### 7.2 摘要 Prompt 设计要点

摘要 prompt 应提取：
1. **这个会话在做什么** — 一句话概括
2. **做了哪些关键决策** — 按时间序列排列，每个决策附带选择理由
3. **发现了什么** — 讨论中的 insight、意外发现、重要结论
4. **完成了哪些任务** — 可量化的进度
5. **代码改了什么** — 文件级 diff 摘要
6. **最后在做什么** — 对话最后几轮的浓缩

不应包含：工具调用统计、token 消耗、技术性元数据。

## 8. MCP Integration

### 8.1 MCP Server Tools

| Tool | 参数 | 返回 |
|---|---|---|
| `list_sessions` | worktree?, status?, priority?, tag? | Session 列表（含元数据） |
| `get_session_detail` | session_id | 完整 Session Detail（描述+决策+洞察+回顾） |
| `search_sessions` | query (模糊搜索) | 匹配的 Session 列表 |
| `resume_session` | session_id | 启动 `claude --resume {id}` 的命令 |
| `summarize_session` | session_id, force? | 生成/返回摘要 |
| `update_session_meta` | session_id, name?, priority?, tags?, pin? | 更新 sidecar 元数据 |

### 8.2 斜杠命令

注册到 `~/.claude/commands/`：

| 命令 | 作用 |
|---|---|
| `/sessions` | 列出当前 worktree 的会话（调用 list_sessions） |
| `/session-detail` | 显示指定会话的详情（调用 get_session_detail） |
| `/resume-to` | 交互式选择并恢复会话 |

## 9. Package Structure

```
ccsm/
├── __init__.py
├── __main__.py           # python -m ccsm 入口
├── core/
│   ├── __init__.py
│   ├── parser.py         # JSONL 解析器
│   ├── discovery.py      # Worktree 发现 + 项目目录扫描
│   ├── status.py         # Status/Priority 自动推断引擎
│   ├── summarizer.py     # 摘要生成（结构化提取 + LLM）
│   └── meta.py           # Sidecar 元数据读写
├── models/
│   ├── __init__.py
│   └── session.py        # Session / Worktree / Meta 数据模型
├── mcp/
│   ├── __init__.py
│   └── server.py         # MCP Server
├── tui/
│   ├── __init__.py
│   ├── app.py            # Textual App 主入口
│   ├── screens/
│   │   └── main.py       # 主屏幕（三面板）
│   ├── widgets/
│   │   ├── worktree_tree.py   # 左面板：Worktree 树
│   │   ├── session_list.py    # 中面板：会话列表
│   │   ├── session_card.py    # 会话卡片组件
│   │   └── session_detail.py  # 右面板：Session Detail
│   └── styles/
│       └── claude_native.tcss # Textual CSS 主题
├── cli/
│   ├── __init__.py
│   └── main.py           # Click CLI
└── commands/
    ├── sessions.md        # /sessions 斜杠命令
    ├── session-detail.md  # /session-detail 斜杠命令
    └── resume-to.md       # /resume-to 斜杠命令
```

## 10. Configuration

`~/.ccsm/config.toml`:

```toml
[general]
claude_dir = "~/.claude"          # Claude Code 数据目录
scan_archived = false             # 是否扫描归档会话

[status]
active_threshold_hours = 24       # ACTIVE 判定时间窗口
done_threshold_hours = 48         # DONE 判定时间窗口
background_min_duration_hours = 2 # BACKGROUND 最低运行时长
noise_min_messages = 3            # NOISE 判定最低消息数

[summarizer]
mode = "extract"                  # "extract" (无 API) | "llm" | "both"
api_key = ""                      # LLM API Key（可选）
model = "claude-haiku-4-5-20251001"  # 摘要模型
auto_summarize = false            # 是否后台自动生成摘要

[tui]
theme = "claude-native"           # 主题
show_noise = false                # 默认是否显示 NOISE
show_archived = false             # 默认是否显示归档
```

## 11. Dependencies

```
textual >= 1.0.0      # TUI 框架
rich >= 13.0.0         # 终端富文本（Textual 依赖）
click >= 8.0.0         # CLI 框架
tomli >= 2.0.0         # TOML 配置解析（Python < 3.11）
mcp >= 1.0.0           # MCP SDK
anthropic >= 0.40.0    # Anthropic API（可选，LLM 摘要）
```

## 12. Success Criteria

- [ ] 3 秒内找到任意 worktree 下的历史会话
- [ ] Status 自动推断准确率 >= 85%
- [ ] 一键恢复会话到 Claude Code（调用 `claude --resume`）
- [ ] 命名/标签持久化，不被 Claude 覆盖（sidecar 隔离）
- [ ] Session Detail 五区块完整展示（描述+Claude回复+决策+洞察+回顾）
- [ ] 支持 MCP Plugin 集成到 Claude Code
- [ ] NOISE 自动检测准确率 >= 90%
