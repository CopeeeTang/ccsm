"""Session card widget for the session list panel.

Compose-based layout using Textual CSS flex for perfect alignment:
  Horizontal:
    - Static (Spine Gutter): time + graph connector
    - Vertical (Card Body):
        - Horizontal (Title Row): Title (1fr) + Status + Lineage + Time
        - Horizontal (Intent Row): Intent (1fr) + Message Count
"""

from __future__ import annotations

from datetime import datetime, timezone

from rich.markup import escape as rich_escape
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ccsm.models.session import SessionInfo, SessionMeta, Status

# Lineage badge colors
_LINEAGE_BADGES = {
    "fork": ("⑂", "lineage-fork"),
    "compact": ("⟳", "lineage-compact"),
    "duplicate": ("⊕", "lineage-dup"),
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
        meta: SessionMeta | None = None,
        last_thought: str = "",
        lineage_type: str | None = None,
        spine_time: str = "",
        spine_graph: str = "",
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.meta = meta
        self._lineage_type = lineage_type
        self._spine_time = spine_time
        self._spine_graph = spine_graph

    def compose(self) -> ComposeResult:
        """Build card layout as nested Textual widgets."""
        s = self.session

        # ── Spine gutter (time + graph connector) ──
        if self._spine_time or self._spine_graph:
            time_part = self._spine_time or ""
            graph_part = self._spine_graph or "┃"
            yield Static(
                f"[#78716c]{rich_escape(time_part)}[/]\n[#3a3835]{rich_escape(graph_part)}[/]",
                classes="card-spine",
            )

        # ── Card body ──
        with Vertical(classes="card-body"):
            # ── Row 1: Title + status + lineage + time ──
            with Horizontal(classes="card-row-title"):
                # Running indicator
                running_prefix = ""
                if s.is_running:
                    running_prefix = "[bold #788c5d]⚡[/] "

                # Title
                title = s.display_title
                if self.meta and self.meta.name:
                    title = self.meta.name
                title_truncated = _truncate(title, 60)
                yield Static(
                    f"{running_prefix}[#e8e6dc]{rich_escape(title_truncated)}[/]",
                    classes="card-title",
                )

                # Status tag
                tag_icon, tag_label, tag_class = _STATUS_TAGS.get(
                    s.status, ("?", "?", "tag-done")
                )
                yield Static(
                    f"{tag_icon}{tag_label}",
                    classes=f"card-tag {tag_class}",
                )

                # Lineage badge
                if self._lineage_type and self._lineage_type in _LINEAGE_BADGES:
                    badge_icon, badge_class = _LINEAGE_BADGES[self._lineage_type]
                    yield Static(badge_icon, classes=f"card-badge {badge_class}")

                # Relative time (right-aligned)
                time_str = _relative_time(s.last_timestamp)
                if time_str:
                    yield Static(
                        f"[#78716c]{rich_escape(time_str)}[/]",
                        classes="card-time",
                    )

            # ── Row 2: Intent + message count ──
            with Horizontal(classes="card-row-intent"):
                # Intent text
                ai_intent = self.meta.ai_intent if self.meta else None
                first_msg = ai_intent or s.first_user_content or ""
                if first_msg and not ai_intent:
                    first_msg = _clean_intent_text(first_msg)

                if first_msg:
                    intent_truncated = _truncate(first_msg, 80)
                    yield Static(
                        f"[#b0aea5]📝 \"{rich_escape(intent_truncated)}\"[/]",
                        classes="card-intent",
                    )
                else:
                    yield Static(
                        "[#3a3835]📝 (no content)[/]",
                        classes="card-intent",
                    )

                # Message count (right-aligned)
                yield Static(
                    f"[#78716c]💬 {s.message_count}[/]",
                    classes="card-msgcount",
                )

    def on_click(self) -> None:
        self.post_message(self.CardSelected(self.session))

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "-selected")

    def on_mount(self) -> None:
        self.add_class("session-card")
