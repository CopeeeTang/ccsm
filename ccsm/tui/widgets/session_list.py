"""Middle panel: Session list widget.

Displays sessions with status tab filtering.
Tab bar at top: ACTIVE | BACK | IDEA | DONE
Cards below show only the selected status group.
"""

from __future__ import annotations

from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from ccsm.models.session import (
    Priority,
    SessionInfo,
    SessionMeta,
    Status,
)
from ccsm.tui.widgets.session_card import SessionCard

# Display order for status tabs
_STATUS_ORDER = [Status.ACTIVE, Status.BACKGROUND, Status.IDEA, Status.DONE]

_STATUS_LABELS = {
    Status.ACTIVE: "🟢 ACTIVE",
    Status.BACKGROUND: "🔵 BACK",
    Status.IDEA: "🟣 IDEA",
    Status.DONE: "⚪ DONE",
    Status.NOISE: "⬛ NOISE",
}

_TAB_LABELS = {
    Status.ACTIVE: "ACTIVE",
    Status.BACKGROUND: "BACK",
    Status.IDEA: "IDEA",
    Status.DONE: "DONE",
}

_TAB_ICONS = {
    Status.ACTIVE: "🟢",
    Status.BACKGROUND: "🔵",
    Status.IDEA: "🟣",
    Status.DONE: "⚪",
}


class StatusTabBar(Static):
    """Horizontal tab bar for switching between status groups."""

    class TabChanged(Message):
        """Emitted when the user clicks a different tab."""

        def __init__(self, status: Status) -> None:
            self.status = status
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active: Status = Status.ACTIVE
        self._counts: dict[Status, int] = {s: 0 for s in _STATUS_ORDER}

    def update_counts(self, counts: dict[Status, int], active: Status) -> None:
        """Update tab counts and active tab, then re-render."""
        self._counts = counts
        self._active = active
        self.update(self._render_tabs())

    def _render_tabs(self) -> str:
        """Render horizontal tab bar as Rich markup."""
        parts = []
        for status in _STATUS_ORDER:
            icon = _TAB_ICONS.get(status, "?")
            label = _TAB_LABELS.get(status, "?")
            count = self._counts.get(status, 0)
            if status == self._active:
                # 选中 tab：橙色方括号高亮
                parts.append(f"[bold #fb923c][ {icon} {label} {count} ][/]")
            else:
                # 未选中 tab：灰色淡显
                parts.append(f"[#78716c]{icon} {label} {count}[/]")
        return "  ".join(parts)

    def on_click(self, event) -> None:
        """Handle click on tab bar — map x coordinate to tab using actual rendered widths."""
        if self.size.width == 0:
            return
        x = event.x

        # Reconstruct each tab's visible text to compute actual column widths
        separator_width = 2  # "  " between tabs
        offset = 1  # left padding from CSS
        new_status = _STATUS_ORDER[-1]  # fallback to last tab

        for status in _STATUS_ORDER:
            icon = _TAB_ICONS.get(status, "?")
            label = _TAB_LABELS.get(status, "?")
            count = self._counts.get(status, 0)
            if status == self._active:
                rendered = f"[ {icon} {label} {count} ]"
            else:
                rendered = f"{icon} {label} {count}"
            # Approximate visible width (emoji ~2 cols, rest ~1 col each)
            tab_width = len(rendered) + 1  # +1 for emoji extra width
            if x < offset + tab_width:
                new_status = status
                break
            offset += tab_width + separator_width

        if new_status != self._active:
            self._active = new_status
            self.update(self._render_tabs())
            self.post_message(self.TabChanged(new_status))


class SessionListPanel(VerticalScroll):
    """Scrollable panel showing session cards filtered by status tab."""

    class SessionSelected(Message):
        """Bubbled when a session card is selected."""

        def __init__(self, session: SessionInfo) -> None:
            self.session = session
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(id="session-list-container", **kwargs)
        self._sessions: list[SessionInfo] = []
        self._all_meta: dict[str, SessionMeta] = {}
        self._last_thoughts: dict[str, str] = {}
        self._lineage_types: dict[str, str] = {}  # session_id → "fork"/"compact"/"duplicate"
        self._show_noise: bool = False
        self._selected_id: str | None = None
        self._active_tab: Status = Status.ACTIVE
        self._tab_bar: StatusTabBar | None = None

    def load_sessions(
        self,
        sessions: list[SessionInfo],
        all_meta: dict[str, SessionMeta] | None = None,
        last_thoughts: dict[str, str] | None = None,
        lineage_types: dict[str, str] | None = None,
    ) -> None:
        """Replace displayed sessions with new data."""
        self._sessions = sessions
        self._all_meta = all_meta or {}
        self._last_thoughts = last_thoughts or {}
        self._lineage_types = lineage_types or {}
        # 自动选择第一个有数据的 tab
        self._auto_select_tab()
        self._rebuild()

    def _auto_select_tab(self) -> None:
        """Auto-select the first tab that has sessions."""
        counts = self._count_by_status()
        # 当前 tab 有数据则保持不动
        if counts.get(self._active_tab, 0) > 0:
            return
        # 否则找第一个非空 tab
        for status in _STATUS_ORDER:
            if counts.get(status, 0) > 0:
                self._active_tab = status
                return

    def _count_by_status(self) -> dict[Status, int]:
        """Count sessions per status (excluding NOISE)."""
        counts: dict[Status, int] = {s: 0 for s in _STATUS_ORDER}
        for session in self._sessions:
            if session.status in counts:
                counts[session.status] += 1
        return counts

    def set_active_tab(self, status: Status) -> None:
        """Switch to a specific status tab (called by keyboard shortcuts)."""
        if status in _STATUS_ORDER:
            self._active_tab = status
            self._rebuild()

    def _rebuild(self) -> None:
        """Clear and rebuild: tab bar + filtered session cards."""
        self.remove_children()

        counts = self._count_by_status()

        # 顶部挂载 Tab Bar
        self._tab_bar = StatusTabBar(classes="status-tab-bar")
        self.mount(self._tab_bar)
        self._tab_bar.update_counts(counts, self._active_tab)

        # 过滤出当前 tab 的会话
        filtered = [
            s for s in self._sessions
            if s.status == self._active_tab
        ]

        # 排序：运行中优先，再按 last_timestamp 降序
        def _sort_key(s: SessionInfo) -> tuple:
            ts = s.last_timestamp
            ts_val = ts.timestamp() if ts else 0
            return (-int(s.is_running), -ts_val)

        filtered.sort(key=_sort_key)

        if not filtered:
            self.mount(
                Static(
                    f"  No {self._active_tab.value} sessions",
                    classes="empty-state",
                )
            )
            return

        for session in filtered:
            meta = self._all_meta.get(session.session_id)
            thought = self._last_thoughts.get(session.session_id, "")
            lineage_type = self._lineage_types.get(session.session_id)
            card = SessionCard(
                session, meta=meta, last_thought=thought,
                lineage_type=lineage_type,
            )
            if session.session_id == self._selected_id:
                card.selected = True
            self.mount(card)

    def toggle_noise(self) -> None:
        """Toggle visibility of NOISE sessions."""
        self._show_noise = not self._show_noise
        self._rebuild()

    def select_session(self, session_id: str) -> None:
        """Highlight a session card by ID."""
        self._selected_id = session_id
        for child in self.query(SessionCard):
            child.selected = child.session.session_id == session_id

    def on_session_card_card_selected(self, event: SessionCard.CardSelected) -> None:
        """Handle card click — select and bubble up."""
        self.select_session(event.session.session_id)
        self.post_message(self.SessionSelected(event.session))

    def on_status_tab_bar_tab_changed(self, event: StatusTabBar.TabChanged) -> None:
        """Handle tab bar click — switch to new tab."""
        self._active_tab = event.status
        self._rebuild()
