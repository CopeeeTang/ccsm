"""Session card widget for the middle panel.

Two-line compact layout:
  Line 1: title (white) + status tag (colored dot+text) + lineage badge + relative time (right-aligned)
  Line 2: 📝 AI intent / first user content (muted) + 💬 message count (right-aligned)
"""

from __future__ import annotations

from datetime import datetime, timezone

from rich.markup import escape as rich_escape
from textual.message import Message
from textual.reactive import reactive
from textual.widgets import Static

from ccsm.models.session import SessionInfo, SessionMeta, Status

# Lineage badge colors
_LINEAGE_BADGES = {
    "fork": ("[#60a5fa]⑂[/]", 2),       # blue fork icon
    "compact": ("[#a78bfa]⟳[/]", 2),    # purple compact icon
    "duplicate": ("[#f87171]⊕[/]", 2),  # red duplicate icon
}

# Status inline tags: (icon, label, color)
_STATUS_TAGS = {
    Status.ACTIVE: ("●", "Active", "#22c55e"),
    Status.BACKGROUND: ("◐", "Back", "#3b82f6"),
    Status.IDEA: ("◇", "Idea", "#a855f7"),
    Status.DONE: ("○", "Done", "#78716c"),
    Status.NOISE: ("·", "Noise", "#44403c"),
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


def _truncate(text: str, max_len: int) -> str:
    """Truncate text with ellipsis."""
    text = text.replace("\n", " ").strip()
    if len(text) > max_len:
        return text[:max_len - 1] + "…"
    return text


class SessionCard(Static):
    """A compact card representing a single session."""

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
        last_thought: str = "",  # deprecated, kept for caller compat
        lineage_type: str | None = None,  # "fork" | "compact" | "duplicate" | None
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self.session = session
        self.meta = meta
        self._lineage_type = lineage_type

    def render(self) -> str:
        """Render the card content as Rich markup (two-line layout).

        Line 1: title + status_tag + lineage_badge + relative_time (right-aligned)
        Line 2: intent + message count (right-aligned)
        """
        s = self.session
        w = self.size.width or 60

        # ── Line 1: title + status tag + lineage badge + relative time ──

        title = s.display_title
        if self.meta and self.meta.name:
            title = self.meta.name

        # Status tag: "●Active" etc.
        tag_icon, tag_label, tag_color = _STATUS_TAGS.get(
            s.status, ("?", "?", "#78716c")
        )
        tag_markup = f" [{tag_color}]{tag_icon}{tag_label}[/]"
        tag_visible_len = 1 + len(tag_icon) + len(tag_label)  # space + icon + label

        # Lineage badge (pain point #1: distinguish fork/compact/dup)
        badge_markup = ""
        badge_width = 0
        if self._lineage_type and self._lineage_type in _LINEAGE_BADGES:
            badge_markup_tpl, badge_width = _LINEAGE_BADGES[self._lineage_type]
            badge_markup = " " + badge_markup_tpl
            badge_width += 1  # space before badge

        # Running indicator (prepended to title)
        running_prefix = ""
        running_prefix_width = 0
        if s.is_running:
            running_prefix = "[bold #22c55e]⚡[/] "
            running_prefix_width = 3  # ⚡(2cols) + space

        time_str = _relative_time(s.last_timestamp)
        time_visible_len = len(time_str)

        # Budget: running_prefix + title + tag + badge + gap(2) + time
        max_title_len = max(
            10,
            w - running_prefix_width - tag_visible_len - badge_width - 2 - time_visible_len,
        )
        title_truncated = _truncate(title, max_title_len)
        title_display = rich_escape(title_truncated)
        title_visible_len = len(title_truncated)

        # Compute padding between left content and right-aligned time
        left_len = running_prefix_width + title_visible_len + tag_visible_len + badge_width
        padding1 = max(1, w - left_len - time_visible_len)

        line1 = (
            f"{running_prefix}"
            f"[#e7e5e4]{title_display}[/]"
            f"{tag_markup}"
            f"{badge_markup}"
            f"{' ' * padding1}"
            f"[#78716c]{rich_escape(time_str)}[/]"
        )

        # ── Line 2: intent + 💬 msg count (right-aligned) ────────────
        # Right part visible text and markup
        count_str = str(s.message_count)
        right_text = f"\U0001f4ac {count_str}"           # "💬 42"
        right_markup = f"[#78716c]\U0001f4ac {count_str}[/]"
        right_visible_len = len(right_text)

        # Left part: "  📝 \"intent...\""
        prefix_len = 7   # visible terminal width of '  📝 "' (emoji=2cols + 2spaces + space + quote)
        suffix_len = 1   # closing quote
        max_intent_len = max(5, w - prefix_len - suffix_len - 1 - right_visible_len)

        # Prefer AI-generated intent over raw first_user_content
        ai_intent = self.meta.ai_intent if self.meta else None
        first_msg = ai_intent or s.first_user_content or ""
        if first_msg:
            intent_truncated = _truncate(first_msg, max_intent_len)
            intent_display = rich_escape(intent_truncated)
            intent_visible_len = len(intent_truncated)
            left_len2 = prefix_len + intent_visible_len + suffix_len
            padding2 = max(1, w - left_len2 - right_visible_len)
            line2 = (
                f"  [#a8a29e]\U0001f4dd \"{intent_display}\"[/]"
                f"{' ' * padding2}"
                f"{right_markup}"
            )
        else:
            no_content = "  📝 (no content)"
            left_len2 = len(no_content) + 1   # +1 for emoji extra terminal width
            padding2 = max(1, w - left_len2 - right_visible_len)
            line2 = (
                f"  [#44403c]\U0001f4dd (no content)[/]"
                f"{' ' * padding2}"
                f"{right_markup}"
            )

        return "\n".join([line1, line2])

    def on_click(self) -> None:
        self.post_message(self.CardSelected(self.session))

    def watch_selected(self, value: bool) -> None:
        self.set_class(value, "-selected")

    def on_mount(self) -> None:
        self.add_class("session-card")
