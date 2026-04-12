"""Middle panel: Session list widget with lineage tree grouping.

Session cards with inline status tags, filterable by status.
Lineage-related sessions (compact/fork/duplicate) are grouped
into collapsible trees via LineageGroup.

Filter bar at top: ALL | 🟢Active | 🔵Back | 🟣Idea | ⚪Done
Date dividers inserted between sessions on different days.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

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
    WorkflowCluster,
)
from ccsm.tui.widgets.session_card import SessionCard
from ccsm.tui.widgets.lineage_group import LineageGroup

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


def _build_lineage_trees(
    sessions: list[SessionInfo],
    lineage_types: dict[str, str],
    lineage_graph: dict | None = None,
) -> list[list[SessionInfo]]:
    """Group sessions into lineage trees using parent-child graph.

    Uses build_lineage_graph's parent/child relationships to cluster
    related sessions (compact continuations, fork branches, duplicates)
    into the same tree. Sessions without graph data remain standalone.

    Returns list of trees (each a list of SessionInfo).
    Trees sorted by latest timestamp descending.
    Sessions within tree sorted by timestamp ascending.
    """
    sid_to_session = {s.session_id: s for s in sessions}
    assigned: set[str] = set()
    raw_trees: list[list[SessionInfo]] = []

    if lineage_graph:
        # Find root nodes: nodes with no parent (or parent not in current set)
        roots = []
        for sid, node in lineage_graph.items():
            if sid not in sid_to_session:
                continue  # filtered out
            if node.parent_id is None or node.parent_id not in sid_to_session:
                roots.append(sid)

        # BFS from each root to collect tree members
        for root_sid in roots:
            if root_sid in assigned:
                continue
            tree_sids: list[str] = []
            queue = [root_sid]
            while queue:
                current = queue.pop(0)
                if current in assigned or current not in sid_to_session:
                    continue
                assigned.add(current)
                tree_sids.append(current)
                # Add children
                node = lineage_graph.get(current)
                if node and node.children:
                    for child_sid in node.children:
                        if child_sid not in assigned and child_sid in sid_to_session:
                            queue.append(child_sid)

            if tree_sids:
                tree_sessions = [sid_to_session[sid] for sid in tree_sids]
                raw_trees.append(tree_sessions)

    # Add any unassigned sessions as standalone trees
    for s in sessions:
        if s.session_id not in assigned:
            raw_trees.append([s])

    # Sort within each tree by timestamp ascending
    for tree in raw_trees:
        tree.sort(
            key=lambda s: (s.last_timestamp or datetime.min.replace(tzinfo=timezone.utc)).timestamp()
        )

    # Sort trees by latest timestamp descending
    def tree_max_ts(tree: list[SessionInfo]) -> float:
        timestamps = [
            s.last_timestamp.timestamp()
            for s in tree if s.last_timestamp
        ]
        return max(timestamps) if timestamps else 0

    raw_trees.sort(key=tree_max_ts, reverse=True)
    return raw_trees


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

    def update_state(
        self,
        counts: dict[Status, int],
        active_filter: Optional[Status],
    ) -> None:
        """Update filter counts and active filter."""
        self._counts = counts
        self._active_filter = active_filter
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

        return "  ".join(parts)

    def on_click(self, event) -> None:
        """Handle click on filter bar — map x to filter chip."""
        if self.size.width == 0:
            return
        x = event.x

        # ALL chip
        offset = 0
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
    """Scrollable panel showing session cards filtered by status."""

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
        self._lineage_types: dict[str, str] = {}
        self._lineage_graph: dict = {}  # session_id → SessionLineage
        self._show_noise: bool = False
        self._selected_id: str | None = None
        self._active_filter: Optional[Status] = None  # None = ALL
        self._filter_bar: FilterBar | None = None

    def load_sessions(
        self,
        sessions: list[SessionInfo],
        all_meta: dict[str, SessionMeta] | None = None,
        last_thoughts: dict[str, str] | None = None,
        lineage_types: dict[str, str] | None = None,
        lineage_graph: dict | None = None,
    ) -> None:
        """Replace displayed sessions with new data."""
        self._sessions = sessions
        self._all_meta = all_meta or {}
        self._last_thoughts = last_thoughts or {}
        self._lineage_types = lineage_types or {}
        self._lineage_graph = lineage_graph or {}
        self._rebuild()

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
        """Clear and rebuild: filter bar + session cards."""
        self.remove_children()

        counts = self._count_by_status()

        # Mount filter bar
        self._filter_bar = FilterBar(classes="status-tab-bar")
        self.mount(self._filter_bar)
        self._filter_bar.update_state(counts, self._active_filter)

        self._rebuild_list()

    def _pass_filter(self, session: SessionInfo) -> bool:
        """True if this session matches the current filter."""
        if self._active_filter is not None:
            return session.status == self._active_filter
        # ALL filter: include unless NOISE (unless show_noise toggled)
        if session.status == Status.NOISE and not self._show_noise:
            return False
        return True

    def _rebuild_list(self) -> None:
        """Render session cards grouped by lineage tree.

        Strategy: Build lineage trees from the FULL session set
        (so parent/child edges across filters are preserved), then
        mask individual cards visible/hidden based on current filter.
        This prevents tree fragmentation when switching status tabs.
        """
        # Compute filtered set for visibility masking
        filtered_ids = {
            s.session_id for s in self._sessions
            if self._pass_filter(s)
        }

        if not filtered_ids:
            label = self._active_filter.value if self._active_filter else "matching"
            self.mount(
                Static(
                    f"  No {label} sessions",
                    classes="empty-state",
                )
            )
            return

        # Identify Fork Points (sessions with at least one child of type 'fork')
        fork_parents: set[str] = set()
        if self._lineage_graph:
            for sid, node in self._lineage_graph.items():
                if node.children:
                    for child_sid in node.children:
                        if self._lineage_types.get(child_sid) == "fork":
                            fork_parents.add(sid)
                            break

        # Build lineage trees from FULL session set — not filtered!
        # This preserves tree topology across filter switches.
        all_visible = [
            s for s in self._sessions
            if s.status != Status.NOISE or self._show_noise
        ]
        trees = _build_lineage_trees(all_visible, self._lineage_types, self._lineage_graph)

        # Sort visible sessions: running first → last_timestamp desc
        def _sort_key(s: SessionInfo) -> tuple:
            ts = s.last_timestamp
            ts_val = ts.timestamp() if ts else 0
            return (-int(s.is_running), -ts_val)

        # Track dates for date dividers
        prev_date = None

        for tree in trees:
            # Skip trees where NO session passes the current filter
            if not any(s.session_id in filtered_ids for s in tree):
                continue

            # Filter the tree to only include visible members for card rendering
            visible_in_tree = [s for s in tree if s.session_id in filtered_ids]

            # Use the newest visible session's date for the divider
            visible_sorted = sorted(
                visible_in_tree,
                key=lambda s: (s.last_timestamp or datetime.min.replace(tzinfo=timezone.utc)).timestamp(),
            )
            newest = visible_sorted[-1]
            if newest.last_timestamp:
                ts = newest.last_timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                tree_date = ts.date()
            else:
                tree_date = None

            if tree_date and tree_date != prev_date:
                label = _format_date_divider(newest.last_timestamp)
                self.mount(DateDivider(label, classes="date-divider"))
                prev_date = tree_date

            # Single visible session in tree: render as plain card
            if len(visible_in_tree) == 1 and len(tree) == 1:
                s = visible_in_tree[0]
                meta = self._all_meta.get(s.session_id)
                thought = self._last_thoughts.get(s.session_id, "")
                ltype = self._lineage_types.get(s.session_id)

                time_label = ""
                if s.last_timestamp:
                    t = s.last_timestamp
                    if t.tzinfo is None:
                        t = t.replace(tzinfo=timezone.utc)
                    time_label = t.strftime("%H:%M")

                card = SessionCard(
                    s, meta=meta, last_thought=thought,
                    lineage_type=ltype,
                    is_fork_point=(s.session_id in fork_parents),
                    spine_time=time_label,
                    spine_graph="━●",
                )
                if s.session_id == self._selected_id:
                    card.selected = True
                self.mount(card)
            else:
                # Multi-session tree: use LineageGroup with visible_ids mask
                group = LineageGroup(
                    tree_sessions=tree,
                    lineage_types=self._lineage_types,
                    all_meta=self._all_meta,
                    last_thoughts=self._last_thoughts,
                    fork_parents=fork_parents,
                    selected_id=self._selected_id,
                    visible_ids=filtered_ids,
                )
                self.mount(group)

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

    # ── Keyboard-first navigation ──────────────────────────────────────

    def move_cursor(self, delta: int) -> "SessionInfo | None":
        """Move keyboard cursor by delta. Returns new target or None."""
        cards = list(self.query(SessionCard))
        if not cards:
            return None
        current_idx = next(
            (i for i, c in enumerate(cards) if c.selected), -1,
        )
        if current_idx == -1:
            new_idx = 0 if delta > 0 else len(cards) - 1
        else:
            new_idx = max(0, min(len(cards) - 1, current_idx + delta))
        if new_idx == current_idx:
            return None
        for i, c in enumerate(cards):
            c.selected = (i == new_idx)
        target_card = cards[new_idx]
        target_card.scroll_visible(animate=False)
        return target_card.session

    def move_cursor_to(self, position: str) -> "SessionInfo | None":
        """Move cursor to 'top' or 'bottom'."""
        cards = list(self.query(SessionCard))
        if not cards:
            return None
        target = cards[0] if position == "top" else cards[-1]
        for c in cards:
            c.selected = (c is target)
        target.scroll_visible(animate=False)
        return target.session

    def move_cursor_page(self, direction: int) -> "SessionInfo | None":
        """Move cursor by one page. direction: -1 = up, +1 = down."""
        visible_count = max(1, self.size.height // 5)
        return self.move_cursor(direction * visible_count)

    def confirm_selection(self) -> "SessionInfo | None":
        """Called by Enter key — emit SessionSelected for the cursored card."""
        cards = list(self.query(SessionCard))
        selected_card = next((c for c in cards if c.selected), None)
        if selected_card is None and cards:
            selected_card = cards[0]
            selected_card.selected = True
        if selected_card is None:
            return None
        self.post_message(self.SessionSelected(selected_card.session))
        return selected_card.session

    def render_title_counter(self) -> str:
        """Return 'Sessions (N of M)' string for the panel title."""
        cards = list(self.query(SessionCard))
        total = len(cards)
        if total == 0:
            return " Sessions (0)"
        current_idx = next(
            (i for i, c in enumerate(cards) if c.selected), 0,
        )
        return f" Sessions ({current_idx + 1} of {total})"
