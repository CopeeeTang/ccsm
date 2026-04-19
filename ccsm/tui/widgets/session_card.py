"""Session card widget for the session list panel.

Compose-based layout using Textual CSS flex for perfect alignment:
  Horizontal:
    - Static (Spine Gutter): time + graph connector
    - Vertical (Card Body):
        - Horizontal (Title Row): Title (1fr) + Status + Lineage + Time
        - Horizontal (Intent Row): Intent (1fr) + Message Count
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ccsm.models.session import SessionInfo, SessionMeta, Status, resolve_title

logger = logging.getLogger(__name__)

# Lineage badge colors — more prominent labels
_LINEAGE_BADGES = {
    "fork": ("⑂ Fork", "lineage-fork"),
    "compact": ("⟳ Compact", "lineage-compact"),
    "duplicate": ("⊕ Dup", "lineage-dup"),
}

# Status inline tags: (icon, label, css_class)
_STATUS_TAGS = {
    Status.ACTIVE: ("●", "Active", "tag-active"),
    Status.BACKGROUND: ("◐", "Back", "tag-background"),
    Status.IDEA: ("◇", "Idea", "tag-idea"),
    Status.DONE: ("○", "Done", "tag-done"),
    Status.NOISE: ("·", "Noise", "tag-noise"),
}


def _relative_time(dt: datetime | None) -> str:
    """Format a datetime as relative time (e.g., '2h ago', '3d ago')."""
    if dt is None:
        return ""
    now = datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    delta = (now - dt).total_seconds()
    if delta < 0:
        return "just now"
    if delta < 60:
        return "just now"
    if delta < 3600:
        return f"{int(delta / 60)}m ago"
    if delta < 86400:
        return f"{int(delta / 3600)}h ago"
    days = int(delta / 86400)
    if days == 1:
        return "yesterday"
    if days < 30:
        return f"{days}d ago"
    return f"{days // 30}mo ago"


def _clean_intent_text(text: str) -> str:
    """Clean numbered/listed text into concise intent form."""
    import re
    text = text.replace("\n", " ").strip()
    parts = re.split(r'(?:^|(?<=\s))\d+[\.\)、]\s*', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return "；".join(parts)
    return text


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len - 1] + "…"
    return text


class SessionCard(Widget):
    """A compact card representing a single session — compose-based layout."""

    selected = reactive(False)

    class CardSelected(Message):
        """Emitted when a card is clicked."""

        def __init__(self, session: SessionInfo) -> None:
            self.session = session
            super().__init__()

    def __init__(
        self,
        session: SessionInfo,
        meta: "SessionMeta | None" = None,
        last_thought: str = "",
        lineage_type: str | None = None,
        is_fork_point: bool = False,
        spine_time: str = "",
        spine_graph: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.meta = meta
        self._lineage_type = lineage_type
        self._is_fork_point = is_fork_point
        self._spine_time = spine_time
        self._spine_graph = spine_graph

    def compose(self) -> ComposeResult:
        """Build card layout as nested Textual widgets."""
        s = self.session

        # ── Spine gutter (just time now, graph is hidden via CSS) ──
        if self._spine_time:
            time_part = self._spine_time
            time_color = "#d97757" if s.is_running else "#78716c"

            with Vertical(classes="card-spine"):
                yield Static(
                    f"[{time_color} bold]{rich_escape(time_part)}[/]",
                    classes="card-spine-time",
                )

        # ── Card body ──
        body_classes = "card-body"
        if self._lineage_type == "fork":
            body_classes += " -fork-body"

        with Vertical(classes=body_classes):
            # ── Row 1: Title + status + lineage + time ──
            with Horizontal(classes="card-row-title"):
                # Title (with running prefix and id for update_data)
                yield Static(
                    self._render_title_markup(),
                    classes="card-title",
                    id=f"ct-{self.session.session_id[:16]}",
                )

                # Status tag
                tag_icon, tag_label, tag_class = _STATUS_TAGS.get(
                    s.status, ("?", "?", "tag-done")
                )
                yield Static(
                    f"{tag_icon}{tag_label}",
                    classes=f"card-tag {tag_class}",
                    id=f"cs-{self.session.session_id[:16]}",
                )

                # Lineage badge
                if self._lineage_type and self._lineage_type in _LINEAGE_BADGES:
                    badge_icon, badge_class = _LINEAGE_BADGES[self._lineage_type]
                    yield Static(badge_icon, classes=f"card-badge {badge_class}")

                # Fork Point badge
                if self._is_fork_point:
                    yield Static("⑂ Fork Point", classes="badge-fork-point")

                # Right-aligned time info
                right_label = self._render_time_label()
                if right_label:
                    yield Static(
                        f"[#78716c]{rich_escape(right_label)}[/]",
                        classes="card-time",
                        id=f"cm-{self.session.session_id[:16]}",
                    )

            # ── Row 2: Intent + message count ──
            with Horizontal(classes="card-row-intent"):
                yield Static(
                    self._render_intent_markup(),
                    classes="card-intent",
                    id=f"ci-{self.session.session_id[:16]}",
                )

    # ── Render helpers (shared by compose + update_data) ──────────────

    def _render_title_markup(self) -> str:
        s = self.session
        running_prefix = "[bold #788c5d]⚡[/] " if s.is_running else ""
        title = resolve_title(s, self.meta)
        title_truncated = _truncate(title, 60)
        return f"{running_prefix}[#e8e6dc]{rich_escape(title_truncated)}[/]"

    def _render_time_label(self) -> str:
        s = self.session
        time_str = _relative_time(s.last_timestamp)
        extra_parts = []
        if s.last_timestamp and s.first_timestamp and s.last_timestamp > s.first_timestamp:
            diff = (s.last_timestamp - s.first_timestamp).total_seconds()
            if diff < 60:
                extra_parts.append(f"{int(diff)}s")
            elif diff < 3600:
                extra_parts.append(f"{int(diff // 60)}m")
            else:
                extra_parts.append(f"{int(diff // 3600)}h{int((diff % 3600) // 60)}m")
        model_str = s.model_name or ""
        if model_str.startswith("claude-"):
            model_str = model_str[7:]
        if model_str:
            extra_parts.append(model_str)
        if extra_parts:
            return f"{'·'.join(extra_parts)}  {time_str}" if time_str else '·'.join(extra_parts)
        return time_str

    def _render_intent_markup(self) -> str:
        s = self.session
        ai_intent = self.meta.ai_intent if self.meta else None
        first_msg = ai_intent or s.first_user_content or ""
        if first_msg and not ai_intent:
            first_msg = _clean_intent_text(first_msg)
        if first_msg:
            intent_truncated = _truncate(first_msg, 80)
            return f"[#b0aea5]{rich_escape(intent_truncated)}[/]"
        return "[#3a3835](no content)[/]"

    def update_data(
        self,
        session: SessionInfo,
        meta: "SessionMeta | None" = None,
        last_thought: str = "",
        lineage_type: str | None = None,
        is_fork_point: bool = False,
    ) -> None:
        """Update card data in-place without rebuilding widget tree.

        Uses query_one() to find and update specific Static sub-widgets.
        """
        self.session = session
        self.meta = meta
        self._lineage_type = lineage_type
        self._is_fork_point = is_fork_point

        sid = session.session_id[:16]

        # Update title
        try:
            self.query_one(f"#ct-{sid}", Static).update(self._render_title_markup())
        except Exception as e:
            logger.debug("update_data title failed for %s: %s", sid, e)

        # Update status tag
        try:
            tag_icon, tag_label, tag_class = _STATUS_TAGS.get(
                session.status, ("?", "?", "tag-done")
            )
            self.query_one(f"#cs-{sid}", Static).update(f"{tag_icon}{tag_label}")
        except Exception as e:
            logger.debug("update_data status failed for %s: %s", sid, e)

        # Update time
        try:
            right_label = self._render_time_label()
            if right_label:
                self.query_one(f"#cm-{sid}", Static).update(
                    f"[#78716c]{rich_escape(right_label)}[/]"
                )
        except Exception as e:
            logger.debug("update_data time failed for %s: %s", sid, e)

        # Update intent
        try:
            self.query_one(f"#ci-{sid}", Static).update(self._render_intent_markup())
        except Exception as e:
            logger.debug("update_data intent failed for %s: %s", sid, e)


    def on_click(self) -> None:
        self.post_message(self.CardSelected(self.session))

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "-selected")

    def on_mount(self) -> None:
        self.add_class("session-card")
