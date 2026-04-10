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


# ── Batch rendering constants ────────────────────────────────────────────

INITIAL_BATCH_SIZE = 30   # Cards rendered on first load
LAZY_BATCH_SIZE = 20      # Cards appended per scroll-to-bottom event


def _prepare_render_batches(
    sessions: list,
    initial_size: int = INITIAL_BATCH_SIZE,
) -> tuple[list, list]:
    """Split a session list into initial + remaining batches.

    The initial batch is rendered immediately; remaining batches
    are appended lazily when the user scrolls to the bottom.

    Args:
        sessions: Full sorted list of sessions to display.
        initial_size: Number of sessions in the first batch.

    Returns:
        (initial_batch, remaining) — both preserving original order.
    """
    return sessions[:initial_size], sessions[initial_size:]


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
        # Batch rendering state
        self._pending_trees: list[list[SessionInfo]] = []
        self._rendered_count: int = 0
        self._is_loading_more: bool = False
        self._fork_parents: set[str] = set()

    def _ordered_cards(self) -> list[SessionCard]:
        """Return rendered cards in the same order as the UI."""
        return list(self.query(SessionCard))

    def _select_card_at(self, index: int) -> None:
        """Select card at a rendered index and keep it visible."""
        cards = self._ordered_cards()
        if not cards:
            self._selected_id = None
            return

        index = max(0, min(index, len(cards) - 1))
        card = cards[index]
        self.select_session(card.session.session_id)

        try:
            self.scroll_to_widget(card, animate=False)
        except Exception:
            pass

    def _move_selection(self, delta: int) -> None:
        """Move the keyboard selection by one rendered card."""
        cards = self._ordered_cards()
        if not cards:
            return

        if self._selected_id is None:
            index = 0 if delta >= 0 else len(cards) - 1
            self._select_card_at(index)
            return

        current_index = next(
            (
                i for i, card in enumerate(cards)
                if card.session.session_id == self._selected_id
            ),
            None,
        )
        if current_index is None:
            self._select_card_at(0)
            return

        next_index = current_index + delta
        if next_index >= len(cards) and self._pending_trees:
            self._load_next_batch()
            cards = self._ordered_cards()

        self._select_card_at(next_index)

    def _open_selected_session(self) -> None:
        """Bubble the currently selected session upward."""
        if self._selected_id is None:
            self._move_selection(1)
        if self._selected_id is None:
            return

        for card in self._ordered_cards():
            if card.session.session_id == self._selected_id:
                self.post_message(self.SessionSelected(card.session))
                return

    def on_focus(self) -> None:
        """Ensure keyboard users always land on a concrete selection."""
        if self._selected_id is None and self._ordered_cards():
            self._select_card_at(0)

    def key_down(self) -> None:
        """Select the next rendered session card."""
        self._move_selection(1)

    def key_up(self) -> None:
        """Select the previous rendered session card."""
        self._move_selection(-1)

    def key_enter(self) -> None:
        """Open the currently selected session."""
        self._open_selected_session()

    def show_loading(self, count: int = 8) -> None:
        """Show skeleton placeholder cards while data is loading.

        Called by main.py when a worktree is selected, before
        _parse_and_display() completes.
        """
        self.remove_children()
        self._rendered_count = 0
        self._pending_trees = []

        # Mount filter bar placeholder (empty counts)
        self._filter_bar = FilterBar(classes="status-tab-bar")
        self.mount(self._filter_bar)
        self._filter_bar.update_state({s: 0 for s in _STATUS_ORDER}, None)

        # Mount skeleton cards
        for _ in range(count):
            self.mount(SessionCard.skeleton())

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
        """Clear and rebuild: filter bar + initial batch of session cards."""
        self.remove_children()
        self._rendered_count = 0
        self._pending_trees = []

        counts = self._count_by_status()
        self._filter_bar = FilterBar(classes="status-tab-bar")
        self.mount(self._filter_bar)
        self._filter_bar.update_state(counts, self._active_filter)

        self._rebuild_list()

        cards = self._ordered_cards()
        if not cards:
            self._selected_id = None
        elif self._selected_id not in {card.session.session_id for card in cards}:
            self._selected_id = None
            if self.has_focus:
                self._select_card_at(0)

    def _rebuild_list(self) -> None:
        """Render session cards in batches, grouped by lineage tree."""
        # ── Filter ──
        if self._active_filter is not None:
            filtered = [
                s for s in self._sessions
                if s.status == self._active_filter
            ]
        else:
            filtered = [
                s for s in self._sessions
                if s.status != Status.NOISE or self._show_noise
            ]

        # ── Sort ──
        def _sort_key(s: SessionInfo) -> tuple:
            ts = s.last_timestamp
            ts_val = ts.timestamp() if ts else 0
            return (-int(s.is_running), -ts_val)

        filtered.sort(key=_sort_key)

        # ── Fork parents ──
        fork_parents: set[str] = set()
        filtered_ids = {s.session_id for s in filtered}
        if self._lineage_graph:
            for sid, node in self._lineage_graph.items():
                if sid in filtered_ids and node.children:
                    for child_sid in node.children:
                        if self._lineage_types.get(child_sid) == "fork":
                            fork_parents.add(sid)
                            break
        self._fork_parents = fork_parents

        if not filtered:
            label = self._active_filter.value if self._active_filter else "matching"
            self.mount(
                Static(
                    f"  No {label} sessions",
                    classes="empty-state",
                )
            )
            return

        # ── Build all lineage trees ──
        all_trees = _build_lineage_trees(filtered, self._lineage_types, self._lineage_graph)

        # ── Split into initial + pending batches ──
        initial_trees, remaining_trees = _prepare_render_batches(
            all_trees, initial_size=INITIAL_BATCH_SIZE,
        )
        self._pending_trees = remaining_trees
        self._rendered_count = len(initial_trees)

        # ── Mount initial batch ──
        self._mount_tree_batch(initial_trees)

        # ── Show "loading more" indicator if there are pending trees ──
        if remaining_trees:
            self.mount(
                Static(
                    f"[#78716c]  ↓ 滚动加载更多 ({len(remaining_trees)} 组) …[/]",
                    classes="lazy-load-hint",
                    id="lazy-load-hint",
                )
            )

    def _mount_tree_batch(self, trees: list[list[SessionInfo]]) -> None:
        """Mount a batch of lineage trees as session cards."""
        prev_date = None

        for tree in trees:
            newest = tree[-1]
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

            if len(tree) == 1:
                s = tree[0]
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
                    is_fork_point=(s.session_id in self._fork_parents),
                    spine_time=time_label,
                    spine_graph="━●",
                )
                if s.session_id == self._selected_id:
                    card.selected = True
                self.mount(card)
            else:
                group = LineageGroup(
                    tree_sessions=tree,
                    lineage_types=self._lineage_types,
                    all_meta=self._all_meta,
                    last_thoughts=self._last_thoughts,
                    fork_parents=self._fork_parents,
                    selected_id=self._selected_id,
                )
                self.mount(group)

    def _load_next_batch(self) -> None:
        """Append the next batch of trees when user scrolls near bottom."""
        if not self._pending_trees or self._is_loading_more:
            return

        self._is_loading_more = True

        # Remove the hint widget
        try:
            hint = self.query_one("#lazy-load-hint")
            hint.remove()
        except Exception:
            pass

        # Take next batch
        batch = self._pending_trees[:LAZY_BATCH_SIZE]
        self._pending_trees = self._pending_trees[LAZY_BATCH_SIZE:]
        self._rendered_count += len(batch)

        self._mount_tree_batch(batch)

        # Re-add hint if more pending
        if self._pending_trees:
            self.mount(
                Static(
                    f"[#78716c]  ↓ 滚动加载更多 ({len(self._pending_trees)} 组) …[/]",
                    classes="lazy-load-hint",
                    id="lazy-load-hint",
                )
            )

        self._is_loading_more = False

    def on_scroll_down(self) -> None:
        """Textual scroll event — load more cards when near bottom."""
        # Check if we're near the bottom (within 5 lines)
        if self.scroll_offset.y + self.size.height >= self.virtual_size.height - 5:
            self._load_next_batch()

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
