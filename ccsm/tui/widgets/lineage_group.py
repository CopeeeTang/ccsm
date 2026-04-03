"""Lineage tree group widget — collapsible session tree container.

Groups sessions by lineage (compact continuation / fork branch).
Default: show newest 3 nodes, collapse older ones with expand button.
"""

from __future__ import annotations

from datetime import timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Static

from ccsm.models.session import SessionInfo, SessionMeta, Status
from ccsm.tui.widgets.session_card import SessionCard


# Maximum visible nodes before collapsing
_DEFAULT_VISIBLE = 3


class _ExpandBar(Static):
    """Clickable expand/collapse bar for lineage groups."""

    class Clicked(Message):
        """Emitted when the bar is clicked."""

        def __init__(self) -> None:
            super().__init__()

    def on_click(self) -> None:
        self.post_message(self.Clicked())


class LineageGroup(Vertical):
    """A collapsible group of session cards sharing the same lineage tree.

    Shows newest N cards by default, with a clickable expand bar for older ones.
    """

    def __init__(
        self,
        tree_sessions: list[SessionInfo],
        lineage_types: dict[str, str],
        all_meta: dict[str, SessionMeta],
        last_thoughts: dict[str, str] | None = None,
        fork_parents: set[str] | None = None,
        selected_id: str | None = None,
        max_visible: int = _DEFAULT_VISIBLE,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._sessions = tree_sessions  # Already sorted by time ascending
        self._lineage_types = lineage_types
        self._all_meta = all_meta
        self._last_thoughts = last_thoughts or {}
        self._fork_parents = fork_parents or set()
        self._selected_id = selected_id
        self._max_visible = max_visible
        self._expanded = False

    def compose(self) -> ComposeResult:
        """Render the tree: newest N cards + optional expand bar."""
        if not self._sessions:
            return

        total = len(self._sessions)
        hidden_count = max(0, total - self._max_visible)

        # Determine which sessions to show
        if self._expanded or hidden_count == 0:
            visible = list(self._sessions)
        else:
            # Show newest N (end of the time-sorted list)
            visible = self._sessions[-self._max_visible:]

            # Mount expand bar for hidden older sessions
            yield _ExpandBar(
                f"[#78716c]  ▸ 展开 {hidden_count} 个更早的会话[/]",
                classes="lineage-expand-bar",
            )

        # Mount cards with lineage-aware spine
        for i, session in enumerate(visible):
            ltype = self._lineage_types.get(session.session_id, "root")
            meta = self._all_meta.get(session.session_id)
            thought = self._last_thoughts.get(session.session_id, "")

            is_first = (i == 0 and (self._expanded or hidden_count == 0))
            is_last = (i == len(visible) - 1)

            # Spine graph based on lineage type and position
            if ltype == "fork":
                graph = " ╰──⑂"
            elif is_last:
                graph = " └──●"
            elif is_first:
                graph = " ┌──●"
            else:
                graph = " │  ●"

            # Time label
            time_label = ""
            if session.last_timestamp:
                ts = session.last_timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                time_label = ts.strftime("%H:%M")

            card = SessionCard(
                session,
                meta=meta,
                last_thought=thought,
                lineage_type=ltype,
                is_fork_point=(session.session_id in self._fork_parents),
                spine_time=time_label,
            )

            # Apply staircase effect to history (distance from newest)
            # visible is sorted by time ascending, so newest is the last one.
            newest_idx = len(visible) - 1
            dist = newest_idx - i
            if dist > 0:
                card.add_class(f"history-step-{min(dist, 3)}")

            if session.session_id == self._selected_id:
                card.selected = True
            yield card

    def on__expand_bar_clicked(self, event: _ExpandBar.Clicked) -> None:
        """Toggle expand/collapse when the bar is clicked."""
        self._expanded = not self._expanded
        self.remove_children()
        for child in self.compose():
            self.mount(child)

    def on_mount(self) -> None:
        self.add_class("lineage-group")
        # Apply fork-universe styling if any session in this group is of type fork
        if any(self._lineage_types.get(s.session_id) == "fork" for s in self._sessions):
            self.add_class("fork-universe")
