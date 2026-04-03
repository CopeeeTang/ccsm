# Detail 面板重设计方案

> 基于 Claude Code 源码逆向分析 + JSONL 实际数据挖掘的一手研究结果

## 研究基础

### 数据来源
1. **Claude Code 源码** — `/usr/local/lib/node_modules/@anthropic-ai/claude-code/cli.js` (v2.1.20 npm bundle, 可逆向)
2. **实际 JSONL 数据** — `~/.claude/projects/` 下的多个真实会话文件
3. **history.jsonl** — `~/.claude/history.jsonl` (3196 条 prompt 历史)
4. **CCSM 现有实现** — parser.py, summarizer.py, session_detail.py 等

### JSONL 完整记录类型（源码 appendEntry 函数确认）

| type | 说明 | CCSM 是否利用 |
|------|------|--------------|
| `user` | 用户消息（含 content blocks） | ✅ 提取 text |
| `assistant` | 助手消息（含 tool_use, thinking） | ⚠️ 只提取 text，**跳过 tool_use** |
| `system` | 系统消息（compact_boundary, turn_duration 等） | ⚠️ 只检测 compact_boundary |
| `summary` | Claude Code 自己生成的摘要 | ❌ 完全忽略 |
| `custom-title` | 用户自定义标题 | ✅ |
| `ai-title` | CC 生成的 AI 标题 | ✅ |
| `last-prompt` | 用户最后一条 prompt 原文 | ❌ 完全忽略 |
| `tag` | 会话标签 | ❌ 完全忽略 |
| `file-history-snapshot` | 文件变更快照 | ❌ 跳过 |
| `progress` | Hook/工具执行进度 | ❌ 跳过 |
| `queue-operation` | 消息队列操作（含 prompt） | ❌ 跳过 |
| `attachment` | 附件（MCP 指令等） | ❌ 跳过 |

### 被忽略的高价值数据

#### 1. `isCompactSummary` 内容 (🔴 极高价值)
**来源**: compact 操作后 Claude 自己生成的上下文总结
**当前处理**: `if data.get("isCompactSummary"): continue` — 直接跳过！
**实际内容示例**:
```
This session is being continued from a previous conversation...

Summary:
1. Primary Request and Intent:
   用户要求评估当前系统存储状况，判断全量跑 OVO-Bench 和 RTV-Bench 是否需要...

2. Key Technical Concepts:
   - Azure Blob Storage 迁移（azcopy，SAS token）
   - OVO-Bench 数据架构：src_videos → chunk_ovo_videos.py → chunked_videos
   ...
```
**价值**: 这是 Claude 自己对整个对话历史的理解总结，比我们用 LLM 二次生成的更准确、更全面，**且完全零成本**。

#### 2. `tool_use` blocks (🔴 极高价值)
**来源**: assistant 消息的 `message.content[]` 中 `type: "tool_use"` 的块
**当前处理**: `_extract_text()` 只提取 `type: "text"` 块，tool_use 完全跳过
**实际内容示例**:
```json
{"type": "tool_use", "name": "Edit", "input": {"file_path": "/path/parser.py", "old_string": "...", "new_string": "..."}}
{"type": "tool_use", "name": "Bash", "input": {"command": "pytest tests/ -v"}}
{"type": "tool_use", "name": "Read", "input": {"file_path": "/path/models/session.py"}}
```
**价值**: 能自动生成"编辑了哪些文件"、"执行了哪些命令"、"读了哪些文件"的操作日志。

#### 3. `last-prompt` type (🔴 高价值)
**来源**: Claude Code 在会话结束/切换时写入的最后一条 prompt
**当前处理**: 作为未知 type 被跳过
**实际内容**:
```json
{"type": "last-prompt", "lastPrompt": "读取该文章，如果我的clawbot...", "sessionId": "..."}
```
**价值**: 用户最后输入的 prompt 就是 resume 时最需要知道的"我当时在做什么"。

#### 4. `summary` type (🟡 中高价值)
**来源**: Claude Code 自己生成的会话摘要，通过 `leafUuid` 关联到消息树叶子节点
**当前处理**: 完全忽略
**实际内容**: `"Implementing Video-Enhanced A2UI Agent for Smart Glasses UI"`
**价值**: 现有 45 条摘要数据，比 CCSM 的 AI title 更语义化。

#### 5. `message.model` (🟡 中价值)
**来源**: assistant 消息的 `message.model` 字段
**当前处理**: 不提取
**价值**: 用户能快速判断 session 使用了什么模型（opus vs sonnet vs haiku），影响对质量的预期。

#### 6. `message.usage` (🟡 中价值)
**来源**: assistant 消息的 token 使用统计
**当前处理**: 不提取
**价值**: 成本追踪 + context 使用率。

---

## 当前 Detail 面板的问题

| 区域 | 当前设计 | 核心问题 |
|------|---------|---------|
| 📋 SESSION | title/branch/duration/intent | 缺 model、缺 token 成本 |
| 🤖 AI SUMMARY | 需要 LLM API 调用才有内容 | JSONL 中已有零成本的 compact summary，却被跳过 |
| 🧭 MILESTONES | 只展示 LLM 模式生成的 | rule-based 结果被完全排除 |
| 📍 BREAKPOINT | 依赖 LLM 生成 | last-prompt 就是最好的 breakpoint，零成本 |
| 💬 LAST REPLY | 只有 Claude 的 600 chars | 缺少对应的 user message，缺上下文 |

**核心哲学问题**: 当前设计是"AI 生成优先" — 需要调用 API 才能展示有价值的内容。
但 JSONL 中已经有大量被跳过的零成本高价值数据。

---

## 新设计方案

### 设计原则

1. **数据挖掘优先，AI 增强其次** — 先把 JSONL 中已有的数据用好，再考虑 LLM 增强
2. **面向恢复，而非面向回顾** — 用户的核心需求是"回到工作状态"，不是"了解历史"
3. **三层渐进加载** — L1 纯解析(即时) → L2 rule-based(毫秒) → L3 LLM(按需)

### 新的 6 区域布局

```
┌─────────────────────────────────────────────┐
│ 📋 SESSION IDENTITY                   [L1]  │  ← 目标1: 快速定位
│   Title + Status + Branch + Model            │
│   Intent: "优化搜索性能并修复排序问题"         │
│   Duration: 2h 15m (43 msg) | opus-4.6       │
│   Tokens: 12.3k in / 8.5k out               │
├─────────────────────────────────────────────┤
│ 📍 WHERE YOU LEFT OFF                 [L1]  │  ← 目标2: 恢复核心
│   Last prompt: "修复 parser.py 的性能..."     │  ← from last-prompt / history
│   Last topic:  正在优化正则表达式性能          │  ← from breakpoint
│   → 下一步: 运行 benchmark 验证优化效果        │
├─────────────────────────────────────────────┤
│ 🔧 WHAT WAS DONE                     [L1]  │  ← 目标1+2: 操作回溯
│   📝 Edited: parser.py, status.py (+3)      │  ← from tool_use(Edit/Write)
│   ⚡ Ran: pytest tests/ -v, git diff         │  ← from tool_use(Bash)
│   📖 Read: session.py, summarizer.py         │  ← from tool_use(Read)
├─────────────────────────────────────────────┤
│ 📝 CONTEXT SUMMARY                   [L1]  │  ← 目标2: 零成本摘要
│   From compact: "用户要求优化CCSM的..."      │  ← from isCompactSummary
│   OR From CC summary: "Implementing..."      │  ← from type=summary
│   OR From AI summary: (press 's' to gen)     │  ← from LLM (fallback)
├─────────────────────────────────────────────┤
│ 🧭 MILESTONES                        [L2]  │  ← 目标1: 进度概览
│   ✓ 架构讨论    确认微服务拆分方案              │
│   ▶ 实现阶段    parser 优化 [← HERE]          │
│   ○ 测试验证    待运行 benchmark               │
├─────────────────────────────────────────────┤
│ 💬 LAST EXCHANGE                     [L1]  │  ← 目标2: 对话上下文
│   [YOU] 修复 parser.py 的性能问题...          │  ← last user msg
│   [AI]  好的，我来分析当前的性能瓶颈...        │  ← last assistant reply
└─────────────────────────────────────────────┘
```

### 每个区域的数据来源和实现

#### 区域 1: 📋 SESSION IDENTITY (改进)

| 字段 | 来源 | 改动 |
|------|------|------|
| Title | 现有 `display_title` | 不变 |
| Status | 现有 `status` | 不变 |
| Branch | 现有 `git_branch` | 不变 |
| **Model** | **新增** `message.model` 提取 | 解析最后一条 assistant 的 model |
| Intent | 现有 `ai_intent` / `first_user_content` | 不变 |
| Duration | 现有 | 不变 |
| **Tokens** | **新增** `message.usage` 聚合 | 累加所有 assistant 的 input/output tokens |

#### 区域 2: 📍 WHERE YOU LEFT OFF (新增/重构)

这是**最高价值区域**，用户看到它就知道"我当时在做什么"。

数据来源（优先级从高到低）：
1. **`last-prompt` type** — JSONL 中的 `lastPrompt` 字段，用户最后一条 prompt 原文
2. **`history.jsonl`** — 全局 prompt 历史的 `display` 字段（按 sessionId 匹配）
3. **最后一条非 meta 的 user message** — 从 JSONL 中提取
4. **现有 breakpoint** — 保留 LLM 生成的 breakpoint 作为补充

**展示逻辑**:
```python
# 优先级: last-prompt > history prompt > last user msg > breakpoint
last_prompt = session_info.last_prompt or history_prompts.get(session_id)
if last_prompt:
    show("Your last request:", truncate(last_prompt, 200))

if breakpoint:
    show("Topic:", breakpoint.detail)
    show("Next step:", breakpoint.last_topic)
elif last_prompt:
    show("(press 's' for AI-generated context)")
```

#### 区域 3: 🔧 WHAT WAS DONE (新增)

从 `tool_use` blocks 中提取操作摘要，按类别分组：

```python
# 从 assistant messages 的 content blocks 中提取
operations = {
    "edited": set(),    # Edit, Write, MultiEdit → file_path
    "commands": [],     # Bash → command (last 5)
    "read": set(),      # Read → file_path
    "searched": set(),  # Grep, Glob → pattern/path
    "agents": [],       # Agent → description
    "web": [],          # WebFetch, WebSearch → url/query
}
```

**展示格式**:
```
📝 Edited: parser.py, status.py, session_detail.py
⚡ Ran: pytest tests/ -v | git status | npm install
📖 Read: session.py, summarizer.py (+4 more)
🔍 Searched: "compact_boundary", "*.jsonl"
```

#### 区域 4: 📝 CONTEXT SUMMARY (重构)

**三级数据源（零成本 → 低成本 → 高成本）**:

1. **`isCompactSummary` 内容** (零成本) — JSONL 中已有的 Claude compact 摘要
   - 包含: Primary Request, Key Technical Concepts, Progress, Open Questions
   - 通常 2000-5000 chars，极其详细
   - **截取前 500 chars + 展开按钮**

2. **`type: "summary"` 条目** (零成本) — Claude Code 自己生成的简短摘要
   - 通常一句话描述
   - 通过 `leafUuid` 可定位到具体消息

3. **LLM 生成摘要** (API 成本) — 现有的 summarizer.py，作为 fallback
   - 用户按 `s` 触发
   - 但重新设计 prompt 为面向恢复而非面向回顾

#### 区域 5: 🧭 MILESTONES (小改)

- **移除 `mode == "llm"` 限制** — rule-based 的 milestones 也应该展示
- 现有实现已足够，不需大改

#### 区域 6: 💬 LAST EXCHANGE (改进)

- 现有只展示 Claude 的最后一条回复
- **新增**: 展示最后一组 user+assistant 对话对
- 用户消息用 `[YOU]` 前缀，助手用 `[AI]` 前缀
- 各截取 300 chars

---

## 实施计划

### Phase 1: Parser 增强 (P0, 预计 2-3h)

#### 1.1 新增 `last-prompt` 解析
```python
# parser.py 的 parse_session_info() 中新增:
if msg_type == "last-prompt":
    last_prompt = data.get("lastPrompt")
    continue
```

#### 1.2 新增 `isCompactSummary` 内容提取
```python
# 当前: if data.get("isCompactSummary"): continue
# 改为: 提取内容但不计入 message_count
if data.get("isCompactSummary"):
    msg_content = _extract_text(data.get("message", {}).get("content", ""))
    if msg_content and len(msg_content) > 50:
        compact_summaries.append(msg_content)
    continue
```

#### 1.3 新增 `summary` type 提取
```python
if msg_type == "summary":
    cc_summaries.append(data.get("summary", ""))
    continue
```

#### 1.4 新增 `model` 和 `usage` 提取
```python
if msg_type == "assistant":
    msg_obj = data.get("message", {})
    model = msg_obj.get("model")
    usage = msg_obj.get("usage", {})
```

#### 1.5 新增 `tool_use` 操作提取
```python
# 新函数: 从 content blocks 中提取工具调用摘要
def _extract_tool_operations(content) -> list[dict]:
    if not isinstance(content, list):
        return []
    ops = []
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            ops.append({
                "tool": block.get("name"),
                "input_summary": _summarize_tool_input(block.get("name"), block.get("input", {}))
            })
    return ops
```

### Phase 2: Model 扩展 (P0, 预计 1h)

#### 2.1 SessionInfo 新增字段
```python
@dataclass
class SessionInfo:
    # ... 现有字段 ...
    
    # 新增字段
    last_prompt: Optional[str] = None           # 来自 last-prompt type
    compact_summaries: list[str] = field(default_factory=list)  # 来自 isCompactSummary
    cc_summaries: list[str] = field(default_factory=list)       # 来自 type=summary
    model_name: Optional[str] = None            # 来自 message.model (最后一条)
    total_input_tokens: int = 0                 # 累计 input tokens
    total_output_tokens: int = 0                # 累计 output tokens
    files_edited: list[str] = field(default_factory=list)  # 从 tool_use 提取
    commands_run: list[str] = field(default_factory=list)   # 从 tool_use 提取
    files_read: list[str] = field(default_factory=list)     # 从 tool_use 提取
    last_user_message: Optional[str] = None     # 最后一条非 meta user message
```

### Phase 3: Detail 面板重构 (P1, 预计 2-3h)

按照新的 6 区域布局重构 `session_detail.py`，替换现有的 5 区域布局。

### Phase 4: 性能优化 (P2, 可选)

- `tool_use` 提取可能增加解析时间 — 考虑只在 Detail 模式下做完整解析
- `parse_session_info()` 保持轻量（列表用），`parse_session_detail()` 新函数做深度解析（详情用）

---

## 性能影响评估

| 改动 | 解析时间影响 | 内存影响 |
|------|------------|---------|
| last-prompt 提取 | 微小 (+1 字段判断) | 微小 (+200 chars) |
| isCompactSummary 提取 | 微小 (改 skip → extract) | 中等 (+5KB/session) |
| summary 提取 | 微小 (+1 type 判断) | 微小 (+100 chars) |
| model/usage 提取 | 小 (每条 assistant 多读 2 字段) | 微小 (+几个数字) |
| tool_use 提取 | **中等** (需要遍历 content blocks) | 中等 (+file paths) |

**建议**: tool_use 提取在 `parse_session_info()` 中只做轻量版（统计 tool 类型计数），
完整的操作日志在 `parse_session_detail()` 新函数中实现（只在打开 Detail 时调用）。
