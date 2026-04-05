"""Lineage tree group widget — collapsible session tree container.

Groups sessions by lineage (compact continuation / fork branch).
Default: show newest 3 nodes, collapse older ones with expand button.

Visual design (inspired by web_demo/index.html):
  - Newest session at TOP (no indent, full opacity)
  - Older sessions below, progressively indented + dimmed
  - Compact chain: staircase indent + blue left border
  - Fork branches: ⑂ anchor label + purple-bordered block
  - Duplicate: dimmed display with ⊕ badge
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

# Maximum history step depth (prevents deep stacking)
_MAX_STEP_DEPTH = 2


class _ExpandBar(Static):
    """Clickable expand/collapse bar for lineage groups."""

    class Clicked(Message):
        """Emitted when the bar is clicked."""

        def __init__(self) -> None:
            super().__init__()

    def on_click(self) -> None:
        self.post_message(self.Clicked())


class _ForkPointSeparator(Static):
    """Visual separator marking a fork point in the lineage tree."""
    pass


class LineageGroup(Vertical):
    """A collapsible group of session cards sharing the same lineage tree.

    Display order: newest at top, oldest at bottom (indented + dimmed).
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
        self._sessions = tree_sessions  # Sorted by time ascending
        self._lineage_types = lineage_types
        self._all_meta = all_meta
        self._last_thoughts = last_thoughts or {}
        self._fork_parents = fork_parents or set()
        self._selected_id = selected_id
        self._max_visible = max_visible
        self._expanded = False

    def compose(self) -> ComposeResult:
        """Render the tree: newest first, oldest last (indented).

        Order: newest → oldest (top → bottom)
        Indent: newest = 0, older = step-1, oldest = step-2
        """
        if not self._sessions:
            return

        total = len(self._sessions)
        hidden_count = max(0, total - self._max_visible)

        # _sessions is sorted ascending (oldest first).
        # We want to DISPLAY newest first, so reverse for rendering.
        if self._expanded or hidden_count == 0:
            visible = list(reversed(self._sessions))
        else:
            # Take newest N from the end, then reverse for display
            visible = list(reversed(self._sessions[-self._max_visible:]))

        # Separate fork sessions from main trunk
        trunk_sessions = []
        fork_sessions = []
        for s in visible:
            ltype = self._lineage_types.get(s.session_id, "root")
            if ltype == "fork":
                fork_sessions.append(s)
            else:
                trunk_sessions.append(s)

        # ── Render main trunk (newest first) ──
        for i, session in enumerate(trunk_sessions):
            ltype = self._lineage_types.get(session.session_id, "root")
            meta = self._all_meta.get(session.session_id)
            thought = self._last_thoughts.get(session.session_id, "")
            is_fork_point = session.session_id in self._fork_parents

            # Insert fork-point separator BEFORE the fork-point card
            if is_fork_point:
                yield _ForkPointSeparator(
                    "[#a855f7]─── ⑂ Fork Point ───[/]",
                    classes="fork-point-separator",
                )

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
                is_fork_point=is_fork_point,
                spine_time=time_label,
            )

            # i=0 is newest (no indent), i>0 is older (more indent)
            if i > 0:
                step = min(i, _MAX_STEP_DEPTH)
                card.add_class(f"history-step-{step}")

            # Type-specific styling
            if ltype == "compact":
                card.add_class("compact-card")
            elif ltype == "duplicate":
                card.add_class("duplicate-card")

            if session.session_id == self._selected_id:
                card.selected = True
            yield card

        # Expand bar at the bottom (after visible cards)
        if not self._expanded and hidden_count > 0:
            yield _ExpandBar(
                f"[#78716c]  ▸ 展开 {hidden_count} 个更早的会话[/]",
                classes="lineage-expand-bar",
            )

        # ── Render fork branches (isolated visual block) ──
        if fork_sessions:
            fork_source_title = ""
            if self._fork_parents:
                for parent_sid in self._fork_parents:
                    for s in trunk_sessions:
                        if s.session_id == parent_sid:
                            fork_source_title = s.display_title[:30]
                            meta = self._all_meta.get(parent_sid)
                            if meta and meta.name:
                                fork_source_title = meta.name[:30]
                            break

            anchor_text = "⑂ Fork" + (f" from: {rich_escape(fork_source_title)}" if fork_source_title else "")

            # Yield fork container content directly (no context manager)
            # so that _do_rebuild can mount them correctly
            yield Static(
                f"[#a855f7 bold]{anchor_text}[/]",
                classes="fork-anchor-label",
            )
            for session in fork_sessions:
                meta = self._all_meta.get(session.session_id)
                thought = self._last_thoughts.get(session.session_id, "")

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
                    lineage_type="fork",
                    is_fork_point=False,
                    spine_time=time_label,
                )
                card.add_class("fork-card")
                if session.session_id == self._selected_id:
                    card.selected = True
                yield card

    def on__expand_bar_clicked(self, event: _ExpandBar.Clicked) -> None:
        """Toggle expand/collapse when the bar is clicked."""
        self._expanded = not self._expanded
        self._do_rebuild()

    def _do_rebuild(self) -> None:
        """Remove children and re-mount from compose."""
        self.remove_children()
        for child in self.compose():
            self.mount(child)

    def on_mount(self) -> None:
        self.add_class("lineage-group")
        has_fork = any(
            self._lineage_types.get(s.session_id) == "fork"
            for s in self._sessions
        )
        if has_fork:
            self.add_class("has-fork-branch")
