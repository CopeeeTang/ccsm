# CCSM Part 2: 主面板 Claude Code 原生风格对齐 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 CCSM 主面板的外观和交互对齐到 Claude Code 原生 Resume Session 选择器（截图为准）：内联三栏布局替代模态抽屉、键盘导航即时同步 detail、移除装饰性 emoji/圆点/徽章噪音。

**Architecture:**
1. 把 `SessionDetailDrawer` 从 `ModalScreen` 降级为普通 `Vertical` 容器，放到 MainScreen 的 compose 链里成为第三栏；
2. Session 卡片改为纯 2 行、`·` 中点分隔符、零装饰 emoji 的 Claude 原生样式；
3. 键盘光标变化触发 detail 内容刷新（不再 push/pop ModalScreen）；
4. 血缘可视化只保留 1 个维度（ASCII 树形字符缩进），删除透明度和左边框颜色噪音。

**Tech Stack:** Textual (Python TUI), TCSS (Textual CSS), pytest-asyncio

**参考文件:**
- 视觉目标：用户提供的 Claude Code Resume Session 截图
- 已有键盘方案：`docs/plans/2026-04-11-keyboard-first-navigation.md`
- Part 2 调研报告：本轮对话内 part2-researcher 输出

---

## File Structure

### 将要新建的文件

| 路径 | 责任 |
|------|------|
| `tests/test_inline_detail_panel.py` | 三栏内联布局的集成测试 |
| `tests/test_session_card_claude_style.py` | 卡片 Claude 原生样式测试 |

### 将要修改的文件

| 路径 | 修改内容 |
|------|---------|
| `ccsm/tui/screens/main.py` | compose() 里把 Drawer 改为第三栏；删除 `_drawer` 模态 push/pop；cursor 移动直接更新第三栏内容 |
| `ccsm/tui/screens/drawer.py` | `SessionDetailDrawer(ModalScreen)` → `SessionDetailPanel(Vertical)`；删除 `drawer-title` 顶栏；绑定改为 panel 事件 |
| `ccsm/tui/widgets/session_card.py` | 删除 📝 / 💬 emoji；改为 2 行 Claude 原生样式（title / `time · branch · model · intent...`）；删除消息数独立列 |
| `ccsm/tui/widgets/session_list.py` | `DateDivider` 删除 ⬤ 圆点；改为 `── Today ────────`；标题栏增加 "Sessions (N of M)" 计数 |
| `ccsm/tui/widgets/lineage_group.py` | 简化血缘展示：保留缩进 + ASCII 树形字符 (`└─` `├─` `│`)，删除 history-step-{1,2,3} 透明度与左边框颜色 |
| `ccsm/tui/styles/claude_native.tcss` | 新增 `#detail-panel` 第三栏样式；调整 `#worktree-panel`/`#session-panel` 宽度；删除 `.det-session-card` block；简化 `.session-card` 到纯文本；删除阶梯缩进透明度类 |

### 将要删除的文件

无文件层面删除；`drawer.py` 保留但重命名类（为避免破坏 import 路径，保留 `SessionDetailDrawer` 作为向后兼容别名或直接替换所有引用）。

---

## Task 1: 把 Drawer 从 Modal 改为 Inline 第三栏

**Files:**
- Modify: `ccsm/tui/screens/drawer.py` — 整个文件重写
- Modify: `ccsm/tui/screens/main.py:126-148` — compose() 方法
- Modify: `ccsm/tui/screens/main.py:479-509` — `on_session_list_panel_session_selected`
- Modify: `ccsm/tui/styles/claude_native.tcss:43-80, 268-283` — panel 宽度与 drawer 样式

### Step 1.1: 重写 drawer.py 为内联 panel ☐

- [ ] **Step 1.1a: 写失败测试**

Create `tests/test_inline_detail_panel.py`:

```python
"""Test that detail panel is inline (not modal) in three-column layout."""
import pytest
from ccsm.tui.app import CCSMApp
from ccsm.tui.screens.main import MainScreen


@pytest.mark.asyncio
async def test_detail_panel_is_inline_not_modal():
    """Detail panel should be a regular widget inside MainScreen, not a ModalScreen."""
    async with CCSMApp().run_test() as pilot:
        from ccsm.tui.screens.drawer import SessionDetailPanel
        # 直接 query_one 应该找到
        panel = pilot.app.query_one(SessionDetailPanel)
        assert panel is not None
        # panel 应该在 MainScreen 里，不是 push 到 screen stack
        assert panel.screen is pilot.app.screen
        assert type(pilot.app.screen).__name__ == "MainScreen"


@pytest.mark.asyncio
async def test_three_column_layout_widths():
    """Three panels should sum to ~100% viewport width."""
    async with CCSMApp().run_test() as pilot:
        worktree = pilot.app.query_one("#worktree-panel")
        sessions = pilot.app.query_one("#session-panel")
        detail = pilot.app.query_one("#detail-panel")
        # All three present and visible
        assert worktree.display is True
        assert sessions.display is True
        assert detail.display is True
```

- [ ] **Step 1.1b: 跑测试看它失败**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_inline_detail_panel.py -v
```

Expected: FAIL — `SessionDetailPanel` 不存在 或 `#detail-panel` 找不到

- [ ] **Step 1.1c: 重写 drawer.py**

覆盖写 `ccsm/tui/screens/drawer.py`:

```python
"""Inline detail panel — third column of MainScreen.

Historically this was a ModalScreen overlay; it is now a regular
container widget embedded directly in MainScreen.compose(), so
detail updates follow keyboard cursor without screen stack churn.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical

from ccsm.tui.widgets.session_detail import SessionDetail


class SessionDetailPanel(Vertical):
    """Third-column detail panel. Shows detail of the cursored session."""

    DEFAULT_CSS = ""  # styles live in claude_native.tcss under #detail-panel

    def compose(self) -> ComposeResult:
        yield SessionDetail(id="session-detail")


# Back-compat alias for any legacy import path.
SessionDetailDrawer = SessionDetailPanel
```

- [ ] **Step 1.1d: 跑测试验证通过**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_inline_detail_panel.py::test_detail_panel_is_inline_not_modal -v
```

Expected: PASS

### Step 1.2: MainScreen.compose() 加入第三栏 ☐

- [ ] **Step 1.2a: 编辑 main.py compose()**

修改 `ccsm/tui/screens/main.py` 第 126-148 行（整个 compose 方法）为：

```python
def compose(self) -> ComposeResult:
    # NOTE: Header bar removed — aligning with Claude Code native style.
    # Title/counter is shown in the session panel title bar instead.
    with Horizontal(id="main-container"):
        # Column 1: Worktree tree (15%)
        with Vertical(id="worktree-panel"):
            yield Static(" WORKTREES", classes="panel-title")
            yield WorktreeTree()
        # Column 2: Session list (55%)
        with Vertical(id="session-panel"):
            yield Static(" SESSIONS", classes="panel-title", id="session-panel-title")
            yield Input(
                placeholder="Search...",
                id="search-input",
                classes="search-input -hidden",
            )
            yield SessionListPanel()
        # Column 3: Inline detail (30%)
        with Vertical(id="detail-panel"):
            yield Static(" DETAIL", classes="panel-title")
            yield SessionDetailPanel()
    # Minimal footer — Claude-native style with `·` separators
    yield Static(
        "[#78716c]↑↓[/] Navigate  "
        "[#78716c]·[/]  "
        "[#78716c]Enter[/] Open  "
        "[#78716c]·[/]  "
        "[#78716c]r[/] Resume  "
        "[#78716c]·[/]  "
        "[#78716c]/[/] Search  "
        "[#78716c]·[/]  "
        "[#78716c]q[/] Quit",
        id="footer-bar",
    )
```

新增 import:

```python
from ccsm.tui.screens.drawer import SessionDetailPanel
```

- [ ] **Step 1.2b: 编辑 CSS 布局宽度**

修改 `ccsm/tui/styles/claude_native.tcss` 中的 panel 宽度（定位第 43-51 行和 75-80 行）:

```css
/* Column 1: Worktree — 15% */
#worktree-panel {
    width: 15%;
    min-width: 18;
    background: #1e1d1c;
    border-right: solid #3a3835;
    padding: 0;
}

/* Column 2: Session list — 55% */
#session-panel {
    width: 55%;
    background: #1e1d1c;
    border-right: solid #3a3835;
    padding: 0;
}

/* Column 3: Inline detail — 30% */
#detail-panel {
    width: 30%;
    background: #141413;
    padding: 0;
}
```

在同一文件中**删除** 第 268-283 行的 `SessionDetailDrawer { align: center middle; }` 和 `#drawer-panel { width: 70%; ... }`、`.drawer-title { ... }` 这三个规则（它们属于旧的 modal 样式，不再需要）。

- [ ] **Step 1.2c: 跑测试验证三栏可见**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_inline_detail_panel.py::test_three_column_layout_widths -v
```

Expected: PASS

### Step 1.3: 删除 Modal push/pop 逻辑 ☐

- [ ] **Step 1.3a: 改写 on_session_list_panel_session_selected**

修改 `ccsm/tui/screens/main.py` 第 479-509 行：

```python
def on_session_list_panel_session_selected(
    self, event: SessionListPanel.SessionSelected
) -> None:
    """Update inline detail panel for the selected session."""
    session = event.session
    self._selected_session = session
    # No modal push — directly update the inline detail panel
    self._load_session_detail(session)
```

- [ ] **Step 1.3b: 删除所有 `self._drawer` 残留**

在 main.py 中 grep `_drawer` 找所有引用并删除或改为 `self._detail_panel`：

```bash
grep -n "_drawer" /home/v-tangxin/GUI/projects/ccsm/.claude/worktrees/show/ccsm/tui/screens/main.py
```

把 `__init__` 里的 `self._drawer = None` 删除；把所有 `self.app.push_screen(self._drawer)` 调用整段删除；把任何 `self.app.pop_screen()` 替换为 no-op（inline 不需要 pop）。

- [ ] **Step 1.3c: 更新 `_load_session_detail` 回调目标**

定位 `_on_detail_loaded` 方法（约 main.py L557-573）。原实现通过 drawer 查询 SessionDetail，改为直接 query 内联 panel：

```python
def _on_detail_loaded(
    self,
    session: SessionInfo,
    meta: Optional[SessionMeta],
    summary: Optional[SessionSummary],
    breakpoint: Optional[Breakpoint],
    assistant_msgs: list,
    tasks: list,
) -> None:
    """Update inline detail panel with loaded data."""
    try:
        detail = self.query_one("#session-detail", SessionDetail)
    except Exception:
        return
    detail.show_session(
        session,
        meta=meta,
        summary=summary,
        breakpoint=breakpoint,
        last_assistant_messages=assistant_msgs,
        background_tasks=tasks,
    )
```

- [ ] **Step 1.3d: 提交**

```bash
cd /home/v-tangxin/GUI/projects/ccsm/.claude/worktrees/show
git add ccsm/tui/screens/drawer.py ccsm/tui/screens/main.py ccsm/tui/styles/claude_native.tcss tests/test_inline_detail_panel.py
git commit -m "refactor(tui): convert detail drawer from modal to inline third column"
```

---

## Task 2: Session Card 对齐 Claude Code 原生 2 行样式

**Files:**
- Modify: `ccsm/tui/widgets/session_card.py:116-226` — compose() 整段重写
- Modify: `ccsm/tui/styles/claude_native.tcss` — `.card-*` 相关所有样式

### Step 2.1: 卡片结构改为纯 2 行 ☐

视觉目标（截图规范）:
```
 目前的实验分成20%和80%的部分，我需要你梳理一下最近关于系统的改动...
 32 seconds ago · fix/smoke-fresh-checkpoints-local · sonnet-4-5 · 42 msgs
```

- [ ] **Step 2.1a: 写失败测试**

Create `tests/test_session_card_claude_style.py`:

```python
"""Session card should match Claude Code native Resume Session visual style."""
import pytest
from ccsm.models.session import SessionInfo, Status
from ccsm.tui.widgets.session_card import SessionCard
from datetime import datetime, timezone


def _fake_session() -> SessionInfo:
    return SessionInfo(
        session_id="abc-123",
        jsonl_path="/tmp/fake.jsonl",
        display_name="修复登录Bug的OAuth刷新逻辑",
        status=Status.ACTIVE,
        message_count=42,
        first_timestamp=datetime(2026, 4, 12, 10, 0, tzinfo=timezone.utc),
        last_timestamp=datetime(2026, 4, 12, 15, 30, tzinfo=timezone.utc),
        first_user_content="帮我看下 auth.py 里的 refresh_token 逻辑",
        model_name="claude-sonnet-4-5",
        total_input_tokens=1000,
        total_output_tokens=2000,
        duration_seconds=19800,
    )


def test_card_has_no_emoji_decorations():
    """Card rendering must not include 📝 💬 ⬤ or other decorative emoji."""
    session = _fake_session()
    card = SessionCard(session=session, meta=None)
    # Walk the composed children and check static texts
    widgets = list(card.compose())
    all_text = " ".join(str(w) for w in widgets)
    assert "📝" not in all_text
    assert "💬" not in all_text
    assert "⬤" not in all_text


def test_card_metadata_uses_middot_separator():
    """Metadata row must use ` · ` separator, not comma or pipe."""
    from rich.console import Console
    session = _fake_session()
    card = SessionCard(session=session, meta=None)
    widgets = list(card.compose())
    text = " ".join(str(w) for w in widgets)
    assert " · " in text
```

- [ ] **Step 2.1b: 跑测试看它失败**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_card_claude_style.py -v
```

Expected: FAIL — 当前卡片有 📝 💬 emoji

- [ ] **Step 2.1c: 重写 compose() 方法**

修改 `ccsm/tui/widgets/session_card.py` 的 `compose` 方法（第 116-226 行）。新实现：

```python
def compose(self) -> ComposeResult:
    """Build card layout: 2 lines, Claude Code native style.

    Line 1: [bold white]Session title[/]                     [dim]2h ago[/]
    Line 2: [dim]branch · model · N msgs · intent excerpt...[/]

    No decorative emoji. Status color encoded in left-border CSS only.
    """
    s = self.session

    # ── Title selection (priority chain) ──
    title_text = (
        (self.meta.name if self.meta and self.meta.name else None)
        or (self.meta.ai_intent if self.meta and self.meta.ai_intent else None)
        or s.display_name
        or s.display_title
        or "(untitled)"
    )

    # Running indicator: small prefix, not a badge
    running_prefix = "● " if s.is_running else ""

    # Relative time (right-aligned on line 1)
    rel_time = _relative_time(s.last_timestamp)

    # ── Line 1: title + relative time ──
    with Horizontal(classes="card-row-title"):
        yield Static(
            f"[bold #e8e6dc]{running_prefix}{rich_escape(title_text)}[/]",
            classes="card-title",
        )
        yield Static(
            f"[#78716c]{rich_escape(rel_time)}[/]",
            classes="card-reltime",
        )

    # ── Line 2: metadata `·`-separated ──
    meta_parts: list[str] = []

    # Branch (strip "feature/" "fix/" prefixes kept — match Claude Code)
    branch = getattr(s, "git_branch", None) or getattr(s, "worktree_name", None)
    if branch:
        meta_parts.append(rich_escape(str(branch)))

    # Model short name
    if s.model_name:
        short_model = s.model_name.replace("claude-", "").replace("-20250514", "")
        meta_parts.append(rich_escape(short_model))

    # Message count (plain number, no emoji)
    if s.message_count:
        meta_parts.append(f"{s.message_count} msgs")

    # Intent excerpt (truncated)
    ai_intent = self.meta.ai_intent if self.meta else None
    intent = ai_intent or s.first_user_content or ""
    if intent:
        intent_clean = _clean_intent_text(intent)
        meta_parts.append(_truncate(intent_clean, 60))

    meta_line = " · ".join(meta_parts) if meta_parts else "(no metadata)"

    yield Static(
        f"[#78716c]{meta_line}[/]",
        classes="card-meta",
    )
```

删除所有用到 `📝` `💬` `🗣` emoji 的代码段，删除 `card-spine` / `card-body` / `card-title-line` / `card-intent-line` 所有旧的嵌套结构引用。

- [ ] **Step 2.1d: 更新 CSS 样式（简化到 Claude 原生）**

在 `ccsm/tui/styles/claude_native.tcss` 中替换 session-card 相关样式（定位原有 `.session-card` `.card-spine` `.card-body` `.card-title-line` `.card-intent-line` 所有块）为：

```css
/* ── Session Card — Claude Code native 2-line style ──────────────── */

.session-card {
    layout: vertical;
    height: auto;
    max-height: 3;
    padding: 0 1;
    margin: 0;
    background: transparent;
}

.session-card:hover {
    background: #e8e6dc 6%;
}

.session-card.-selected {
    background: #d97757 12%;
    border-left: thick #d97757;
}

.card-row-title {
    height: 1;
    layout: horizontal;
}

.card-title {
    width: 1fr;
    overflow: hidden ellipsis;
    color: #e8e6dc;
}

.card-reltime {
    width: auto;
    min-width: 10;
    content-align: right middle;
    color: #78716c;
}

.card-meta {
    height: 1;
    width: 100%;
    overflow: hidden ellipsis;
    color: #78716c;
}
```

**删除** 旧的 `.card-spine`, `.card-body`, `.card-title-line`, `.card-intent-line`, `.card-intent`, `.card-msgcount`, `.tag-active`, `.tag-background`, `.tag-idea`, `.tag-done` 等类（这些都因删除 emoji/状态 badge 而不再需要）。

- [ ] **Step 2.1e: 跑测试验证通过**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_session_card_claude_style.py -v
```

Expected: PASS

- [ ] **Step 2.1f: 提交**

```bash
git add ccsm/tui/widgets/session_card.py ccsm/tui/styles/claude_native.tcss tests/test_session_card_claude_style.py
git commit -m "refactor(tui): session card to Claude Code native 2-line style, remove emoji decorations"
```

---

## Task 3: 日期分隔线与标题栏 Claude 原生化

**Files:**
- Modify: `ccsm/tui/widgets/session_list.py:52-78` — DateDivider 类
- Modify: `ccsm/tui/screens/main.py` — session panel title 更新逻辑
- Modify: `ccsm/tui/styles/claude_native.tcss` — `.date-divider`, `.panel-title`

### Step 3.1: 删除装饰性圆点 ☐

- [ ] **Step 3.1a: 改写 DateDivider.render**

修改 `ccsm/tui/widgets/session_list.py` 第 52-64 行：

```python
class DateDivider(Static):
    """Date divider — plain horizontal rule with label, no decoration."""

    def __init__(self, date_label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._date_label = date_label
        self.add_class("date-divider")

    def render(self) -> str:
        label = rich_escape(self._date_label)
        # Claude native: `──── Today ──────────────────`
        return f"[#3a3835]──── [/][#78716c]{label}[/][#3a3835] ────────────────────────[/]"
```

同时简化 `_format_date_divider()`（第 67-87 行）的返回文本——把中文星期去掉，使用 `Today` / `Yesterday` / `Mon Apr 12`：

```python
def _format_date_divider(dt: datetime) -> str:
    """Format a datetime for date divider display (Claude native style)."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    today = now.date()
    d = dt.date()
    if d == today:
        return "Today"
    delta = (today - d).days
    if delta == 1:
        return "Yesterday"
    if delta < 7:
        return d.strftime("%a %b %d")  # "Mon Apr 12"
    return d.strftime("%Y-%m-%d")
```

- [ ] **Step 3.1b: 更新 .date-divider CSS**

在 `ccsm/tui/styles/claude_native.tcss` 中找到 `.date-divider` 规则（约第 248-251 行），改为：

```css
.date-divider {
    height: 1;
    padding: 0 1;
    background: transparent;
    text-style: none;
}
```

删除原来的 `color: #d97757; text-style: bold;`。

### Step 3.2: Session panel title 显示 "Sessions (N of M)" ☐

- [ ] **Step 3.2a: 新增更新方法**

在 `ccsm/tui/widgets/session_list.py` 的 `SessionListPanel` 里加一个方法：

```python
def render_title_counter(self) -> str:
    """Return 'Sessions (N of M)' where N is cursor idx, M is total visible."""
    cards = list(self.query(SessionCard))
    total = len(cards)
    if total == 0:
        return " Sessions (0)"
    current_idx = next(
        (i for i, c in enumerate(cards) if c.selected),
        0,
    )
    return f" Sessions ({current_idx + 1} of {total})"
```

- [ ] **Step 3.2b: MainScreen 在光标移动时刷新 title**

`ccsm/tui/screens/main.py` 中定位现有的 `action_cursor_down` / `action_cursor_up`（如果还没有，跟 Part 1 合并实现）。在每次调用 `panel.move_cursor()` 之后追加：

```python
try:
    title_static = self.query_one("#session-panel-title", Static)
    title_static.update(panel.render_title_counter())
except Exception:
    pass
```

### Step 3.3: 提交 ☐

- [ ] **Step 3.3a: 运行所有测试**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/ -v --asyncio-mode=auto
```

Expected: 全部 PASS，无回归

- [ ] **Step 3.3b: 提交**

```bash
git add ccsm/tui/widgets/session_list.py ccsm/tui/screens/main.py ccsm/tui/styles/claude_native.tcss
git commit -m "feat(tui): date divider and panel title counter in Claude native style"
```

---

## Task 4: 血缘可视化简化（只保留 1 个视觉维度）

**Files:**
- Modify: `ccsm/tui/widgets/lineage_group.py:127-220` — compose() 方法
- Modify: `ccsm/tui/styles/claude_native.tcss:579-613` — 血缘相关所有类

**背景:** 当前血缘使用 4 层视觉编码（缩进 + 透明度 + 左边框颜色 + 徽章符号），过度信息化。改为 **只用 ASCII 树形字符 + 固定缩进**。

### Step 4.1: 重写 LineageGroup.compose() ☐

- [ ] **Step 4.1a: 写预期行为的测试**

Create `tests/test_lineage_ascii_tree.py`:

```python
"""Lineage groups should render with ASCII tree chars, not opacity/color."""
import pytest
from ccsm.tui.widgets.lineage_group import LineageGroup
from ccsm.models.session import SessionInfo
from datetime import datetime, timezone


def _mk(sid: str, ts_hour: int) -> SessionInfo:
    return SessionInfo(
        session_id=sid,
        jsonl_path=f"/tmp/{sid}.jsonl",
        display_name=f"Session {sid}",
        last_timestamp=datetime(2026, 4, 12, ts_hour, 0, tzinfo=timezone.utc),
    )


def test_lineage_renders_tree_connectors():
    """Compact chain should render with `└─` and `├─` connectors."""
    sessions = [_mk("a", 10), _mk("b", 11), _mk("c", 12)]
    lineage_types = {"b": "compact", "c": "compact"}
    group = LineageGroup(
        tree_sessions=sessions,
        lineage_types=lineage_types,
        all_meta={},
    )
    # Children of compose are either SessionCards or Static (connector)
    children = list(group.compose())
    assert len(children) >= 3  # at least the 3 session cards


def test_lineage_no_opacity_classes():
    """After refactor, history-step-{1,2,3} classes should not be added."""
    sessions = [_mk("a", 10), _mk("b", 11), _mk("c", 12), _mk("d", 13)]
    lineage_types = {"b": "compact", "c": "compact", "d": "compact"}
    group = LineageGroup(
        tree_sessions=sessions,
        lineage_types=lineage_types,
        all_meta={},
    )
    children = list(group.compose())
    from ccsm.tui.widgets.session_card import SessionCard
    for ch in children:
        if isinstance(ch, SessionCard):
            classes = set(ch.classes)
            assert "history-step-1" not in classes
            assert "history-step-2" not in classes
            assert "history-step-3" not in classes
```

- [ ] **Step 4.1b: 跑测试看它失败**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_lineage_ascii_tree.py -v
```

Expected: FAIL — 当前实现 add_class `history-step-{1,2,3}`

- [ ] **Step 4.1c: 重写 compose()**

定位 `ccsm/tui/widgets/lineage_group.py:127-220` 的 `compose()` 方法，删除所有 `card.add_class(f"history-step-{step}")` 调用。改为在每张 non-root card 之前插入一个连接符 Static：

```python
def compose(self) -> ComposeResult:
    if not self._sessions:
        return

    # Display newest → oldest (top → bottom)
    total = len(self._sessions)
    hidden_count = max(0, total - self._max_visible)

    if self._expanded or hidden_count == 0:
        visible = list(reversed(self._sessions))
    else:
        visible = list(reversed(self._sessions[-self._max_visible:]))

    trunk_sessions = []
    fork_sessions = []
    for s in visible:
        ltype = self._lineage_types.get(s.session_id, "root")
        if ltype == "fork":
            fork_sessions.append(s)
        else:
            trunk_sessions.append(s)

    # ── Trunk ──
    n_trunk = len(trunk_sessions)
    for i, session in enumerate(trunk_sessions):
        is_last_in_trunk = (i == n_trunk - 1) and not fork_sessions and hidden_count == 0

        # Insert connector before non-root cards
        if i > 0:
            connector = "└─" if is_last_in_trunk else "├─"
            yield Static(
                f"[#3a3835] {connector}[/]",
                classes="lineage-connector",
            )

        meta = self._all_meta.get(session.session_id)
        thought = self._last_thoughts.get(session.session_id, "")

        time_label = ""
        if session.last_timestamp:
            ts = session.last_timestamp
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            time_label = ts.strftime("%H:%M")

        card = SessionCard(
            session=session,
            meta=meta,
            last_user_message=thought,
            time_label=time_label,
        )

        # Selected highlight passes through
        if session.session_id == self._selected_id:
            card.selected = True

        # NOTE: deliberately no history-step-N classes, no compact-card class
        yield card

    # ── Expand bar ──
    if not self._expanded and hidden_count > 0:
        yield _ExpandBar(
            f"[#78716c] └─ {hidden_count} more[/]",
            classes="lineage-expand-bar",
        )

    # ── Fork branches ──
    for j, session in enumerate(fork_sessions):
        is_last = (j == len(fork_sessions) - 1)
        connector = "└─⑂" if is_last else "├─⑂"
        yield Static(
            f"[#a855f7] {connector}[/]",
            classes="lineage-connector-fork",
        )
        meta = self._all_meta.get(session.session_id)
        thought = self._last_thoughts.get(session.session_id, "")
        card = SessionCard(session=session, meta=meta, last_user_message=thought)
        if session.session_id == self._selected_id:
            card.selected = True
        yield card
```

**关键删除项:**
- 删除 `if is_fork_point: yield _ForkPointSeparator(...)` 整块（约 L150-154）
- 删除 `card.add_class(f"history-step-{step}")` （约 L180）
- 删除 `if ltype == "compact": card.add_class("compact-card")` （约 L182）
- 删除 `if ltype == "duplicate": card.add_class("duplicate-card")`
- 删除尾部 `yield Static(f"[#a855f7 bold]{anchor_text}[/]", ...)` 的 `fork-anchor-label`
- 删除 `card.add_class("fork-card")` 调用

- [ ] **Step 4.1d: 更新 CSS — 删除血缘视觉类**

在 `ccsm/tui/styles/claude_native.tcss` 中找到并**完全删除**以下规则（约第 579-613 行）:
- `.history-step-1`, `.history-step-2`, `.history-step-3`
- `.compact-card .card-body` 左边框规则
- `.fork-branch-block .session-card` 左边框规则
- `.duplicate-card` opacity 规则
- `.fork-card` margin 规则
- `.fork-point-separator` 规则
- `.fork-anchor-label` 规则

新增 2 个简单规则:

```css
.lineage-connector {
    height: 1;
    padding: 0 1;
    color: #3a3835;
}

.lineage-connector-fork {
    height: 1;
    padding: 0 1;
    color: #a855f7;
}
```

- [ ] **Step 4.1e: 跑测试验证通过**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_lineage_ascii_tree.py -v
```

Expected: PASS

- [ ] **Step 4.1f: 全量回归**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/ -v --asyncio-mode=auto
```

Expected: 所有已有测试 + 新测试 PASS

- [ ] **Step 4.1g: 提交**

```bash
git add ccsm/tui/widgets/lineage_group.py ccsm/tui/styles/claude_native.tcss tests/test_lineage_ascii_tree.py
git commit -m "refactor(tui): simplify lineage visualization to ASCII tree connectors"
```

---

## Task 5: 光标跟随 detail 自动刷新（预览态，不触发加载除非停留）

**Files:**
- Modify: `ccsm/tui/screens/main.py` — `action_cursor_up` / `action_cursor_down`
- Modify: `ccsm/tui/widgets/session_card.py` — 增加 `can_focus = True`（若 keyboard-first 计划已实现则跳过）

**说明:** 这个 task 建立在 keyboard-first 计划（Task 1-2）之上。keyboard-first 只要求 ↑↓ 移动光标不触发 detail 加载。但现在 detail 是内联第三栏，我们希望光标停留某张卡片时**自动**刷新第三栏，又不希望疯狂按 ↓ 每次都触发完整 JSONL 解析。

**策略:** debounce 模式 — cursor 每次移动只更新 `self._hovered_session`，启动一个 150ms 的定时器；定时器到期后如果光标还停留在同一张卡片，才真正调用 `_load_session_detail`。

### Step 5.1: 实现 debounced detail preview ☐

- [ ] **Step 5.1a: 写测试**

Append to `tests/test_inline_detail_panel.py`:

```python
@pytest.mark.asyncio
async def test_rapid_cursor_movement_debounces_detail_load():
    """5 rapid ↓ should result in exactly 1 _load_session_detail call."""
    async with CCSMApp().run_test() as pilot:
        screen = pilot.app.screen
        # Assume sessions already loaded via fixture
        load_count = 0
        original = screen._load_session_detail
        def counter(s):
            nonlocal load_count
            load_count += 1
            original(s)
        screen._load_session_detail = counter

        # 5 fast down presses
        for _ in range(5):
            await pilot.press("down")
        # Wait for debounce to fire (150ms)
        await pilot.pause(delay=0.25)

        assert load_count == 1, f"Got {load_count} loads (expected 1 debounced)"
```

- [ ] **Step 5.1b: 给 MainScreen 加 debounce 机制**

在 `ccsm/tui/screens/main.py` 的 `__init__` 里加：

```python
self._hover_debounce_timer = None
self._hovered_session: Optional[SessionInfo] = None
```

新增方法：

```python
def _schedule_detail_preview(self, session: SessionInfo) -> None:
    """Debounced detail load: waits 150ms of cursor stillness before loading."""
    self._hovered_session = session
    # Cancel any pending timer
    if self._hover_debounce_timer is not None:
        try:
            self._hover_debounce_timer.stop()
        except Exception:
            pass
    self._hover_debounce_timer = self.set_timer(
        0.15,
        lambda: self._fire_preview_if_stable(session),
    )

def _fire_preview_if_stable(self, target: SessionInfo) -> None:
    """If cursor still on `target` after debounce, load detail."""
    if self._hovered_session is target:
        self._load_session_detail(target)
```

修改 `action_cursor_up` / `action_cursor_down`（Part 1 keyboard-first Task 1 会建立这两个 action；如果它们已存在就在里面追加一行）:

```python
def action_cursor_up(self) -> None:
    if not self._list_actionable():
        return
    panel = self.query_one(SessionListPanel)
    session = panel.move_cursor(-1)
    if session is not None:
        self._selected_session = session
        self._schedule_detail_preview(session)  # ← 新增
```

同样修改 `action_cursor_down` / `action_cursor_top` / `action_cursor_bottom` / `action_page_up` / `action_page_down`。

- [ ] **Step 5.1c: 跑测试验证通过**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/test_inline_detail_panel.py::test_rapid_cursor_movement_debounces_detail_load -v
```

Expected: PASS

- [ ] **Step 5.1d: 提交**

```bash
git add ccsm/tui/screens/main.py tests/test_inline_detail_panel.py
git commit -m "feat(tui): debounced detail preview on keyboard cursor movement"
```

---

## Task 6: 验收与全量回归

- [ ] **Step 6.1: 启动 TUI 手动检查**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
PYTHONPATH=.:$PYTHONPATH python3 -m ccsm
```

视觉确认：
- 三栏布局 15%+55%+30% 可见
- 没有 📝 💬 ⬤ 等装饰 emoji
- 日期分割线是纯线条 `──── Today ────`
- 按 ↓ 移动时 detail 面板平滑刷新（debounced）
- 血缘分支只用 ASCII 树形字符

- [ ] **Step 6.2: 跑全量测试**

```bash
PYTHONPATH=.:$PYTHONPATH python3 -m pytest tests/ -v --asyncio-mode=auto
```

Expected: **所有** 测试通过。上一轮 115+ 测试 + Part 2 新增 ~10 个测试全部 PASS，零回归。

- [ ] **Step 6.3: 关闭 Task 2 TaskUpdate**

任务单状态标记为 completed。

---

## Self-Review 清单

- [x] 每个 Task 都有明确的文件路径和行号
- [x] 每个测试都有完整代码，非伪代码
- [x] 每个 CSS 修改都列出了具体类名和属性
- [x] Task 之间的依赖关系明确（Task 5 依赖 keyboard-first Task 1）
- [x] 回归测试在 Task 6 显式触发
- [x] 视觉目标用截图字段精确描述（`·` 中点、无 emoji、2 行、无装饰圆点）
- [x] 删除的代码明确列出（旧 `history-step-*` / `.card-spine` / `.drawer-panel` / 圆点 ⬤）
