"""Right panel: Session detail widget — simplified 4-zone layout.

Layout (top to bottom, after Part 3 simplification):
1. AI DIGEST — four-dimension structured summary (always expanded)
2. MILESTONES — decision points / phase progress (always expanded)
3. WHAT WAS DONE — tool_use operations (collapsible, collapsed)
4. LAST EXCHANGE — last user+assistant pair + recovery context
                   (collapsible, collapsed; merged with former "where you left off")

Removed in Part 3:
  - SESSION card header (redundant with list card)
  - CONTEXT SUMMARY (overlaps with digest)
  - WHERE YOU LEFT OFF standalone (merged into LAST EXCHANGE)

Data source philosophy: mine JSONL first (zero cost), AI second (on demand).
"""

from __future__ import annotations

import re as _re
from datetime import datetime, timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Collapsible, Markdown, Static

from ccsm.models.session import (
    Breakpoint,
    CompactSummaryParsed,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
    Priority,
    SessionDetailData,
    SessionDigest,
    SessionInfo,
    SessionMeta,
    SessionSummary,
    Status,
)
from ccsm.tui.widgets.session_card import _relative_time


def _format_timestamp(dt: Optional[datetime]) -> str:
    if dt is None:
        return "—"
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%Y-%m-%d %H:%M")


def _format_duration(seconds: Optional[float]) -> str:
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    hours = int(seconds / 3600)
    mins = int((seconds % 3600) / 60)
    return f"{hours}h {mins}m"


def _format_tokens(tokens: int) -> str:
    """Format token count: 12345 → '12.3k'."""
    if tokens == 0:
        return "—"
    if tokens < 1000:
        return str(tokens)
    return f"{tokens / 1000:.1f}k"


# Status inline tags (same as session_card but for detail display)
_STATUS_TAGS = {
    Status.ACTIVE: ("[#788c5d]● Active[/]", "#788c5d"),
    Status.BACKGROUND: ("[#6a9bcc]◐ Back[/]", "#6a9bcc"),
    Status.IDEA: ("[#a855f7]◇ Idea[/]", "#a855f7"),
    Status.DONE: ("[#78716c]○ Done[/]", "#78716c"),
    Status.NOISE: ("[#3a3835]· Noise[/]", "#3a3835"),
}

_PRIORITY_DISPLAY = {
    Priority.FOCUS: ("[#d97757]▲[/] FOCUS", "#d97757"),
    Priority.WATCH: ("[#facc15]△[/] WATCH", "#facc15"),
    Priority.PARK: ("[#78716c]▽[/] PARK", "#78716c"),
    Priority.HIDE: ("[#3a3835]▿[/] HIDE", "#3a3835"),
}

# Milestone status rendering
_MS_ICONS = {
    MilestoneStatus.DONE: ("[#788c5d]✓[/]", "#788c5d"),
    MilestoneStatus.IN_PROGRESS: ("[#d97757]▶[/]", "#d97757"),
    MilestoneStatus.PENDING: ("[#78716c]○[/]", "#78716c"),
}


def _strip_emoji_prefix(label: str) -> str:
    """Strip emoji prefix from milestone labels like '💬 讨论' → '讨论'."""
    stripped = _re.sub(
        r'^[\U0001f300-\U0001faff\U00002702-\U000027b0\u2600-\u26ff\u2700-\u27bf✓✅🔧🔍📊💬▶○🎯]+\s*',
        '', label
    )
    return stripped.strip() or label


def _clean_intent_text(text: str) -> str:
    """Clean numbered/listed text into concise intent form.

    Transforms patterns like:
      "1.添加GPT-5.4支持 2.检查soul.md"  → "添加GPT-5.4支持；检查soul.md"
      "1) first task 2) second"           → "first task；second"
    """
    text = text.replace("\n", " ").strip()
    # Match number+separator only at string-start or after whitespace
    parts = _re.split(r'(?:^|(?<=\s))\d+[\.\)、]\s*', text)
    parts = [p.strip() for p in parts if p.strip()]
    if len(parts) > 1:
        return "；".join(parts)
    return text


class SessionDetail(VerticalScroll):
    """Detail view for a selected session — recovery-first layout."""

    def __init__(self, **kwargs) -> None:
        super().__init__(id="detail-content", **kwargs)
        self._session: Optional[SessionInfo] = None
        self._meta: Optional[SessionMeta] = None
        self._summary: Optional[SessionSummary] = None
        self._last_replies: list[str] = []
        self._detail_data: Optional[SessionDetailData] = None
        self._compact_parsed: Optional[CompactSummaryParsed] = None

    def show_session(
        self,
        session: SessionInfo,
        meta: Optional[SessionMeta] = None,
        summary: Optional[SessionSummary] = None,
        last_replies: Optional[list[str]] = None,
        detail_data: Optional[SessionDetailData] = None,
        compact_parsed: Optional[CompactSummaryParsed] = None,
    ) -> None:
        """Display detail for a session."""
        self._session = session
        self._meta = meta
        self._summary = summary
        self._last_replies = last_replies or []
        self._detail_data = detail_data
        self._compact_parsed = compact_parsed
        self._rebuild()

    def clear_detail(self) -> None:
        self._session = None
        self.remove_children()
        self.mount(Static("Select a session to view details", classes="empty-state"))

    def _rebuild(self) -> None:
        """Rebuild detail panel with progressive loading.

        Frame 1 (immediate): AI Digest + Milestones (core content)
        Frame 2 (+50ms): What Was Done + Last Exchange (collapsed sections)

        This prevents blocking the UI thread while building heavy
        Collapsible/Markdown widgets for the collapsed sections.
        """
        self.remove_children()
        s = self._session
        if s is None:
            self.mount(Static("Select a session to view details", classes="empty-state"))
            return

        # ── Frame 1: Always-visible core sections (immediate) ──
        self._mount_digest_section()
        self._mount_milestones_section()

        # ── Frame 2: Collapsed sections (deferred 50ms) ──
        # Capture session_id to avoid stale mount if user switches quickly
        target_sid = s.session_id
        self.set_timer(0.05, lambda: self._mount_deferred_sections(target_sid))

    def _mount_deferred_sections(self, target_sid: str) -> None:
        """Mount collapsed sections if still viewing the same session."""
        if self._session is None or self._session.session_id != target_sid:
            return  # User switched to a different session — skip stale mount
        self._mount_what_was_done_section()
        self._mount_last_exchange_section()

    # ── AI DIGEST (five-dimension structured summary) ──────────

    def _mount_digest_section(self) -> None:
        """Mount AI Digest — five-dimension recovery summary, always expanded."""
        digest = self._summary.digest if self._summary else None

        section = Vertical(classes="detail-section det-digest-section")
        self.mount(section)
        section.mount(Static(
            "  [#a8a29e bold]AI DIGEST[/]",
            classes="detail-section-title",
        ))

        if not digest:
            section.mount(Static(
                "  [#78716c italic]Press [/][bold #d97757]s[/]"
                "[#78716c italic] to generate AI digest.[/]",
                classes="detail-section-body",
            ))
            return

        V = "#e8e6dc"
        K = "#b0aea5"
        lines: list[str] = []

        # Progress
        lines.append(f"  [#788c5d bold]Progress[/]  [{V}]{rich_escape(digest.progress)}[/]")

        # Decisions
        decisions = digest.decisions if digest.decisions else []
        if decisions:
            lines.append(f"  [#d97757 bold]Decisions[/]")
            for dec in decisions:
                lines.append(f"    [#d97757]•[/] [{V}]{rich_escape(dec)}[/]")

        # Breakpoint (visually prominent)
        lines.append(f"  [#e87b7b bold]Breakpoint[/] [{V}]{rich_escape(digest.breakpoint)}[/]")

        # Todo
        todo = digest.todo if digest.todo else digest.next_steps or []
        if todo:
            lines.append(f"  [#a78bfa bold]Todo[/]")
            for item in todo:
                lines.append(f"    [#a78bfa]•[/] [{V}]{rich_escape(item)}[/]")

        section.mount(Static("\n".join(lines), classes="detail-section-body"))

    # ── MILESTONES (1st priority) ─────────────────────────────

    def _mount_milestones_section(self) -> None:
        """Mount milestone timeline — Stepper style with left guide line.

        IN_PROGRESS milestones get background highlight for visual focus.
        """
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static(
            "  [#a8a29e bold]MILESTONES[/]",
            classes="detail-section-title",
        ))

        milestones: list[Milestone] = []

        # L1: From compact summary (zero cost, highest quality)
        if self._compact_parsed:
            from ccsm.core.compact_parser import extract_milestones_from_compact
            milestones = extract_milestones_from_compact(self._compact_parsed)

        # L2: From LLM-generated summary (if available and no compact)
        if not milestones and self._summary and self._summary.milestones:
            milestones = self._summary.milestones

        # L3: From rule-based extraction (always available as fallback)
        if not milestones and self._summary and self._summary.mode == "extract" and self._summary.milestones:
            milestones = self._summary.milestones

        if not milestones:
            section.mount(Static(
                "  [#78716c italic]No milestone data. Press [/][bold #d97757]s[/][#78716c italic] to generate.[/]",
                classes="detail-section-body",
            ))
            return

        # Render milestones inside Stepper container
        stepper = Vertical(classes="det-milestones")
        section.mount(stepper)

        for ms in milestones:
            icon, color = _MS_ICONS.get(ms.status, ("[#78716c]○[/]", "#78716c"))

            clean_label = _strip_emoji_prefix(ms.label)
            label_markup = rich_escape(clean_label)

            detail_str = ""
            if ms.detail:
                detail_text = ms.detail
                if len(detail_text) > 60:
                    detail_text = detail_text[:59] + "…"
                detail_str = f"\n      [#a8a29e]{rich_escape(detail_text)}[/]"

            # IN_PROGRESS gets a dedicated highlighted container
            if ms.status == MilestoneStatus.IN_PROGRESS:
                ms_widget = Static(
                    f"  {icon} [{color} bold]{label_markup}[/]{detail_str}",
                    classes="det-ms-active",
                )
            else:
                ms_widget = Static(
                    f"  {icon} [{color}]{label_markup}[/]{detail_str}",
                    classes="det-ms-item",
                )
            stepper.mount(ms_widget)

            # Sub-items
            show_subs = ms.sub_items
            if ms.status != MilestoneStatus.IN_PROGRESS:
                show_subs = ms.sub_items[:2]
            for item in show_subs:
                sub_icon, sub_color = _MS_ICONS.get(
                    item.status, ("[#78716c]○[/]", "#78716c")
                )
                sub_label = rich_escape(item.label)
                here_marker = ""
                if item.status == MilestoneStatus.IN_PROGRESS:
                    here_marker = "  [#d97757 bold]← HERE[/]"
                stepper.mount(Static(
                    f"    {sub_icon} [{sub_color}]{sub_label}[/]{here_marker}",
                    classes="det-ms-sub",
                ))

    # ── WHAT WAS DONE (collapsible) ───────────────────────────

    def _mount_what_was_done_section(self) -> None:
        """Mount tool_use operations summary — collapsible."""
        dd = self._detail_data
        if dd is None:
            return  # No detail data loaded yet

        has_content = dd.files_edited or dd.commands_run or dd.files_read

        if not has_content:
            return  # Nothing to show

        lines: list[str] = []
        V = "#e8e6dc"

        if dd.files_edited:
            # Show up to 5 files, then "+N more"
            shown = dd.files_edited[:5]
            file_names = [_re.sub(r".*/", "", f) for f in shown]  # basename only
            extra = len(dd.files_edited) - len(shown)
            suffix = f" (+{extra})" if extra > 0 else ""
            lines.append(f"  [#788c5d bold]Edited[/]  [{V}]{', '.join(file_names)}{suffix}[/]")

        if dd.commands_run:
            shown = dd.commands_run[-5:]  # Last 5 commands
            for cmd in shown:
                cmd_short = cmd[:60] + "…" if len(cmd) > 60 else cmd
                lines.append(f"  [#facc15 bold]Ran[/]     [{V}]{rich_escape(cmd_short)}[/]")

        if dd.files_read:
            shown = dd.files_read[:5]
            file_names = [_re.sub(r".*/", "", f) for f in shown]
            extra = len(dd.files_read) - len(shown)
            suffix = f" (+{extra})" if extra > 0 else ""
            lines.append(f"  [#6a9bcc bold]Read[/]    [{V}]{', '.join(file_names)}{suffix}[/]")

        if dd.agents_spawned:
            for desc in dd.agents_spawned[:3]:
                desc_short = desc[:50] + "…" if len(desc) > 50 else desc
                lines.append(f"  [#a855f7 bold]Agent[/]   [{V}]{rich_escape(desc_short)}[/]")

        body = "\n".join(lines)

        collapsible = Collapsible(title="WHAT WAS DONE", collapsed=True)
        self.mount(collapsible)
        collapsible.mount(Static(body, classes="detail-section-body"))

    # ── LAST EXCHANGE (Chat bubble style + recovery context) ──────

    def _mount_last_exchange_section(self) -> None:
        """Mount last user+assistant message pair + recovery context.

        Merged from (formerly) _mount_where_left_off_section:
          - Adds breakpoint.last_topic as "Next" line after the bubbles
          - Adds key_insights[-1] as "Insight" line after the bubbles
        Default collapsed (per user requirement). Chat-bubble rendering retained.
        """
        from textual.containers import Horizontal

        dd = self._detail_data
        has_exchange = bool(dd and (dd.last_user_msg or dd.last_assistant_msg))
        has_reply = bool(self._last_replies)

        # Recovery context fields (formerly in where_left_off)
        bp = self._summary.breakpoint if self._summary else None
        next_topic = bp.last_topic if bp else None
        insight: Optional[str] = None
        if self._summary and self._summary.key_insights:
            insight = self._summary.key_insights[-1]

        has_next = bool(next_topic)
        has_insight = bool(insight)

        if not any([has_exchange, has_reply, has_next, has_insight]):
            return

        # Default collapsed — user's requirement
        collapsible = Collapsible(title="LAST EXCHANGE", collapsed=True)
        self.mount(collapsible)

        V = "#e8e6dc"
        K = "#b0aea5"

        # ── User bubble ──
        if dd and dd.last_user_msg:
            user_text = dd.last_user_msg[:300]
            if len(dd.last_user_msg) > 300:
                user_text += "…"
            row = Horizontal(classes="det-chat-row")
            collapsible.mount(row)
            row.mount(Static(" YOU ", classes="det-chat-avatar det-chat-avatar-user"))
            row.mount(Static(
                f"[{V}]{rich_escape(user_text)}[/]",
                classes="det-chat-msg",
            ))

        # ── AI bubble ──
        ai_text: Optional[str] = None
        if dd and dd.last_assistant_msg:
            ai_text = dd.last_assistant_msg[:300]
            if len(dd.last_assistant_msg) > 300:
                ai_text += "…"
        elif self._last_replies:
            ai_text = self._last_replies[-1][:300]
            if len(self._last_replies[-1]) > 300:
                ai_text += "…"

        if ai_text:
            row = Horizontal(classes="det-chat-row")
            collapsible.mount(row)
            row.mount(Static("  AI ", classes="det-chat-avatar"))
            row.mount(Static(
                f"[{K}]{rich_escape(ai_text)}[/]",
                classes="det-chat-msg",
            ))

        # ── Recovery context (merged from where_left_off) ──
        if has_next or has_insight:
            ctx_lines: list[str] = []
            if next_topic:
                ctx_lines.append(
                    f"  [#788c5d bold]Next[/] [{V}]{rich_escape(next_topic)}[/]"
                )
            if insight:
                trunc = insight[:100] + ("…" if len(insight) > 100 else "")
                ctx_lines.append(
                    f"  [#6a9bcc bold]Insight[/] [{V}]{rich_escape(trunc)}[/]"
                )
            collapsible.mount(Static(
                "\n".join(ctx_lines),
                classes="detail-section-body",
            ))
