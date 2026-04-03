# CCSM Rule-Based Pipeline 审计修复计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 基于 Claude Code 源码审计结果，修复 CCSM pipeline 中 5 个 rule-based 模块的不匹配、遗漏和过时假设。

**Architecture:** 按风险优先级分为 P0（数据正确性）、P1（功能增强）、P2（覆盖完善）三批。每个 Task 改一个文件，无交叉依赖，可并行。

**Tech Stack:** Python 3.12, Textual, Claude Code JSONL format

**审计来源:** Claude Code 源码 `/home/v-tangxin/github/claude-code/src/`，关键文件：`types/logs.ts`（Entry 类型定义）、`utils/sessionStorage.ts`（JSONL 写入）、`commands/branch/branch.ts`（fork 实现）、`services/compact/compact.ts`（压缩实现）、`utils/concurrentSessions.ts`（PID 机制）

---

## 文件结构

| 文件 | 修改类型 | 职责 |
|------|---------|------|
| `ccsm/core/discovery.py` | Modify L147-173 | 修复 `load_running_sessions` PID 验证 |
| `ccsm/core/parser.py` | Modify L60-90, L150-270 | 新增 JSONL type 解析、提取 `custom-title`/`ai-title`/`forkedFrom` |
| `ccsm/core/lineage.py` | Modify L70-130 | 修复 fork 检测大小写 + 使用 `forkedFrom` + compact 前缀更新 |
| `ccsm/core/milestones.py` | Modify L85-90, 新增英文 pattern | 扩充 slash command 列表 + 系统信号里程碑 + 英文模式 |
| `ccsm/core/status.py` | Modify L138-166 | 利用 PID `kind` 字段增强 BACKGROUND 检测 |
| `ccsm/models/session.py` | Modify SessionInfo | 新增 `custom_title`, `ai_title_from_cc`, `forked_from` 字段 |

---

## P0: 数据正确性修复（必须修）

### Task 1: 修复 `is_running` 幽灵进程 Bug

**Files:**
- Modify: `ccsm/core/discovery.py:147-173`

**问题:** `load_running_sessions()` 只检查 PID 文件是否存在，不验证进程是否存活。Claude Code 崩溃后残留的 PID 文件会导致幽灵 "running" 状态。

- [ ] **Step 1: 添加 PID 存活检查函数**

```python
# 在 load_running_sessions() 前添加
def _is_pid_alive(pid: int) -> bool:
    """Check if a process with given PID is still running."""
    try:
        os.kill(pid, 0)  # Signal 0: no kill, just check existence
        return True
    except (OSError, ProcessLookupError):
        return False
```

- [ ] **Step 2: 修改 `load_running_sessions()` 使用 PID 验证**

```python
def load_running_sessions(claude_dir: Path | None = None) -> dict[str, bool]:
    claude_dir = claude_dir or _default_claude_dir()
    sessions_dir = claude_dir / "sessions"
    if not sessions_dir.is_dir():
        return {}

    running: dict[str, bool] = {}
    for f in sessions_dir.iterdir():
        if f.suffix != ".json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            session_id = data.get("sessionId")
            pid = data.get("pid")
            if session_id and pid and _is_pid_alive(int(pid)):
                running[session_id] = True
            elif session_id and pid:
                logger.debug("Stale PID file %s (pid %s dead), ignoring", f.name, pid)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.debug("Failed to read session file %s: %s", f.name, e)

    return running
```

- [ ] **Step 3: 添加 `import os` 到文件顶部**（如果不存在）

- [ ] **Step 4: 同时提取 `kind` 字段供后续 BACKGROUND 检测使用**

将返回类型从 `dict[str, bool]` 改为 `dict[str, dict]`，包含 `{session_id: {"running": True, "kind": "interactive"|"bg"|...}}`。

```python
def load_running_sessions(claude_dir: Path | None = None) -> dict[str, dict]:
    """Return {session_id: {"running": True, "kind": "...", "pid": N}} for live sessions."""
    # ... 同上，但 running[session_id] = {"running": True, "kind": data.get("kind", "interactive"), "pid": pid}
```

注意：修改返回类型后需要同步修改 `main.py` 中的调用方（`self._running` 的使用方式）。

---

### Task 2: 修复 `display_name` 来源 — 从 JSONL 读取 `custom-title` / `ai-title`

**Files:**
- Modify: `ccsm/core/parser.py:150-270`（`parse_session_info` 函数）
- Modify: `ccsm/models/session.py`（SessionInfo 新增字段）

**问题:** `history.jsonl` 的 `display` 字段是用户的**输入文本**，不是会话标题。Claude Code 把真正的标题写入 JSONL 的 `custom-title` 和 `ai-title` 条目。

- [ ] **Step 1: 在 SessionInfo 中新增字段**

```python
# models/session.py SessionInfo 类中新增:
custom_title: Optional[str] = None       # 从 JSONL `custom-title` type 提取
ai_title_from_cc: Optional[str] = None   # 从 JSONL `ai-title` type 提取
forked_from_session: Optional[str] = None # 从消息的 forkedFrom.sessionId 提取
```

- [ ] **Step 2: 修改 `display_title` property 的优先级**

```python
@property
def display_title(self) -> str:
    """Best available title: user-assigned > custom-title > ai-title > slug > id."""
    return self.display_name or self.custom_title or self.ai_title_from_cc or self.slug or self.session_id[:8]
```

- [ ] **Step 3: 在 `parse_session_info()` 的解析循环中提取新 type**

在 `for line in lines:` 循环中，已有的 `msg_type not in _MESSAGE_TYPES` 检查之前，添加新的 type 处理：

```python
# 在现有的 "Skip non-message lines" 之前插入：
# Extract custom-title / ai-title (last one wins)
if msg_type == "custom-title":
    custom_title = data.get("title") or data.get("customTitle", "")
    continue
if msg_type == "ai-title":
    ai_title = data.get("title") or data.get("aiTitle", "")
    continue
# Extract tag entries
if msg_type == "tag":
    # Store tags for potential future use
    continue
# Extract forkedFrom from any message
if not forked_from and isinstance(data.get("forkedFrom"), dict):
    forked_from = data["forkedFrom"].get("sessionId")
```

在函数末尾的 `return SessionInfo(...)` 中添加这些新字段。

- [ ] **Step 4: 在循环前初始化新变量**

```python
custom_title: Optional[str] = None
ai_title: Optional[str] = None
forked_from: Optional[str] = None
```

---

### Task 3: 修复 lineage.py fork 检测

**Files:**
- Modify: `ccsm/core/lineage.py:53-130`

**三个问题:**
1. `(branch)` 小写 vs Claude Code 实际使用 `(Branch)` 大写
2. 没使用 `forkedFrom` 字段（最可靠的 fork 信号）
3. compact summary 前缀过时

- [ ] **Step 1: 修复大小写匹配**

```python
# lineage.py L70: 改为 case-insensitive
if display_name and display_name.lower().endswith("(branch)"):
```

并且支持编号：`(Branch 2)`, `(Branch 3)` 等

```python
import re
_BRANCH_SUFFIX = re.compile(r'\(branch(?:\s+\d+)?\)\s*$', re.IGNORECASE)

if display_name and _BRANCH_SUFFIX.search(display_name):
    signals.is_fork = True
    signals.fork_hint = "display_name_branch_suffix"
```

- [ ] **Step 2: 更新 compact summary 前缀列表**

```python
_COMPACT_SUMMARY_PREFIXES = (
    # 新版格式 (2025+)
    "This session is being continued from a previous conversation",
    # 旧版格式 (保持向后兼容)
    "Here is a summary of the conversation",
    "Here's a summary of the conversation",
    "Here is a summary of our conversation",
    "Here's a summary of our conversation",
)
```

- [ ] **Step 3: 在信号扫描中提取 `forkedFrom` 字段**

```python
# 在 JSONL 扫描循环中添加:
# Direct fork evidence from forkedFrom field
if not signals.is_fork:
    forked_from = entry.get("forkedFrom")
    if isinstance(forked_from, dict) and forked_from.get("sessionId"):
        signals.is_fork = True
        signals.fork_hint = "forkedFrom_field"
        signals.fork_source_id = forked_from["sessionId"]
```

在 `LineageSignals` dataclass 中新增：`fork_source_id: Optional[str] = None`

- [ ] **Step 4: 识别 `microcompact_boundary` subtype**

```python
# 在 compact boundary 检测处添加:
if entry_type == "system" and entry_subtype in ("compact_boundary", "microcompact_boundary"):
    signals.compact_count += 1
    signals.has_compact_boundary = True
```

---

## P1: 功能增强（强烈建议）

### Task 4: 增强 BACKGROUND 分类 — 利用 PID `kind` 字段

**Files:**
- Modify: `ccsm/core/status.py:138-166`

**问题:** 当前用关键词匹配判断 BACKGROUND，但 PID 文件的 `kind` 字段（`bg`/`daemon`/`daemon-worker`）是权威信号。

- [ ] **Step 1: 修改 `_is_background()` 添加 kind 检查**

需要将 `running_info` 传入分类函数。修改 `classify_session` 签名：

```python
def classify_session(
    session: SessionInfo,
    meta: Optional[SessionMeta] = None,
    running_info: Optional[dict] = None,   # 新增: {"kind": "bg", "pid": 123}
) -> tuple[Status, Priority]:
```

在 `_is_background()` 中：

```python
def _is_background(session: SessionInfo, running_info: Optional[dict] = None) -> bool:
    # Rule 0 (new): PID kind is authoritative
    if running_info and running_info.get("kind") in ("bg", "daemon", "daemon-worker"):
        return True
    # ... 其余规则不变
```

- [ ] **Step 2: 同步修改 `classify_all()` 传递 `running_info`**

```python
def classify_all(
    sessions: list[SessionInfo],
    all_meta: Optional[dict[str, SessionMeta]] = None,
    running_info: Optional[dict[str, dict]] = None,  # 新增
) -> None:
    for session in sessions:
        meta = all_meta.get(session.session_id) if all_meta else None
        info = running_info.get(session.session_id) if running_info else None
        classify_session(session, meta, running_info=info)
```

---

### Task 5: 扩充 milestones.py slash command + 系统信号

**Files:**
- Modify: `ccsm/core/milestones.py:85-90, 116-156`

- [ ] **Step 1: 扩充 `_SLASH_TRANSITIONS` 正则**

```python
_SLASH_TRANSITIONS = re.compile(
    r"^/(spawn|save-session|commit|interview-mode|plan|review|codex-review"
    r"|branch|compact|resume|init|export|diff|status|tasks|agents|rewind)",
    re.IGNORECASE,
)
```

- [ ] **Step 2: 添加系统消息里程碑检测**

当前 `_detect_signal()` 只检测 user 消息。新增一个函数检测 system/metadata 消息：

```python
def _detect_system_signal(msg: JSONLMessage) -> Optional[str]:
    """Detect milestone signals from system/metadata messages.
    
    These signals come from JSONL entries with type != user/assistant.
    Caller should pre-filter and pass relevant entries.
    """
    content = msg.content.strip().lower() if msg.content else ""
    
    # compact_boundary → 阶段结束
    if "conversation compacted" in content or "compact_boundary" in content:
        return "summary"
    
    # PR link → 交付里程碑
    if "pr-link" in content or "pull request" in content:
        return "review"
    
    return None
```

- [ ] **Step 3: 添加英文模式 discourse marker**

```python
# 英文 topic shift
_TOPIC_SHIFT_EN = re.compile(
    r"(next|let's move on|now let's|switch to|moving on|let's focus on"
    r"|let's start|let's work on|let's tackle)",
    re.IGNORECASE,
)

# 英文 approval
_APPROVAL_EN = re.compile(
    r"^(OK|sure|sounds good|looks good|LGTM|approved|go ahead|yes|yep|great|perfect)\s*$",
    re.IGNORECASE,
)

# 英文 directive
_DIRECTIVE_EN = re.compile(
    r"(please implement|go ahead and|start (implementing|building|coding|writing)"
    r"|make the change|fix (this|it|the)|create (a|the)|add (a|the)|remove|delete|refactor)",
    re.IGNORECASE,
)
```

在 `_detect_signal()` 的中文匹配之后添加英文 fallback：

```python
# English fallback patterns
if _REVIEW.search(clean) is None and _TOPIC_SHIFT.search(clean) is None:
    if _TOPIC_SHIFT_EN.search(clean):
        return "topic_shift"
    if len(clean) < 40 and _APPROVAL_EN.match(clean):
        return "approval"
    if _DIRECTIVE_EN.search(clean):
        return "directive"
```

---

### Task 6: parser.py — 过滤 `isCompactSummary` 消息 + `isMeta` 标记

**Files:**
- Modify: `ccsm/core/parser.py:210-225`

**问题:** compact 产生的压缩摘要消息被当作普通用户消息处理，导致 `first_user_content` 提取到 "This session is being continued..." 这种无意义内容。

- [ ] **Step 1: 在用户消息处理中跳过压缩摘要**

```python
if msg_type == "user":
    user_message_count += 1

    msg_obj = data.get("message") or {}
    
    # Skip compact summary messages (not real user input)
    if msg_obj.get("isCompactSummary") or data.get("isCompactSummary"):
        continue
    
    # Skip meta messages (system-injected, not user-visible)
    if msg_obj.get("isMeta") or data.get("isMeta"):
        continue
    
    msg_content = _extract_text(msg_obj.get("content", ""))
    # ... 其余逻辑不变
```

---

## P2: 覆盖完善（建议）

### Task 7: sanitize 补充遗漏的 XML 标签模式

**Files:**
- Modify: `ccsm/core/parser.py:60-78`

- [ ] **Step 1: 更新 `_sanitize_content()` 正则**

当前通用正则 `<[a-z_:-]...>` 已覆盖大多数标签，但 `antml:` 前缀需要单独处理：

```python
def _sanitize_content(text: str) -> Optional[str]:
    if not text:
        return text
    # Remove XML tag blocks (including antml: namespace)
    text = re.sub(r'</?[a-z_:-][^>]{0,200}>.*?</[a-z_:-][^>]{0,50}>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?[a-z_:-][^>]{0,200}>', '', text, flags=re.IGNORECASE)
    # Also handle antml: namespace prefix (used in Claude's function calls)
    text = re.sub(r'</?antml:[a-z_]+[^>]{0,200}>.*?</[a-z_]+>', '', text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r'</?antml:[a-z_]+[^>]{0,200}>', '', text, flags=re.IGNORECASE)
    # Remove system boilerplate lines
    text = re.sub(
        r'^(Base directory for this skill:?\s*.+|'
        r'This session is being continued.+|'
        r'Caveat: The messages below.+|'
        r'Copied to clipboard.+|'
        r'Compacted \(ctrl.+|'
        r'If you need specific details from before compaction.+|'
        r'Recent messages are preserved verbatim.+|'
        r'Continue the conversation from where it left off.+|'
        r'ARGUMENTS:?\s*.*)$',
        '', text, flags=re.MULTILINE | re.IGNORECASE,
    )
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    if not text or len(text) < 3:
        return None
    return text
```

---

## 优先级执行顺序

| 批次 | Task | 说明 | 风险 |
|------|------|------|------|
| **P0** | 1, 2, 3 | 数据正确性：幽灵进程、标题来源、fork 检测 | 不修会导致错误数据 |
| **P1** | 4, 5, 6 | 功能增强：BACKGROUND 分类、里程碑覆盖、compact 过滤 | 不修会降低质量 |
| **P2** | 7 | 覆盖完善：XML 标签清洗 | 不修偶尔有噪音 |

**依赖关系：**
- Task 4 依赖 Task 1（需要 `kind` 字段）
- 其余 Task 之间无依赖，可并行

**预估工作量：** 每个 Task 15-30 分钟，总计 ~2.5 小时
