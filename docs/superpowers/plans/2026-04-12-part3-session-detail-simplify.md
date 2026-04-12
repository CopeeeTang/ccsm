# CCSM Part 3: Session Detail 精简重构 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `SessionDetail` 组件从 7 个臃肿区块（含大量 emoji 和数据重叠）精简为 4 个核心区块，核心原则：**删除与列表卡片重复的信息、移除装饰性 emoji、合并重叠区块、确保可折叠区块默认关闭且展开键生效**。

**Architecture:**
1. **删除** `_mount_session_card` 区块（与列表卡片 100% 重复）；
2. **删除** `_mount_context_summary_section` 区块（与 digest 高度重叠）；
3. **合并** `_mount_where_left_off_section` 到 `_mount_last_exchange_section`（后者保留聊天气泡，前者的 next_steps 和 insight 作为附加行）；
4. **保留并精简** `_mount_digest_section`（去 emoji，四维结构保留）；
5. **保留并精简** `_mount_milestones_section`（去 emoji 标题前缀，时间线样式保留）；
6. **保留** `_mount_what_was_done_section`（已是 Collapsed，去 emoji）；
7. **断言**：所有 Collapsible 区块默认 `collapsed=True`（调研发现这已是事实），并通过集成测试确认 Enter 展开键可用。

**Tech Stack:** Textual (Python TUI), TCSS, pytest-asyncio

**前置依赖:** 独立，可以与 Part 1 / Part 2 并行执行。但推荐在 Part 2 Task 1（inline detail panel）之后执行，这样最终效果最直观。

**参考文件:**
- Part 3 调研报告：本轮对话内 part3-researcher 输出
- 用户诉求：
  - "session列表出现过的信息没必要在开头重复一遍"
  - "一些emoji我觉得很多余"
  - "where you left off跟last exchange重合，保留last exchange"
  - "保留AI digest，以及对应的修改"
  - "可折叠区块默认关闭且展开键生效"

---

## File Structure

### 将要新建的文件

| 路径 | 责任 |
|------|------|
| `tests/test_session_detail_simplified.py` | 精简后区块数量、emoji、折叠默认值、展开键的验证 |

### 将要修改的文件

| 路径 | 修改内容 |
|------|---------|
| `ccsm/tui/widgets/session_detail.py` | 删除 `_mount_session_card` 和 `_mount_context_summary_section`；删除 `_mount_where_left_off_section` 并把数据合并进 `_mount_last_exchange_section`；全文移除装饰性 emoji；精简 `_rebuild()` 组装顺序 |
| `ccsm/tui/styles/claude_native.tcss` | 删除 `.det-session-card`, `.-light-theme .det-session-card`, `.det-summary-intent`（如果不再被用到）；保留 `.det-digest-section`、`.det-milestones`、`.det-chat-*` |

---

## Task 1: 删除 Session Card 区块（去除列表信息重复）

**Files:**
- Modify: `ccsm/tui/widgets/session_detail.py:212-263` — 删除整个 `_mount_session_card` 方法
- Modify: `ccsm/tui/widgets/session_detail.py:177-208` — `_rebuild` 不再调用
- Modify: `ccsm/tui/styles/claude_native.tcss:395-403` — 删除 `.det-session-card` 及 light theme 变体

**Rationale:** session_card 区块展示 title/status/duration/msg_count/model/tokens/intent，这些在列表的 SessionCard 里已全部展示（Part 2 改造后尤其如此）。在 detail 顶部再看到同样的内容是冗余噪音。

### Step 1.1: 写失败测试 ☐

- [ ] **Step 1.1a: 创建测试文件**

Create `tests/test_session_detail_simplified.py`:

```python
"""Session detail panel should be simplified: no session_card header, no duplicate emojis."""
import pytest
from datetime import datetime, timezone
from ccsm.tui.app import CCSMApp
from ccsm.models.session import (
    SessionInfo, SessionMeta, SessionSummary, SessionDigest,
    Breakpoint, Status,
)


def _fake_session() -> SessionInfo:
    return SessionInfo(
        session_id="abc-12345678",
        jsonl_path="/tmp/fake.jsonl",
        display_name="修复登录Bug的OAuth刷新逻辑",
        status=Status.ACTIVE,
        message_count=42,
        first_timestamp=datetime(2026, 4, 12, 10, tzinfo=timezone.utc),
        last_timestamp=datetime(2026, 4, 12, 15, tzinfo=timezone.utc),
        model_name="claude-sonnet-4-5",
        total_input_tokens=1000,
        total_output_tokens=2000,
        duration_seconds=18000,
    )


def _fake_summary() -> SessionSummary:
    digest = SessionDigest(
        progress="Implemented OAuth token refresh logic.",
        breakpoint="Waiting for integration test results.",
        decisions=["Use refresh_token rotation"],
        todo=["Add rate limit", "Write docs"],
    )
    return SessionSummary(
        mode="llm",
        milestones=[],
        digest=digest,
        breakpoint=Breakpoint(
            milestone_label="Testing",
            detail="Running integration tests",
            last_topic="debugging OAuth refresh",
        ),
        key_insights=["refresh_token must be rotated on each use"],
    )


@pytest.mark.asyncio
async def test_detail_panel_has_no_session_card_section():
    """The deleted _mount_session_card block must not render in detail view."""
    from ccsm.tui.widgets.session_detail import SessionDetail
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted in this test context")

        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=None,
            last_assistant_messages=[],
            background_tasks=[],
        )
        await pilot.pause()

        # Assert: no widget with class `det-session-card` exists
        cards = detail.query(".det-session-card")
        assert len(cards) == 0, (
            "session_card block should be deleted from detail view"
        )
```

- [ ] **Step 1.1b: 跑测试看失败**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_detail_panel_has_no_session_card_section -v
```

Expected: FAIL — 当前 `_mount_session_card` 仍在渲染

### Step 1.2: 删除 _mount_session_card ☐

- [ ] **Step 1.2a: 删除方法**

编辑 `ccsm/tui/widgets/session_detail.py`，**删除** 第 212-263 行（整个 `_mount_session_card` 方法体）。

- [ ] **Step 1.2b: 更新 _rebuild 不再调用**

定位 `_rebuild` 方法（约 L177-208），找到 `self._mount_session_card(s)` 这一行并删除。

修改后的 `_rebuild` 内部组装顺序变为：

```python
def _rebuild(self) -> None:
    """Rebuild detail panel — 4 core sections after simplification."""
    self.remove_children()
    s = self._session
    if s is None:
        return

    # ── 1. AI Digest (always visible) ──
    self._mount_digest_section()

    # ── 2. Milestones (always visible) ──
    self._mount_milestones_section()

    # ── 3. What was done (collapsed, expandable) ──
    self._mount_what_was_done_section()

    # ── 4. Last Exchange (collapsed, expandable, merged with where_left_off) ──
    self._mount_last_exchange_section()
```

**删除以下调用:**
- `self._mount_session_card(s)` (原 L189)
- `self._mount_context_summary_section()` (原 L198，将在 Task 2 删除方法)
- `self._mount_where_left_off_section()` (原 L201，将在 Task 3 合并到 last_exchange)

- [ ] **Step 1.2c: 删除 CSS 类**

编辑 `ccsm/tui/styles/claude_native.tcss`，删除以下块（约 L395-403）:

```css
.det-session-card {
    margin: 0 1 1 1;
    padding: 1;
    height: auto;
    border: panel #3a3835;
    background: #1e1d1c;
}

.-light-theme .det-session-card {
    background: #ffffff;
    border: panel #e6e4df;
}
```

同时检查 `.det-summary-intent` (L474-483)。调研报告显示它 **仅在** `_mount_session_card` 内被引用。删除：

```css
.det-summary-intent {
    border-left: solid #d97757 40%;
    padding: 0 1;
    margin: 0 2;
    color: #b0aea5;
    text-style: italic;
    height: auto;
}
```

（如果 grep 确认无其他引用则删除；否则保留。）

```bash
grep -rn "det-summary-intent" /home/v-tangxin/GUI/projects/ccsm/.claude/worktrees/show/ccsm/
```

- [ ] **Step 1.2d: 跑测试验证通过**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_detail_panel_has_no_session_card_section -v
```

Expected: PASS

- [ ] **Step 1.2e: 提交**

```bash
cd /home/v-tangxin/GUI/projects/ccsm/.claude/worktrees/show
git add ccsm/tui/widgets/session_detail.py ccsm/tui/styles/claude_native.tcss tests/test_session_detail_simplified.py
git commit -m "refactor(detail): remove session_card header block (redundant with list card)"
```

---

## Task 2: 删除 Context Summary 区块

**Files:**
- Modify: `ccsm/tui/widgets/session_detail.py:397-472` — 删除整个 `_mount_context_summary_section` 方法

**Rationale:** `context_summary` 显示的信息（primary_request + key_concepts / description + insights / ai_intent fallback）和 `digest` 的 `progress` + `decisions` 有大量重叠。在用户"保留 AI digest"的明确诉求下，context_summary 是冗余的。

### Step 2.1: 写失败测试 ☐

- [ ] **Step 2.1a: 追加测试**

Append to `tests/test_session_detail_simplified.py`:

```python
@pytest.mark.asyncio
async def test_no_context_summary_section():
    from ccsm.tui.widgets.session_detail import SessionDetail
    from textual.widgets import Collapsible
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted")
        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=None,
            last_assistant_messages=[],
            background_tasks=[],
        )
        await pilot.pause()

        # Walk all Collapsible widgets; none should have CONTEXT SUMMARY title
        collapsibles = list(detail.query(Collapsible))
        titles = [c.title for c in collapsibles]
        assert not any("CONTEXT SUMMARY" in t.upper() for t in titles), (
            f"Context summary should be deleted. Found titles: {titles}"
        )
```

- [ ] **Step 2.1b: 跑测试看失败**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_no_context_summary_section -v
```

Expected: FAIL（当前仍有 CONTEXT SUMMARY）

### Step 2.2: 删除方法 ☐

- [ ] **Step 2.2a: 删除 _mount_context_summary_section**

编辑 `ccsm/tui/widgets/session_detail.py`，删除第 397-472 行（整个 `_mount_context_summary_section` 方法）。

调用点已在 Task 1 Step 1.2b 中从 `_rebuild` 移除。

- [ ] **Step 2.2b: 跑测试验证**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_no_context_summary_section -v
```

Expected: PASS

- [ ] **Step 2.2c: 提交**

```bash
git add ccsm/tui/widgets/session_detail.py tests/test_session_detail_simplified.py
git commit -m "refactor(detail): remove context_summary block (duplicates digest)"
```

---

## Task 3: 合并 Where You Left Off 到 Last Exchange

**Files:**
- Modify: `ccsm/tui/widgets/session_detail.py:476-530` — 删除 `_mount_where_left_off_section`
- Modify: `ccsm/tui/widgets/session_detail.py:582-630` — 扩展 `_mount_last_exchange_section`

**Rationale:** 两者 100% 共享 `last_user_msg` + `last_assistant_msg` 的主体内容。where_left_off 多的是：
1. `summary.breakpoint.last_topic` — 下一步
2. `summary.key_insights[-1]` — 最后的洞察

这两个字段作为"附加行"追加到 last_exchange 聊天气泡之后即可。

### Step 3.1: 写失败测试 ☐

- [ ] **Step 3.1a: 追加测试**

Append to `tests/test_session_detail_simplified.py`:

```python
@pytest.mark.asyncio
async def test_where_left_off_merged_into_last_exchange():
    """After merge: no standalone 'WHERE YOU LEFT OFF', but last_topic visible in LAST EXCHANGE."""
    from ccsm.tui.widgets.session_detail import SessionDetail
    from textual.widgets import Collapsible
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted")

        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=Breakpoint(
                milestone_label="Testing",
                detail="Running tests",
                last_topic="debugging OAuth refresh",
            ),
            last_assistant_messages=["Last AI reply content"],
            background_tasks=[],
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        titles = [c.title for c in collapsibles]
        # WHERE YOU LEFT OFF should be gone
        assert not any("WHERE YOU LEFT OFF" in t.upper() for t in titles), (
            f"where_left_off should be merged. Titles: {titles}"
        )
        # LAST EXCHANGE should still exist
        assert any("LAST EXCHANGE" in t.upper() for t in titles), (
            f"last_exchange should remain. Titles: {titles}"
        )
```

- [ ] **Step 3.1b: 跑测试看失败**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_where_left_off_merged_into_last_exchange -v
```

Expected: FAIL

### Step 3.2: 重写 _mount_last_exchange_section ☐

- [ ] **Step 3.2a: 删除 _mount_where_left_off_section**

编辑 `ccsm/tui/widgets/session_detail.py`，删除第 476-530 行（整个 `_mount_where_left_off_section` 方法）。

- [ ] **Step 3.2b: 扩展 _mount_last_exchange_section**

替换第 582-630 行（`_mount_last_exchange_section` 整个方法）为：

```python
def _mount_last_exchange_section(self) -> None:
    """Last exchange + recovery context — merged chat bubbles + next steps.

    Merged from (formerly) _mount_where_left_off_section:
      - Adds breakpoint.last_topic as "Next" line after bubbles
      - Adds key_insights[-1] as "Insight" line after bubbles
    """
    from textual.containers import Horizontal

    dd = self._detail_data
    has_exchange = dd and (dd.last_user_msg or dd.last_assistant_msg)
    has_reply = bool(self._last_replies)
    has_next = False
    has_insight = False

    # Check recovery fields
    bp = self._summary.breakpoint if self._summary else None
    next_topic = bp.last_topic if bp else None
    insight = None
    if self._summary and self._summary.key_insights:
        insight = self._summary.key_insights[-1]

    has_next = bool(next_topic)
    has_insight = bool(insight)

    if not any([has_exchange, has_reply, has_next, has_insight]):
        return

    # Default collapsed — user's requirement
    collapsible = Collapsible(title="LAST EXCHANGE", collapsed=True)
    self.mount(collapsible)

    V = "#e8e6dc"
    K = "#b0aea5"

    # ── User bubble ──
    if dd and dd.last_user_msg:
        user_text = dd.last_user_msg[:300]
        if len(dd.last_user_msg) > 300:
            user_text += "…"
        row = Horizontal(classes="det-chat-row")
        collapsible.mount(row)
        row.mount(Static(" YOU ", classes="det-chat-avatar det-chat-avatar-user"))
        row.mount(Static(
            f"[{V}]{rich_escape(user_text)}[/]",
            classes="det-chat-msg",
        ))

    # ── AI bubble ──
    ai_text = None
    if dd and dd.last_assistant_msg:
        ai_text = dd.last_assistant_msg[:300]
        if len(dd.last_assistant_msg) > 300:
            ai_text += "…"
    elif self._last_replies:
        ai_text = self._last_replies[-1][:300]
        if len(self._last_replies[-1]) > 300:
            ai_text += "…"

    if ai_text:
        row = Horizontal(classes="det-chat-row")
        collapsible.mount(row)
        row.mount(Static("  AI ", classes="det-chat-avatar"))
        row.mount(Static(
            f"[{K}]{rich_escape(ai_text)}[/]",
            classes="det-chat-msg",
        ))

    # ── Recovery context (merged from where_left_off) ──
    if has_next or has_insight:
        ctx_lines: list[str] = []
        if next_topic:
            ctx_lines.append(
                f"  [#788c5d bold]Next[/] [{V}]{rich_escape(next_topic)}[/]"
            )
        if insight:
            trunc = insight[:100] + ("…" if len(insight) > 100 else "")
            ctx_lines.append(
                f"  [#6a9bcc bold]Insight[/] [{V}]{rich_escape(trunc)}[/]"
            )
        collapsible.mount(Static(
            "\n".join(ctx_lines),
            classes="detail-section-body",
        ))
```

**注意关键改动:**
- 标题改为纯 `LAST EXCHANGE`（无 💬 emoji）
- 仍然 `collapsed=True`（默认关闭）
- 删除头像上的 emoji——`YOU` 和 `AI` 是纯文本标签
- 删除 `→ 下一步` 中的箭头 emoji，改为纯文字 `Next`
- 删除 `💡 Insight` 中的灯泡 emoji

- [ ] **Step 3.2c: 跑测试验证**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_where_left_off_merged_into_last_exchange -v
```

Expected: PASS

- [ ] **Step 3.2d: 提交**

```bash
git add ccsm/tui/widgets/session_detail.py tests/test_session_detail_simplified.py
git commit -m "refactor(detail): merge where_left_off into last_exchange section"
```

---

## Task 4: 全文去 Emoji

**Files:**
- Modify: `ccsm/tui/widgets/session_detail.py` — 定位并删除所有装饰性 emoji

**清单（从 part3-researcher 调研）:**

| 区块 | Emoji | 位置 | 处理 |
|------|-------|------|------|
| Digest 标题 | `🧠 AI DIGEST` | L274 | 改为 `AI DIGEST` |
| Milestones 标题 | `🧭 MILESTONES` | L322 | 改为 `MILESTONES` |
| Digest: Progress | `📊 Progress` | L291 | 改为 `Progress` |
| Digest: Decisions | `⚖ Decisions` | L296 | 改为 `Decisions` |
| Digest: Breakpoint | `⏸ Breakpoint` | L301 | 改为 `Breakpoint` |
| Digest: Todo | `→ Todo` | L306 | 改为 `Todo` |
| What was done 标题 | `🔧 WHAT WAS DONE` | L576 | 改为 `WHAT WAS DONE` |
| What was done: Edited | `📝 Edited` | L554 | 改为 `Edited` |
| What was done: Ran | `⚡ Ran` | L560 | 改为 `Ran` |
| What was done: Read | `📖 Read` | L567 | 改为 `Read` |
| What was done: Agent | `🤖 Agent` | L572 | 改为 `Agent` |
| Milestones icons | `✓ ▶ ○` | L86-89 `_MS_ICONS` | **保留**（这是功能性状态符号，不是装饰） |
| Milestones: HERE | `← HERE` | L361 | **保留**（功能性标记） |

**Rationale:** 保留 `✓ ▶ ○` 和 `← HERE` 因为它们编码**状态信息**（done/in-progress/pending）；删除其他所有 emoji 因为它们只是装饰。

### Step 4.1: 写断言测试 ☐

- [ ] **Step 4.1a: 追加测试**

Append to `tests/test_session_detail_simplified.py`:

```python
import re

# Emoji chars that must NOT appear in detail panel text
DECORATIVE_EMOJIS = [
    "🧠", "🧭", "📋", "📝", "🔧", "💬",  # section titles
    "📊", "⚖", "⏸",                      # digest fields
    "🗣", "🤖", "💡",                     # where_left_off (should be deleted)
    "⚡", "📖",                            # what_was_done
]


@pytest.mark.asyncio
async def test_no_decorative_emoji_in_detail_panel():
    """All decorative emoji should be stripped from section titles and body."""
    from ccsm.tui.widgets.session_detail import SessionDetail
    from textual.widgets import Collapsible, Static
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted")

        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=None,
            last_assistant_messages=["reply"],
            background_tasks=[],
        )
        await pilot.pause()

        # Gather all text content
        all_text = ""
        for w in detail.query(Static):
            try:
                all_text += str(w.renderable) + "\n"
            except Exception:
                pass
        for c in detail.query(Collapsible):
            all_text += c.title + "\n"

        for emoji in DECORATIVE_EMOJIS:
            assert emoji not in all_text, (
                f"Decorative emoji {emoji!r} should be removed from detail panel. "
                f"Found in: {all_text[:200]}..."
            )
```

- [ ] **Step 4.1b: 跑测试看失败**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_no_decorative_emoji_in_detail_panel -v
```

Expected: FAIL — 当前大量 emoji 存在

### Step 4.2: 批量替换 emoji ☐

- [ ] **Step 4.2a: 编辑 _mount_digest_section（L267-310）**

替换区块标题和内部字段：

原代码（约 L274）:
```python
section.mount(Static(
    "  [#a8a29e]🧠 AI DIGEST[/]",
    classes="detail-section-title",
))
```

改为:
```python
section.mount(Static(
    "  [#a8a29e bold]AI DIGEST[/]",
    classes="detail-section-title",
))
```

原代码（约 L291-306）:
```python
lines.append(f"  [#788c5d bold]📊 Progress[/]  [{V}]{rich_escape(digest.progress)}[/]")
# ...
lines.append(f"  [#d97757 bold]⚖ Decisions[/]")
# ...
lines.append(f"  [#e87b7b bold]⏸ Breakpoint[/] [{V}]{rich_escape(digest.breakpoint)}[/]")
# ...
lines.append(f"  [#a78bfa bold]→ Todo[/]")
```

改为:
```python
lines.append(f"  [#788c5d bold]Progress[/]  [{V}]{rich_escape(digest.progress)}[/]")
# ...
lines.append(f"  [#d97757 bold]Decisions[/]")
# ...
lines.append(f"  [#e87b7b bold]Breakpoint[/] [{V}]{rich_escape(digest.breakpoint)}[/]")
# ...
lines.append(f"  [#a78bfa bold]Todo[/]")
```

- [ ] **Step 4.2b: 编辑 _mount_milestones_section（L314-395）**

原代码（约 L322）:
```python
section.mount(Static(
    "  [#a8a29e]🧭 MILESTONES[/]",
    classes="detail-section-title",
))
```

改为:
```python
section.mount(Static(
    "  [#a8a29e bold]MILESTONES[/]",
    classes="detail-section-title",
))
```

**保留** `_MS_ICONS` 的 `✓ ▶ ○`（功能性）以及 `← HERE` 标记（功能性）。

- [ ] **Step 4.2c: 编辑 _mount_what_was_done_section（L534-578）**

原代码（约 L554-576）:
```python
lines.append(f"  [#788c5d]📝 Edited[/]  [{V}]{...}[/]")
# ...
lines.append(f"  [#facc15]⚡ Ran[/]     [{V}]{...}[/]")
# ...
lines.append(f"  [#6a9bcc]📖 Read[/]    [{V}]{...}[/]")
# ...
lines.append(f"  [#a855f7]🤖 Agent[/]  [{V}]{...}[/]")
# ...
collapsible = Collapsible(title="🔧 WHAT WAS DONE", collapsed=True)
```

改为:
```python
lines.append(f"  [#788c5d bold]Edited[/]  [{V}]{...}[/]")
# ...
lines.append(f"  [#facc15 bold]Ran[/]     [{V}]{...}[/]")
# ...
lines.append(f"  [#6a9bcc bold]Read[/]    [{V}]{...}[/]")
# ...
lines.append(f"  [#a855f7 bold]Agent[/]   [{V}]{...}[/]")
# ...
collapsible = Collapsible(title="WHAT WAS DONE", collapsed=True)
```

- [ ] **Step 4.2d: 跑测试验证所有 emoji 已移除**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py::test_no_decorative_emoji_in_detail_panel -v
```

Expected: PASS

- [ ] **Step 4.2e: 提交**

```bash
git add ccsm/tui/widgets/session_detail.py tests/test_session_detail_simplified.py
git commit -m "refactor(detail): strip decorative emoji from section titles and fields"
```

---

## Task 5: 验证 Collapsible 默认关闭 + 展开键生效

**Files:**
- Create: Test cases in `tests/test_session_detail_simplified.py`

**调研发现:** 所有 Collapsible 已经是 `collapsed=True`。用户担忧的问题是"修改完的条件显示默认关闭，展开健是可用的"。需要写测试**断言**：
1. 初次加载时所有 Collapsible 都是折叠状态
2. 对每个 Collapsible 发送 "Enter" 键（或点击标题）能成功展开
3. 展开后能看到内容

### Step 5.1: 写集成测试 ☐

- [ ] **Step 5.1a: 追加 Collapsible 断言**

Append to `tests/test_session_detail_simplified.py`:

```python
@pytest.mark.asyncio
async def test_all_collapsibles_default_collapsed():
    from ccsm.tui.widgets.session_detail import SessionDetail
    from textual.widgets import Collapsible
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted")

        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=None,
            last_assistant_messages=["last reply"],
            background_tasks=[],
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        assert len(collapsibles) >= 2, (
            "Expected at least WHAT WAS DONE + LAST EXCHANGE collapsibles"
        )
        for c in collapsibles:
            assert c.collapsed is True, (
                f"Collapsible {c.title!r} should default to collapsed"
            )


@pytest.mark.asyncio
async def test_collapsible_expands_on_click():
    from ccsm.tui.widgets.session_detail import SessionDetail
    from textual.widgets import Collapsible
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted")

        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=None,
            last_assistant_messages=["last reply"],
            background_tasks=[],
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        target = collapsibles[0]
        assert target.collapsed is True

        # Programmatic expand (Textual's Collapsible supports .collapsed attribute write)
        target.collapsed = False
        await pilot.pause()
        assert target.collapsed is False

        # Re-collapse
        target.collapsed = True
        await pilot.pause()
        assert target.collapsed is True


@pytest.mark.asyncio
async def test_collapsible_count_is_two():
    """After simplification: only WHAT WAS DONE + LAST EXCHANGE are collapsibles."""
    from ccsm.tui.widgets.session_detail import SessionDetail
    from textual.widgets import Collapsible
    async with CCSMApp().run_test() as pilot:
        try:
            detail = pilot.app.query_one(SessionDetail)
        except Exception:
            pytest.skip("SessionDetail not mounted")

        detail.show_session(
            _fake_session(),
            meta=None,
            summary=_fake_summary(),
            breakpoint=None,
            last_assistant_messages=["reply"],
            background_tasks=[],
        )
        await pilot.pause()

        collapsibles = list(detail.query(Collapsible))
        titles = sorted(c.title for c in collapsibles)
        # Expected: exactly 2 collapsibles after Part 3 simplification
        # (context_summary and where_left_off deleted)
        assert len(collapsibles) == 2, (
            f"Expected 2 collapsibles (WHAT WAS DONE + LAST EXCHANGE), got {len(collapsibles)}: {titles}"
        )
        assert any("WHAT WAS DONE" in t.upper() for t in titles)
        assert any("LAST EXCHANGE" in t.upper() for t in titles)
```

- [ ] **Step 5.1b: 跑测试**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_detail_simplified.py -v
```

Expected: 全部 PASS

- [ ] **Step 5.1c: 提交**

```bash
git add tests/test_session_detail_simplified.py
git commit -m "test(detail): assert simplified detail panel has 2 collapsibles default closed"
```

---

## Task 6: 验收与回归

- [ ] **Step 6.1: 全量测试**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/ -v --asyncio-mode=auto 2>&1 | tail -40
```

Expected: Part 3 新增测试 + 所有原有测试 PASS

- [ ] **Step 6.2: 手动 TUI 验收**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m ccsm
```

选中一个会话，打开 detail。视觉检查：
- [ ] 没有 session_card 顶部区块
- [ ] 没有 CONTEXT SUMMARY
- [ ] 没有 WHERE YOU LEFT OFF 独立区块
- [ ] LAST EXCHANGE 里能看到 Next 和 Insight 行（若有数据）
- [ ] Digest 四个字段显示：Progress / Decisions / Breakpoint / Todo（无 emoji）
- [ ] Milestones 区块 `✓ ▶ ○` 状态图标仍在
- [ ] WHAT WAS DONE 和 LAST EXCHANGE 默认都是折叠状态
- [ ] 点击折叠区块标题能展开

- [ ] **Step 6.3: 标记 Part 3 完成**

---

## Self-Review 清单

- [x] 删除了与列表卡片重复的 session_card 区块
- [x] 删除了与 digest 重叠的 context_summary 区块
- [x] where_left_off 的 next_steps 和 insight 作为附加行合并进 last_exchange
- [x] digest 和 milestones 保留但去掉装饰 emoji
- [x] 功能性符号（`✓ ▶ ○`、`← HERE`）保留
- [x] 所有 Collapsible 默认 collapsed=True（已有 + 显式断言）
- [x] 最终区块数从 7 降到 4：digest / milestones / what_was_done / last_exchange
- [x] 始终可见的非 Collapsible 区块从 3 降到 2：digest / milestones
- [x] 新增测试覆盖删除项、合并项、折叠状态、emoji 移除
- [x] 每个 Task 有独立的提交点
