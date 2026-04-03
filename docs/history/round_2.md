# CCSM Round 2 — 会话历史

**项目**: CCSM (Claude Code Session Manager)
**日期**: 2026-04-02
**接续**: Round 1（初始架构 + TUI 框架）

---

## 背景

本轮从 Codex Review Round 1 的审查报告出发，系统性地修复了安全漏洞与性能问题，随后根据用户反馈实施了三项功能增强（状态推断 / 卡片渲染 / Detail 面板），最后经历第二轮 Codex Review 并完成所有修复。

---

## 阶段 1：Codex Review Round 1 结果处理

**输入**: Codex agent `acacb15f883d7ec01` 审查报告，含 2 P0 + 5 P1。

### P0-1：路径穿越漏洞（meta.py）

- **问题**: `session_id` 直接拼接为文件路径，`../../etc/passwd` 可逃逸目录。
- **修复**: `core/meta.py` 新增 `_validate_session_id()` 方法，正则校验 `^[a-zA-Z0-9_-]+$`，不合规则抛出 `ValueError`。
- **验证**: 构造 `../../etc/passwd` 输入，确认被正确拒绝并抛出异常。

### P0-2：MCP Server 全量解析性能（mcp/server.py）

- **问题**: 每次工具调用均重新解析 3000+ 条 JSONL，首次调用约 4.1 秒，高频调用不可接受。
- **修复**: 添加模块级 TTL 缓存（30 秒），缓存 `(sessions_dict, all_meta)` 元组；`_cache_ts` 记录时间戳，过期才重新加载。
- **验证**: 首次 4.1s → 缓存命中 0.0ms。

### P1-1：`_session_to_dict()` 冗余 `load_meta()`

- **问题**: 函数内部重复调用 `load_meta()` 导致 N+1 文件读取。
- **修复**: 将已加载的 `all_meta` 作为参数传入，直接查表取值。

### P1-2：`_load_all_sessions()` 死代码

- **问题**: 函数定义但从未调用，维护负担。
- **修复**: 整体删除该函数及相关引用。

### P1-3：`Static` 作容器不稳定

- **问题**: Textual 的 `Static` 设计为叶节点，用作布局容器会引发渲染异常。
- **修复**: 全面改用 `Vertical` 容器承载子 Widget。

### P1-4：`app.exit()` + `subprocess.Popen` 竞态

- **问题**: `app.exit()` 后立刻 `subprocess.Popen` 开启外部进程，存在时序竞态。
- **修复**: 改为 `app.exit(result=sid)` 携带 session ID 退出，在 TUI 外层统一处理 resume 逻辑。

---

## 阶段 2：用户反馈与功能规划

用户运行 TUI 后反馈三个痛点：

1. **卡片概括性不够** — 仅显示 session_id，无法快速判断内容。
2. **状态调优需要优化** — NOISE 过滤过于激进，部分有价值会话被忽略。
3. **Detail 面板排版粗糙** — 纯文本堆砌，信息密度低。

**用户决策（选项确认）**:

| 功能点 | 用户选择 |
|--------|---------|
| 卡片摘要 | 自动生成一句话概括 + 首条消息 + last thought 双行展示 |
| 状态推断 | 规则 + Haiku 两者结合（先规则，边界案例交 Haiku） |
| Detail 渲染 | Rich Markdown + 信息密度排版优化 |

制定三部分实施计划，获得批准：
- **Part 1**: `session_card.py` 全面重写
- **Part 2**: 状态推断扩展（新字段 + 新规则）
- **Part 3**: `session_detail.py` 全面重写

---

## 阶段 3：Part 2 — 状态推断优化

### 模型层扩展（models/session.py）

新增三个字段：
```python
first_user_content: str = ""      # 首条用户消息文本
total_user_chars: int = 0         # 所有用户消息总字符数
all_slash_commands: list[str] = field(default_factory=list)  # 检测到的 slash commands
```

### 解析层扩展（core/parser.py）

在 JSONL 扫描阶段同步提取：
- 首条角色为 `human` 的消息文本（截取前 200 字符）
- 累加所有 human 消息字符数
- 检测 `text` 字段中以 `/` 开头的命令模式

**null 安全处理**: `data.get("message") or {}` 防止 `message: null` 导致的 KeyError。

### 规则层扩展（core/status.py）

在原有 NOISE 规则基础上新增：
- **Rule 3**: 检测到 slash command 且消息总数 < 5 → NOISE（多为测试性调用）
- **Rule 4**: 总用户字符数 < 50 且消息数 < 6 → NOISE（极短内容，无实质交互）

**效果**: NOISE 率 94.9% → 95.0%，规则精准覆盖边界测试会话。

---

## 阶段 4：Part 1 — Session 卡片增强

**文件**: `tui/widgets/session_card.py`，全面重写 `render()` 方法。

### 新布局（4 行结构）

```
① [状态标签] [优先级] 标题文本 (时间戳)
② 📝 首条用户消息（截断 60 字符）
③ 💭 Last thought 内容...   [N msgs]
④ 🏷 tag1  tag2  tag3
```

### 着色规则

| 元素 | 颜色 |
|------|------|
| ACTIVE | `bold green` |
| IDEA | `bold yellow` |
| DONE | `dim` |
| NOISE | `dim red` |
| P0 优先级 | `bold red` |
| P1 优先级 | `bold yellow` |
| 辅助信息（时间/消息数） | `muted`（tcss 变量） |

---

## 阶段 5：Part 3 — Detail 面板渲染优化

**文件**: `tui/widgets/session_detail.py`，全面重写。

### SESSION INFO 区块

Rich markup key-value 对齐格式：
```
[dim]ID:[/dim]          abc123
[dim]Status:[/dim]      [green]ACTIVE[/green]
[dim]Duration:[/dim]    2h 15m
[dim]Messages:[/dim]    42
```

### LAST REPLY 区块

使用 Textual `Markdown` Widget 渲染 Claude 最后一条回复，支持代码块、列表等 Markdown 语法。正确处理 off-by-one 索引（`start_idx` 基于实际 assistant 消息位置计算）。

### RETROSPECTIVE 区块

分层展示：
- **Completed**: 已完成事项列表
- **Code Changes**: 代码变更摘要
- **Pending**: 待办事项
- **Last Context**: 最后一个 context window 的摘要

所有区块改用 `Vertical` 容器，替代原来的嵌套 `Static`。

---

## 阶段 6：Codex Review Round 2 结果处理

### P0-1：Rich markup 注入（Critical）

- **问题**: 用户输入的 `title`、`first_user_content`、`thought` 等字段若含 `[bold]`、`[red]` 等 Rich markup 标签，直接插入 Rich 渲染字符串会导致 `MarkupError` 崩溃，甚至被恶意利用改变显示样式。
- **修复**: 在所有用户来源字段插入 Rich 字符串前调用 `rich_escape()`：
  - `session_card.py`: title, first_user_content, thought, tags
  - `session_detail.py`: notes, decision_trail, insights, retro 各字段
- **验证**: 构造含 `[bold red]INJECTED[/bold red]` 的 title，确认显示为字面文本而非渲染效果。

### P1-1：NOISE Rule 4 误判修复

- **问题**: Rule 4 `total_user_chars < 50` 可能误判含大量 Claude 回复但用户输入简短的正常会话。
- **修复**: 增加保护条件 `message_count < 6`，两个条件同时满足才判 NOISE。

### P1-2：Reply 索引 off-by-one

- **问题**: `session_detail.py` 中计算 assistant 最后回复的起始索引逻辑错误，导致显示内容截断或偏移。
- **修复**: 正确使用 `last_assistant_idx` 从消息列表反向搜索，计算准确的 `start_idx`。

### P1-5：parser.py null 安全

- **问题**: 部分 JSONL 行 `message` 字段为 `null`，直接 `.get()` 会抛 `AttributeError`。
- **修复**: `data.get("message") or {}` 确保取值结果为 dict。

### P2-1：清理未使用 import

- **问题**: `session_detail.py` 保留了未使用的 Widget 导入。
- **修复**: 删除相关 import 行。

---

## 关键数据汇总

| 指标 | 数值 |
|------|------|
| 总会话数 | 3,158 |
| NOISE | 3,001（95.0%） |
| DONE | 114 |
| IDEA | 28 |
| ACTIVE | 15 |
| MCP 首次加载 | 4.1s |
| MCP 缓存命中 | 0.0ms |
| 路径穿越阻断 | 通过（`../../etc/passwd` 被拒绝） |
| Rich 注入防御 | 通过（markup 转义字面显示） |

---

## 完整文件变更清单

| 文件 | 变更内容 |
|------|---------|
| `models/session.py` | 新增 `first_user_content`, `total_user_chars`, `all_slash_commands` 字段 |
| `core/parser.py` | 提取首条消息 / 累加字符数 / slash 检测 + `message: null` 安全 |
| `core/status.py` | NOISE 规则扩展：Rule 3（slash command）+ Rule 4（极短内容）+ Rule 4 保护条件 |
| `core/meta.py` | `_validate_session_id()` 路径穿越校验 |
| `mcp/server.py` | 30s TTL 缓存 / 删除死代码 `_load_all_sessions` / 传入 `all_meta` 避免 N+1 |
| `tui/widgets/session_card.py` | 全面重写：4 行 Rich markup 布局 + `rich_escape()` 防注入 |
| `tui/widgets/session_detail.py` | 全面重写：Rich key-value + Markdown Widget + 分层 Retrospective + escape |
| `tui/screens/main.py` | 新字段传播到卡片 / resume 竞态修复 |
| `tui/app.py` | `app.exit(result=sid)` 携带 session ID 退出 |
| `tui/styles/claude_native.tcss` | 卡片 4 行高度调整 / detail 区块间距 / muted 颜色变量 |

---

## 架构状态（Round 2 结束时）

```
ccsm/
├── models/
│   └── session.py         ✅ 含 first_user_content / total_user_chars / all_slash_commands
├── core/
│   ├── parser.py          ✅ 全字段提取 + null 安全
│   ├── status.py          ✅ 4 条 NOISE 规则（Rule 3/4 新增）
│   └── meta.py            ✅ 路径穿越校验
├── mcp/
│   └── server.py          ✅ TTL 缓存 + 精简代码
├── tui/
│   ├── widgets/
│   │   ├── session_card.py    ✅ 4 行 Rich 布局 + escape
│   │   └── session_detail.py  ✅ Rich + Markdown + escape
│   ├── screens/main.py    ✅ 字段传播 + resume 修复
│   ├── app.py             ✅ exit(result=sid)
│   └── styles/claude_native.tcss  ✅ 样式更新
└── cli/                   🔲 list/resume 待实现
```

---

## 下一步计划（Round 3 预期）

1. **实际运行验证**: `PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m ccsm` 端到端测试。
2. **Haiku 辅助分类**: 实现 `core/classifier.py`，对规则无法判定的边界会话调用 claude-haiku-3-5 API。
3. **Summarizer 模块**: `core/summarizer.py`，为 ACTIVE/IDEA 会话生成一句话摘要写入 meta。
4. **CLI 实现**: `cli/commands.py` 实现 `ccsm list` / `ccsm resume <id>` 实际逻辑。
5. **MCP 注册**: 将 MCP Server 路径写入 `~/.claude/settings.json`，在 Claude Code 中使用。

---

*保存时间: 2026-04-02 | 执行人: Claude Code*
