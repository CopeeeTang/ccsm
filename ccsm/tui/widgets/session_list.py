"""Middle panel: Session list widget with dual view mode.

Supports two view modes (toggled by 'g' key):
1. LIST mode: Session cards with inline status tags, filterable by status
2. SWIMLANE mode: Workflow timeline visualization

Filter bar at top: ALL | 🟢Active | 🔵Back | 🟣Idea | ⚪Done
Cards/lanes below show filtered content.
Date dividers inserted between sessions on different days.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, Optional

from rich.cells import cell_len
from rich.markup import escape as rich_escape

from textual.containers import VerticalScroll
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from ccsm.models.session import (
    Priority,
    SessionInfo,
    SessionMeta,
    Status,
    Workflow,
    WorkflowCluster,
)
from ccsm.tui.widgets.session_card import SessionCard
from ccsm.tui.widgets.swimlane import Swimlane

# Display order for status tabs (with ALL prepended)
_STATUS_ORDER = [Status.ACTIVE, Status.BACKGROUND, Status.IDEA, Status.DONE]

_TAB_LABELS = {
    Status.ACTIVE: "Active",
    Status.BACKGROUND: "Back",
    Status.IDEA: "Idea",
    Status.DONE: "Done",
}

_TAB_ICONS = {
    Status.ACTIVE: "🟢",
    Status.BACKGROUND: "🔵",
    Status.IDEA: "🟣",
    Status.DONE: "⚪",
}

# Status sort priority (lower = shown first in mixed list)
_STATUS_SORT_RANK = {
    Status.ACTIVE: 0,
    Status.BACKGROUND: 1,
    Status.IDEA: 2,
    Status.DONE: 3,
    Status.NOISE: 4,
}


class DateDivider(Static):
    """Date divider inserted between sessions on different days.

    Visual: ╭─── ⬤ 今天 (2026-04-03) ───╮
    """

    def __init__(self, date_label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._date_label = date_label

    def render(self) -> str:
        label = self._date_label
        return f"[#d97757]──── ⬤ {rich_escape(label)} ────[/]"


def _format_date_divider(dt: datetime) -> str:
    """Format a datetime for date divider display.

    Returns: '今天 (04-03)', '昨天 (04-02)', '2026-04-01 周二'
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    today = now.date()
    d = dt.date()
    day_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]

    if d == today:
        return f"今天 ({d.strftime('%m-%d')})"
    delta = (today - d).days
    if delta == 1:
        return f"昨天 ({d.strftime('%m-%d')})"
    if delta < 7:
        weekday = day_names[d.weekday()]
        return f"{d.strftime('%m-%d')} {weekday}"
    return f"{d.strftime('%Y-%m-%d')} {day_names[d.weekday()]}"


class FilterBar(Static):
    """Horizontal filter bar with ALL + per-status chips."""

    class FilterChanged(Message):
        """Emitted when the user clicks a different filter."""

        def __init__(self, status: Optional[Status]) -> None:
            """status=None means ALL."""
            self.status = status
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._active_filter: Optional[Status] = None  # None = ALL
        self._counts: dict[Status, int] = {s: 0 for s in _STATUS_ORDER}
        self._view_mode: Literal["list", "swimlane"] = "list"

    def update_state(
        self,
        counts: dict[Status, int],
        active_filter: Optional[Status],
        view_mode: Literal["list", "swimlane"] = "list",
    ) -> None:
        """Update filter counts, active filter, and view mode indicator."""
        self._counts = counts
        self._active_filter = active_filter
        self._view_mode = view_mode
        self.update(self._render_bar())

    def _render_bar(self) -> str:
        """Render horizontal filter bar as Rich markup."""
        parts = []
        total = sum(self._counts.values())

        # ALL chip
        if self._active_filter is None:
            parts.append(f"[bold #d97757]\\[ ALL {total} \\][/]")
        else:
            parts.append(f"[#78716c]ALL {total}[/]")

        # Per-status chips
        for status in _STATUS_ORDER:
            icon = _TAB_ICONS.get(status, "?")
            label = _TAB_LABELS.get(status, "?")
            count = self._counts.get(status, 0)
            if status == self._active_filter:
                parts.append(f"[bold #d97757]\\[ {icon}{label} {count} \\][/]")
            else:
                parts.append(f"[#78716c]{icon}{label} {count}[/]")

        # View mode indicator
        if self._view_mode == "swimlane":
            mode_indicator = "[#a78bfa]⫍[/]"
        else:
            mode_indicator = "[#78716c]≡[/]"

        return f"{mode_indicator} " + "  ".join(parts)

    def on_click(self, event) -> None:
        """Handle click on filter bar — map x to filter chip."""
        if self.size.width == 0:
            return
        x = event.x

        # Skip mode indicator (icon ~2 cols + space)
        offset = cell_len("⫍") + 2  # icon + " "

        # ALL chip
        total = sum(self._counts.values())
        all_visible = f"ALL {total}"
        if self._active_filter is None:
            all_width = cell_len(f"[ {all_visible} ]")
        else:
            all_width = cell_len(all_visible)

        if x < offset + all_width:
            if self._active_filter is not None:
                self._active_filter = None
                self.update(self._render_bar())
                self.post_message(self.FilterChanged(None))
            return

        offset += all_width + 2  # separator

        # Per-status chips
        for status in _STATUS_ORDER:
            icon = _TAB_ICONS.get(status, "?")
            label = _TAB_LABELS.get(status, "?")
            count = self._counts.get(status, 0)
            if status == self._active_filter:
                chip_width = cell_len(f"[ {icon}{label} {count} ]")
            else:
                chip_width = cell_len(f"{icon}{label} {count}")

            if x < offset + chip_width:
                if status != self._active_filter:
                    self._active_filter = status
                    self.update(self._render_bar())
                    self.post_message(self.FilterChanged(status))
                return

            offset += chip_width + 2  # separator


class SessionListPanel(VerticalScroll):
    """Scrollable panel showing session cards or swimlane, filtered by status."""

    class SessionSelected(Message):
        """Bubbled when a session card is selected."""

        def __init__(self, session: SessionInfo) -> None:
            self.session = session
            super().__init__()

    class WorkflowSelected(Message):
        """Bubbled when a workflow is selected in swimlane mode."""

        def __init__(self, workflow: Workflow) -> None:
            self.workflow = workflow
            super().__init__()

    class ViewModeChanged(Message):
        """Bubbled when view mode changes."""

        def __init__(self, mode: Literal["list", "swimlane"]) -> None:
            self.mode = mode
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(id="session-list-container", **kwargs)
        self._sessions: list[SessionInfo] = []
        self._all_meta: dict[str, SessionMeta] = {}
        self._last_thoughts: dict[str, str] = {}
        self._lineage_types: dict[str, str] = {}
        self._show_noise: bool = False
        self._selected_id: str | None = None
        self._active_filter: Optional[Status] = None  # None = ALL
        self._view_mode: Literal["list", "swimlane"] = "list"
        self._filter_bar: FilterBar | None = None
        self._workflow_cluster: Optional[WorkflowCluster] = None

    def load_sessions(
        self,
        sessions: list[SessionInfo],
        all_meta: dict[str, SessionMeta] | None = None,
        last_thoughts: dict[str, str] | None = None,
        lineage_types: dict[str, str] | None = None,
        workflow_cluster: Optional[WorkflowCluster] = None,
    ) -> None:
        """Replace displayed sessions with new data."""
        self._sessions = sessions
        self._all_meta = all_meta or {}
        self._last_thoughts = last_thoughts or {}
        self._lineage_types = lineage_types or {}
        self._workflow_cluster = workflow_cluster
        self._rebuild()

    def toggle_view_mode(self) -> None:
        """Toggle between list and swimlane view modes."""
        if self._view_mode == "list":
            self._view_mode = "swimlane"
        else:
            self._view_mode = "list"
        self.post_message(self.ViewModeChanged(self._view_mode))
        self._rebuild()
        # When switching views, keep viewport at the top to avoid landing
        # on blank space if the previous view was scrolled deeper.
        self.scroll_home(animate=False)

    def set_active_tab(self, status: Status) -> None:
        """Switch to a specific status filter (called by keyboard shortcuts)."""
        if status in _STATUS_ORDER:
            self._active_filter = status
            self._rebuild()

    def set_filter_all(self) -> None:
        """Switch to ALL filter (show all statuses)."""
        self._active_filter = None
        self._rebuild()

    def _count_by_status(self) -> dict[Status, int]:
        """Count sessions per status (excluding NOISE unless toggled)."""
        counts: dict[Status, int] = {s: 0 for s in _STATUS_ORDER}
        for session in self._sessions:
            if session.status in counts:
                counts[session.status] += 1
        return counts

    def _rebuild(self) -> None:
        """Clear and rebuild: filter bar + content (cards or swimlane)."""
        self.remove_children()

        counts = self._count_by_status()

        # Mount filter bar
        self._filter_bar = FilterBar(classes="status-tab-bar")
        self.mount(self._filter_bar)
        self._filter_bar.update_state(counts, self._active_filter, self._view_mode)

        # Render based on view mode
        if self._view_mode == "swimlane":
            self._rebuild_swimlane()
        else:
            self._rebuild_list()

    def _build_spine_data(self, filtered: list[SessionInfo]) -> list[dict]:
        """Generate spine graph prefixes for each session card.

        Returns a list of dicts with 'time' and 'graph' keys.
        The spine creates a visual timeline with connectors.
        """
        from datetime import timezone

        spine_data = []
        for i, session in enumerate(filtered):
            lineage = self._lineage_types.get(session.session_id)
            is_last = (i == len(filtered) - 1)

            # Time label (HH:MM format from last_timestamp, local time)
            time_label = ""
            if session.last_timestamp:
                ts = session.last_timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                time_label = ts.strftime("%H:%M")

            # Spine graph character
            if lineage == "fork":
                graph = "┣━▶"
            elif is_last:
                graph = "┗━●"
            else:
                graph = "┃ ●"

            spine_data.append({
                "time": time_label,
                "graph": graph,
            })
        return spine_data

    def _rebuild_list(self) -> None:
        """Render session cards (list view) with spine timeline."""
        # Filter sessions by active filter
        if self._active_filter is not None:
            filtered = [
                s for s in self._sessions
                if s.status == self._active_filter
            ]
        else:
            # ALL: show everything except NOISE (unless toggled)
            filtered = [
                s for s in self._sessions
                if s.status != Status.NOISE or self._show_noise
            ]

        # Sort: running first → status rank → last_timestamp desc
        def _sort_key(s: SessionInfo) -> tuple:
            ts = s.last_timestamp
            ts_val = ts.timestamp() if ts else 0
            status_rank = _STATUS_SORT_RANK.get(s.status, 99)
            return (-int(s.is_running), status_rank, -ts_val)

        filtered.sort(key=_sort_key)

        if not filtered:
            label = self._active_filter.value if self._active_filter else "matching"
            self.mount(
                Static(
                    f"  No {label} sessions",
                    classes="empty-state",
                )
            )
            return

        # Generate spine timeline data
        spine_data = self._build_spine_data(filtered)

        # Track current date for date dividers
        prev_date = None

        for i, session in enumerate(filtered):
            # ── Insert DateDivider when crossing day boundary ──
            if session.last_timestamp:
                ts = session.last_timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                session_date = ts.date()
            else:
                session_date = None

            if session_date and session_date != prev_date:
                label = _format_date_divider(session.last_timestamp)
                self.mount(DateDivider(label, classes="date-divider"))
                prev_date = session_date
            meta = self._all_meta.get(session.session_id)
            thought = self._last_thoughts.get(session.session_id, "")
            lineage_type = self._lineage_types.get(session.session_id)
            spine = spine_data[i] if i < len(spine_data) else {}
            card = SessionCard(
                session, meta=meta, last_thought=thought,
                lineage_type=lineage_type,
                spine_time=spine.get("time", ""),
                spine_graph=spine.get("graph", ""),
            )
            if session.session_id == self._selected_id:
                card.selected = True
            self.mount(card)

    def _rebuild_swimlane(self) -> None:
        """Render swimlane timeline (swimlane view) with interactive widget."""
        if not self._workflow_cluster:
            self.mount(
                Static(
                    "  [#78716c italic]No workflow data — select a worktree first[/]",
                    classes="empty-state",
                )
            )
            return

        session_statuses = {s.session_id: s.status for s in self._sessions}

        widget = Swimlane()
        self.mount(widget)
        widget.set_data(
            self._workflow_cluster,
            statuses=session_statuses,
            current_session_id=self._selected_id,
            compact=True,
        )

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

    def on_filter_bar_filter_changed(self, event: FilterBar.FilterChanged) -> None:
        """Handle filter bar click — switch to new filter."""
        self._active_filter = event.status
        self._rebuild()

    def on_swimlane_workflow_selected(
        self, event: Swimlane.WorkflowSelected
    ) -> None:
        """Handle swimlane workflow click — bubble up."""
        self.post_message(self.WorkflowSelected(event.workflow))
