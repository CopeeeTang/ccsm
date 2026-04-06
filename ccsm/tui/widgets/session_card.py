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
from textual.containers import Vertical
from textual.message import Message
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Static

from ccsm.models.session import SessionInfo, SessionMeta, Status

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

# Rich color constants for inline rendering (replaces CSS class-based coloring)
_STATUS_TAG_COLORS = {
    "tag-active": "#788c5d",
    "tag-background": "#c09553",
    "tag-idea": "#6b99b4",
    "tag-done": "#78716c",
    "tag-noise": "#3a3835",
}

_BADGE_COLORS = {
    "lineage-compact": "#6b99b4",
    "lineage-fork": "#a855f7",
    "lineage-dup": "#78716c",
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
    loading = reactive(False)

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

    @classmethod
    def skeleton(cls) -> "SessionCard":
        """Create a skeleton/shimmer placeholder card.

        Used as visual placeholder while JSONL data is loading.
        Renders gray blocks instead of real content.
        """
        from pathlib import Path

        placeholder = SessionInfo(
            session_id="__skeleton__",
            project_dir="",
            jsonl_path=Path("/dev/null"),
            message_count=0,
            status=Status.DONE,
        )
        card = cls(placeholder)
        card.loading = True
        return card

    def compose(self) -> ComposeResult:
        """Build card layout — optimized flat structure.

        When self.loading is True, renders gray placeholder blocks
        (skeleton shimmer) instead of real content.
        """
        # ── Skeleton mode ──
        if self.loading:
            with Vertical(classes="card-body"):
                yield Static(
                    "[#3a3835]━━━━━━━━━━━━━━━━━━━━━━━━━━[/]  [#3a3835]━━━━[/]",
                    classes="card-title-line card-skeleton-line",
                )
                yield Static(
                    "[#3a3835]━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━[/]",
                    classes="card-intent-line card-skeleton-line",
                )
            return

        # ── Normal rendering (existing code below) ──
        s = self.session

        # ── Spine gutter ──
        if self._spine_time:
            time_color = "#d97757" if s.is_running else "#78716c"
            with Vertical(classes="card-spine"):
                yield Static(
                    f"[{time_color} bold]{rich_escape(self._spine_time)}[/]",
                    classes="card-spine-time",
                )

        # ── Card body ──
        body_classes = "card-body"
        if self._lineage_type == "fork":
            body_classes += " -fork-body"

        with Vertical(classes=body_classes):
            # ── Line 1: Title row (single Static with Rich markup) ──
            parts = []

            # Running indicator
            if s.is_running:
                parts.append("[bold #788c5d]⚡[/] ")

            # Title
            title = s.display_title
            if self.meta and self.meta.name:
                title = self.meta.name
            title_truncated = _truncate(title, 60)
            parts.append(f"[#e8e6dc]{rich_escape(title_truncated)}[/]")

            # Status tag
            tag_icon, tag_label, tag_class = _STATUS_TAGS.get(
                s.status, ("?", "?", "tag-done")
            )
            parts.append(f"  [{_STATUS_TAG_COLORS.get(tag_class, '#78716c')}]{tag_icon}{tag_label}[/]")

            # Lineage badge
            if self._lineage_type and self._lineage_type in _LINEAGE_BADGES:
                badge_icon, badge_class = _LINEAGE_BADGES[self._lineage_type]
                parts.append(f" [{_BADGE_COLORS.get(badge_class, '#78716c')}]{badge_icon}[/]")

            # Fork point badge
            if self._is_fork_point:
                parts.append(" [#a855f7]⑂ Fork Point[/]")

            # Right-aligned time + duration
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

            time_str = _relative_time(s.last_timestamp)
            right_label = time_str
            if extra_parts:
                right_label = f"{'·'.join(extra_parts)}  {time_str}" if time_str else '·'.join(extra_parts)

            if right_label:
                parts.append(f"  [#78716c]{rich_escape(right_label)}[/]")

            yield Static("".join(parts), classes="card-title-line")

            # ── Line 2: Intent + message count (single Static) ──
            ai_intent = self.meta.ai_intent if self.meta else None
            first_msg = ai_intent or s.first_user_content or ""
            if first_msg and not ai_intent:
                first_msg = _clean_intent_text(first_msg)

            intent_text = ""
            if first_msg:
                intent_truncated = _truncate(first_msg, 80)
                intent_text = f"[#b0aea5]📝 \"{rich_escape(intent_truncated)}\"[/]"
            else:
                intent_text = "[#3a3835]📝 (no content)[/]"

            msg_count = f"  [#78716c]💬 {s.message_count}[/]"

            yield Static(intent_text + msg_count, classes="card-intent-line")

    def on_click(self) -> None:
        self.post_message(self.CardSelected(self.session))

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "-selected")

    def watch_loading(self, value: bool) -> None:
        self.set_class(value, "-loading")

    def on_mount(self) -> None:
        self.add_class("session-card")
