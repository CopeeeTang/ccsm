"""Right panel: Session detail widget.

Redesigned around PM's "milestone timeline + breakpoint" concept:
1. Session metadata (compressed key-value pairs)
2. 🧭 Milestone timeline (key phase-transition nodes with ✓/▶/○ status)
3. 📍 Breakpoint reminder (where the user left off — most valuable info)
4. 💬 Claude's last reply (Markdown-rendered for detail recall)
"""

from __future__ import annotations

import re as _re
from datetime import datetime, timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.containers import Vertical, VerticalScroll
from textual.widgets import Markdown, Static

from ccsm.models.session import (
    Breakpoint,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
    Priority,
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


_STATUS_DISPLAY = {
    Status.ACTIVE: ("[#22c55e]●[/] ACTIVE", "#22c55e"),
    Status.BACKGROUND: ("[#3b82f6]◐[/] BACKGROUND", "#3b82f6"),
    Status.IDEA: ("[#a855f7]◇[/] IDEA", "#a855f7"),
    Status.DONE: ("[#78716c]○[/] DONE", "#78716c"),
    Status.NOISE: ("[#44403c]·[/] NOISE", "#44403c"),
}

_PRIORITY_DISPLAY = {
    Priority.FOCUS: ("[#fb923c]▲[/] FOCUS", "#fb923c"),
    Priority.WATCH: ("[#facc15]△[/] WATCH", "#facc15"),
    Priority.PARK: ("[#78716c]▽[/] PARK", "#78716c"),
    Priority.HIDE: ("[#44403c]▿[/] HIDE", "#44403c"),
}

# Milestone status rendering
_MS_ICONS = {
    MilestoneStatus.DONE: ("[#22c55e]✓[/]", "#22c55e"),
    MilestoneStatus.IN_PROGRESS: ("[#fb923c]▶[/]", "#fb923c"),
    MilestoneStatus.PENDING: ("[#78716c]○[/]", "#78716c"),
}


def _strip_emoji_prefix(label: str) -> str:
    """Strip emoji prefix from milestone labels like '💬 讨论' → '讨论'."""
    stripped = _re.sub(
        r'^[\U0001f300-\U0001faff\U00002702-\U000027b0\u2600-\u26ff\u2700-\u27bf✓✅🔧🔍📊💬▶○]+\s*',
        '', label
    )
    return stripped.strip() or label


class SessionDetail(VerticalScroll):
    """Detail view for a selected session — milestone-based timeline."""

    def __init__(self, **kwargs) -> None:
        super().__init__(id="detail-content", **kwargs)
        self._session: Optional[SessionInfo] = None
        self._meta: Optional[SessionMeta] = None
        self._summary: Optional[SessionSummary] = None
        self._last_replies: list[str] = []

    def show_session(
        self,
        session: SessionInfo,
        meta: Optional[SessionMeta] = None,
        summary: Optional[SessionSummary] = None,
        last_replies: Optional[list[str]] = None,
    ) -> None:
        """Display detail for a session."""
        self._session = session
        self._meta = meta
        self._summary = summary
        self._last_replies = last_replies or []
        self._rebuild()

    def clear_detail(self) -> None:
        self._session = None
        self.remove_children()
        self.mount(Static("Select a session to view details", classes="empty-state"))

    def _rebuild(self) -> None:
        """Rebuild detail panel with milestone-based layout."""
        self.remove_children()
        s = self._session
        if s is None:
            self.mount(Static("Select a session to view details", classes="empty-state"))
            return

        # ── Section 1: Compressed session metadata ────────────────────
        self._mount_section("📋 SESSION", self._build_description(s))

        # ── Section 2: Milestone timeline (core) ─────────────────────
        self._mount_milestones_section()

        # ── Section 3: Breakpoint reminder ────────────────────────────
        self._mount_breakpoint_section()

        # ── Section 4: Claude's last reply ────────────────────────────
        self._mount_last_reply_section()

    # ── SESSION description (compressed) ──────────────────────────────

    def _build_description(self, s: SessionInfo) -> str:
        title = s.display_title
        if self._meta and self._meta.name:
            title = self._meta.name
        title = rich_escape(title)

        status_markup, _ = _STATUS_DISPLAY.get(s.status, (s.status.value, "#78716c"))

        K = "#a8a29e"  # Key color (muted)
        V = "#e7e5e4"  # Value color

        # Duration + message count combined
        dur = _format_duration(s.duration_seconds)
        msg_info = f"{dur} ({s.message_count} msg)"

        # Relative time for "Last" field
        last_str = _relative_time(s.last_timestamp)

        # Running indicator
        running = "  [bold #22c55e]⚡ Running[/]" if s.is_running else ""

        lines = [
            f"  [{K}]Title   [/] [{V} bold]{title}[/]{running}",
            f"  [{K}]Status  [/] {status_markup}    [{K}]Duration[/]  [{V}]{msg_info}[/]",
            f"  [{K}]Branch  [/] [{V}]{rich_escape(s.git_branch or '—')}[/]    [{K}]Last    [/] [{V}]{last_str}[/]",
        ]

        # Prefer AI-generated intent over raw first_user_content
        ai_intent = self._meta.ai_intent if self._meta else None
        intent_text = ai_intent or (s.first_user_content or "")
        if intent_text:
            content = intent_text.replace("\n", " ").strip()
            if len(content) > 60:
                content = content[:59] + "…"
            lines.append(f"  [{K}]Intent  [/] [#a8a29e italic]\"{rich_escape(content)}\"[/]")

        if self._meta and self._meta.tags:
            tags_str = " ".join(f"[#fb923c]#{rich_escape(t)}[/]" for t in self._meta.tags)
            lines.append(f"  [{K}]Tags    [/] {tags_str}")

        return "\n".join(lines)

    # ── MILESTONES timeline ───────────────────────────────────────────

    def _mount_milestones_section(self) -> None:
        """Mount the milestone timeline — the core of context restoration."""
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static("[#a8a29e]─── 🧭 MILESTONES ───[/]", classes="detail-section-title"))

        milestones = []
        if self._summary:
            milestones = self._summary.milestones or []

        if not milestones:
            # Fallback: build milestones from legacy summary fields
            milestones = self._build_fallback_milestones()

        if not milestones:
            section.mount(Static(
                "  [#78716c italic]No milestones yet. Generate with: ccsm summarize[/]",
                classes="detail-section-body",
            ))
            return

        # Render each milestone as a compact one-liner
        lines: list[str] = []
        for ms in milestones:
            icon, color = _MS_ICONS.get(ms.status, ("[#78716c]○[/]", "#78716c"))

            # Extract clean label without emoji prefix and pad to fixed width
            clean_label = _strip_emoji_prefix(ms.label)
            # Pad label to 6 chars for alignment (Chinese chars count as ~2)
            padded = clean_label.ljust(6)

            detail_str = ""
            if ms.detail:
                detail_text = ms.detail
                if len(detail_text) > 50:
                    detail_text = detail_text[:49] + "…"
                detail_str = f"  [#a8a29e]{rich_escape(detail_text)}[/]"

            label_markup = rich_escape(padded)
            if ms.status == MilestoneStatus.IN_PROGRESS:
                lines.append(f"  {icon} [{color} bold]{label_markup}[/]{detail_str}")
            else:
                lines.append(f"  {icon} [{color}]{label_markup}[/]{detail_str}")

            # Sub-items (only for in-progress milestones)
            if ms.sub_items and ms.status == MilestoneStatus.IN_PROGRESS:
                for item in ms.sub_items:
                    sub_icon, sub_color = _MS_ICONS.get(item.status, ("[#78716c]○[/]", "#78716c"))
                    sub_label = rich_escape(item.label)
                    here_marker = ""
                    if item.status == MilestoneStatus.IN_PROGRESS:
                        here_marker = "  [#fb923c bold]← HERE[/]"
                    lines.append(f"    {sub_icon} [{sub_color}]{sub_label}[/]{here_marker}")

        section.mount(Static("\n".join(lines), classes="detail-section-body"))

    def _build_fallback_milestones(self) -> list[Milestone]:
        """Build milestones from legacy SessionSummary fields (tasks_completed + tasks_pending).

        This provides backward compatibility: even without LLM-generated milestones,
        we can derive a basic timeline from the existing summary structure.
        """
        if not self._summary:
            return []

        milestones: list[Milestone] = []

        # Completed tasks → DONE milestones
        for task in (self._summary.tasks_completed or []):
            milestones.append(Milestone(
                label=task,
                status=MilestoneStatus.DONE,
            ))

        # Decision trail → DONE milestones (decisions are completed by definition)
        for decision in (self._summary.decision_trail or []):
            milestones.append(Milestone(
                label=decision,
                status=MilestoneStatus.DONE,
            ))

        # Pending tasks → PENDING milestones
        for task in (self._summary.tasks_pending or []):
            milestones.append(Milestone(
                label=task,
                status=MilestoneStatus.PENDING,
            ))

        # last_context → IN_PROGRESS milestone
        if self._summary.last_context:
            milestones.append(Milestone(
                label="Last context",
                detail=self._summary.last_context[:80],
                status=MilestoneStatus.IN_PROGRESS,
            ))

        return milestones

    # ── BREAKPOINT reminder ───────────────────────────────────────────

    def _mount_breakpoint_section(self) -> None:
        """Mount the breakpoint reminder — the most valuable info for context restoration."""
        bp = self._summary.breakpoint if self._summary else None

        section = Vertical(classes="detail-section detail-breakpoint")
        self.mount(section)
        # BREAKPOINT keeps orange title — it is the primary visual anchor
        section.mount(Static("[#fb923c]─── 📍 BREAKPOINT ───[/]", classes="detail-section-title"))

        if bp:
            lines = [
                f"  [#fb923c bold]🎯 上次停在这里[/]",
                f"",
                f"  [#fb923c bold]{rich_escape(bp.milestone_label)}[/]",
                f"  {rich_escape(bp.detail)}",
            ]
            if bp.sub_item_label:
                lines.append(f"  {rich_escape(bp.sub_item_label)}")
            if bp.last_topic:
                lines.append(f"")
                lines.append(f"  [#22c55e bold]→ 下一步:[/] {rich_escape(bp.last_topic)}")
            section.mount(Static("\n".join(lines), classes="detail-breakpoint-body"))
        elif self._summary and self._summary.last_context:
            # Fallback from legacy last_context
            ctx = self._summary.last_context
            if len(ctx) > 200:
                ctx = ctx[:197] + "…"
            section.mount(Static(
                f"  [#fb923c bold]🎯 上次停在这里[/]\n\n  {rich_escape(ctx)}",
                classes="detail-breakpoint-body",
            ))
        elif self._last_replies:
            # Ultra-fallback: derive from last reply
            last = self._last_replies[-1]
            snippet = last.replace("\n", " ").strip()
            if len(snippet) > 150:
                snippet = snippet[:147] + "…"
            section.mount(Static(
                f"  [#fb923c bold]🎯 上次停在这里[/]\n\n  [#a8a29e]Last response:[/] {rich_escape(snippet)}",
                classes="detail-breakpoint-body",
            ))
        else:
            section.mount(Static(
                "  [#78716c italic]No breakpoint data available[/]",
                classes="detail-section-body",
            ))

    # ── LAST REPLY ────────────────────────────────────────────────────

    def _mount_last_reply_section(self) -> None:
        """Mount Claude's last reply using Markdown widget."""
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static("[#a8a29e]─── 💬 LAST REPLY ───[/]", classes="detail-section-title"))

        if self._last_replies:
            # Show only the most recent reply (keep it focused)
            reply = self._last_replies[-1]
            content = reply[:600]
            if len(reply) > 600:
                content += "\n\n*…(truncated)*"
            section.mount(Markdown(content, classes="detail-reply-content"))
        else:
            section.mount(Static(
                "  [#78716c italic]No replies loaded yet[/]",
                classes="detail-section-body",
            ))

    # ── Generic section ───────────────────────────────────────────────

    def _mount_section(self, title: str, body: str) -> None:
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static(f"[#a8a29e]─── {rich_escape(title)} ───[/]", classes="detail-section-title"))
        section.mount(Static(body, classes="detail-section-body"))
