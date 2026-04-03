"""Right panel: Session detail widget — recovery-first design.

Layout priority (top to bottom):
1. 📋 SESSION — compact metadata (auxiliary top bar)
2. 🧭 MILESTONES — phase progress from compact summary or rule-based (1st priority)
3. 📝 CONTEXT SUMMARY — compact summary's Primary Request + Key Concepts (2nd priority)
4. 📍 WHERE YOU LEFT OFF — last prompt / last user message / last insight (3rd priority)
5. 🔧 WHAT WAS DONE — tool_use operations: edited/ran/read (collapsible)
6. 💬 LAST EXCHANGE — last user+assistant pair (collapsible)

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
    SessionInfo,
    SessionMeta,
    SessionSummary,
    Status,
    Workflow,
    WorkflowCluster,
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

    def show_workflows(
        self,
        cluster: Optional[WorkflowCluster],
        session_statuses: Optional[dict[str, Status]] = None,
    ) -> None:
        """Display workflow overview (when no individual session is selected)."""
        self._session = None  # Clear selected session state
        self.remove_children()
        self._mount_workflow_overview(cluster, session_statuses)

    def show_workflow_detail(
        self,
        workflow: Workflow,
        session_statuses: Optional[dict[str, Status]] = None,
    ) -> None:
        """Display detail for a selected workflow (from swimlane click)."""
        self._session = None  # Clear selected session state
        self.remove_children()
        self._mount_single_workflow_detail(workflow, session_statuses)

    def _rebuild(self) -> None:
        """Rebuild detail panel with recovery-first layout."""
        self.remove_children()
        s = self._session
        if s is None:
            self.mount(Static("Select a session to view details", classes="empty-state"))
            return

        # ── Section 1: Session Identity (det-summary style) ───────
        self._mount_session_section(s)

        # ── Section 2: Milestones (Stepper with guide line) ───────
        self._mount_milestones_section()

        # ── Section 3: Context Summary (2nd priority) ──────────────
        self._mount_context_summary_section()

        # ── Section 4: Where You Left Off (Breakpoint badge) ──────
        self._mount_where_left_off_section()

        # ── Section 5: What Was Done (collapsible) ─────────────────
        self._mount_what_was_done_section()

        # ── Section 6: Last Exchange (Chat bubble) ─────────────────
        self._mount_last_exchange_section()

    # ── SESSION IDENTITY (det-summary: large title + block-quote intent) ──

    def _mount_session_section(self, s: SessionInfo) -> None:
        """Mount session identity with large title and block-quote intent."""
        section = Vertical(classes="detail-section")
        self.mount(section)

        title = s.display_title
        if self._meta and self._meta.name:
            title = self._meta.name
        title = rich_escape(title)

        # Status tag inline
        status_tag, _ = _STATUS_TAGS.get(s.status, (s.status.value, "#78716c"))

        K = "#b0aea5"
        dur = _format_duration(s.duration_seconds)
        msg_info = f"{dur} · {s.message_count} msg"

        model_str = s.model_name or "—"
        if model_str.startswith("claude-"):
            model_str = model_str[7:]

        token_str = ""
        if s.total_input_tokens > 0 or s.total_output_tokens > 0:
            token_str = f" · {_format_tokens(s.total_input_tokens)}↑ {_format_tokens(s.total_output_tokens)}↓"

        running = "  [bold #788c5d]⚡ Running[/]" if s.is_running else ""

        # Large title (brand orange)
        section.mount(Static(
            f"  [#d97757 bold]{title}[/]  {status_tag}{running}",
            classes="detail-section-title",
        ))

        # Compact metadata line
        section.mount(Static(
            f"  [{K}]{msg_info} · {rich_escape(model_str)}{token_str}[/]",
            classes="detail-section-body",
        ))

        # Intent as block-quote (det-summary-intent style)
        ai_intent = self._meta.ai_intent if self._meta else None
        intent_text = ai_intent or (s.first_user_content or "")
        if intent_text:
            content = intent_text.replace("\n", " ").strip()
            if not ai_intent:
                content = _clean_intent_text(content)
            if len(content) > 120:
                content = content[:119] + "…"
            section.mount(Static(
                f"  💡 [{K} italic]\"{rich_escape(content)}\"[/]",
                classes="det-summary-intent",
            ))

    # ── MILESTONES (1st priority) ─────────────────────────────

    def _mount_milestones_section(self) -> None:
        """Mount milestone timeline — Stepper style with left guide line.

        IN_PROGRESS milestones get background highlight for visual focus.
        """
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static(
            "  [#a8a29e]🧭 MILESTONES[/]",
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

    # ── CONTEXT SUMMARY (2nd priority) ────────────────────────

    def _mount_context_summary_section(self) -> None:
        """Mount context summary from compact summary or AI."""
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static(
            "  [#a8a29e]📝 CONTEXT SUMMARY[/]",
            classes="detail-section-title",
        ))

        lines: list[str] = []
        V = "#e8e6dc"
        K = "#b0aea5"

        has_compact = self._compact_parsed and self._compact_parsed.primary_request

        if has_compact:
            cp = self._compact_parsed

            # Primary Request (truncated)
            if cp.primary_request:
                req = cp.primary_request.replace("\n", " ").strip()
                if len(req) > 300:
                    req = req[:297] + "…"
                lines.append(f"  [{V}]{rich_escape(req)}[/]")
                lines.append("")

            # Key Technical Concepts (condensed)
            if cp.key_concepts:
                concepts = cp.key_concepts.strip()
                # Extract first 3 bullet items
                concept_items = []
                for line in concepts.split("\n"):
                    line = line.strip()
                    if line.startswith(("-", "*", "•")):
                        cleaned = _re.sub(r"^[-*•]\s+", "", line).strip()
                        # Extract bold terms
                        bold_match = _re.match(r"\*\*(.+?)\*\*", cleaned)
                        if bold_match:
                            concept_items.append(bold_match.group(1))
                        elif len(cleaned) > 3:
                            concept_items.append(cleaned[:40])
                    if len(concept_items) >= 5:
                        break

                if concept_items:
                    tags = " · ".join(f"[#6a9bcc]{rich_escape(c)}[/]" for c in concept_items)
                    lines.append(f"  [{K}]Concepts[/] {tags}")

        elif self._summary and self._summary.description:
            # Fallback: AI-generated description
            desc = self._summary.description
            if len(desc) > 300:
                desc = desc[:297] + "…"
            lines.append(f"  [{V}]{rich_escape(desc)}[/]")

            # Key insights
            if self._summary.key_insights:
                lines.append("")
                for insight in self._summary.key_insights[:3]:
                    lines.append(f"    [#6a9bcc]•[/] {rich_escape(insight)}")

        else:
            # L3 fallback: use AI intent or first user content as minimal context
            s = self._session
            ai_intent = self._meta.ai_intent if self._meta else None
            fallback_text = ai_intent or (s.first_user_content if s else None)
            if fallback_text:
                content = fallback_text.replace("\n", " ").strip()
                if not ai_intent:
                    content = _clean_intent_text(content)
                if len(content) > 200:
                    content = content[:197] + "…"
                source = "AI" if ai_intent else "首次请求"
                lines.append(f"  [{K}]{source}[/] [{V}]{rich_escape(content)}[/]")
                lines.append("")
                lines.append(f"  [{K} italic]Press [/][bold #d97757]s[/][{K} italic] for detailed AI summary.[/]")
            else:
                lines.append(f"  [{K} italic]No context summary. Press [/][bold #d97757]s[/][{K} italic] to generate.[/]")

        section.mount(Static("\n".join(lines), classes="detail-section-body"))

    # ── WHERE YOU LEFT OFF (Breakpoint badge style) ────────────

    def _mount_where_left_off_section(self) -> None:
        """Mount the breakpoint / last-prompt section with panel border."""
        section = Vertical(classes="detail-section det-breakpoint-section")
        self.mount(section)

        # Breakpoint badge (panel border style with brand orange)
        section.mount(Static(
            " 📍 WHERE YOU LEFT OFF ",
            classes="det-breakpoint-badge",
        ))

        s = self._session
        dd = self._detail_data
        lines: list[str] = []

        # Last prompt (highest value — user's actual last input)
        last_prompt = s.last_prompt if s else None
        last_user = s.last_user_message if s else None

        if last_prompt:
            prompt_text = last_prompt
            if len(prompt_text) > 200:
                prompt_text = prompt_text[:197] + "…"
            lines.append(f"  [#d97757 bold]You asked[/]")
            lines.append(f"  {rich_escape(prompt_text)}")
        elif last_user:
            user_text = last_user
            if len(user_text) > 200:
                user_text = user_text[:197] + "…"
            lines.append(f"  [#d97757 bold]You said[/]")
            lines.append(f"  {rich_escape(user_text)}")

        # Last AI response
        last_ai = dd.last_assistant_msg if dd else None
        if not last_ai and self._last_replies:
            last_ai = self._last_replies[-1]

        if last_ai:
            ai_text = last_ai.replace("\n", " ").strip()
            if len(ai_text) > 200:
                ai_text = ai_text[:197] + "…"
            lines.append("")
            lines.append(f"  [#6a9bcc bold]AI responded[/]")
            lines.append(f"  [#a8a29e]{rich_escape(ai_text)}[/]")

        # Breakpoint from summary
        bp = self._summary.breakpoint if self._summary else None
        if bp and bp.last_topic:
            lines.append("")
            lines.append(f"  [#788c5d bold]→ 下一步[/] {rich_escape(bp.last_topic)}")

        # Last insight from AI summary
        if self._summary and self._summary.key_insights:
            last_insight = self._summary.key_insights[-1]
            if len(last_insight) > 100:
                last_insight = last_insight[:97] + "…"
            lines.append("")
            lines.append(f"  [#6a9bcc]💡 Insight[/] {rich_escape(last_insight)}")

        if not lines:
            lines.append("  [#78716c italic]No breakpoint data available[/]")

        section.mount(Static("\n".join(lines), classes="detail-breakpoint-body"))

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
            lines.append(f"  [#788c5d]📝 Edited[/]  [{V}]{', '.join(file_names)}{suffix}[/]")

        if dd.commands_run:
            shown = dd.commands_run[-5:]  # Last 5 commands
            for cmd in shown:
                cmd_short = cmd[:60] + "…" if len(cmd) > 60 else cmd
                lines.append(f"  [#facc15]⚡ Ran[/]     [{V}]{rich_escape(cmd_short)}[/]")

        if dd.files_read:
            shown = dd.files_read[:5]
            file_names = [_re.sub(r".*/", "", f) for f in shown]
            extra = len(dd.files_read) - len(shown)
            suffix = f" (+{extra})" if extra > 0 else ""
            lines.append(f"  [#6a9bcc]📖 Read[/]    [{V}]{', '.join(file_names)}{suffix}[/]")

        if dd.agents_spawned:
            for desc in dd.agents_spawned[:3]:
                desc_short = desc[:50] + "…" if len(desc) > 50 else desc
                lines.append(f"  [#a855f7]🤖 Agent[/]  [{V}]{rich_escape(desc_short)}[/]")

        body = "\n".join(lines)

        collapsible = Collapsible(title="🔧 WHAT WAS DONE", collapsed=True)
        self.mount(collapsible)
        collapsible.mount(Static(body, classes="detail-section-body"))

    # ── LAST EXCHANGE (Chat bubble style) ──────────────────────

    def _mount_last_exchange_section(self) -> None:
        """Mount last user+assistant message pair — chat bubble style."""
        from textual.containers import Horizontal

        dd = self._detail_data
        has_exchange = dd and (dd.last_user_msg or dd.last_assistant_msg)
        has_reply = bool(self._last_replies)

        if not has_exchange and not has_reply:
            return

        collapsible = Collapsible(title="💬 LAST EXCHANGE", collapsed=True)
        self.mount(collapsible)

        V = "#e8e6dc"
        K = "#b0aea5"

        # User message bubble
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

        # AI message bubble
        ai_text = None
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

    # ── Workflow overview (no session selected) ──────────────────

    def _mount_workflow_overview(
        self,
        cluster: Optional[WorkflowCluster],
        session_statuses: Optional[dict[str, Status]] = None,
    ) -> None:
        """Mount workflow overview section."""
        from ccsm.tui.widgets.workflow_list import render_workflow_list

        section = Vertical(classes="detail-section")
        self.mount(section)

        count = len(cluster.workflows) if cluster else 0
        title = f"🔗 WORKFLOWS ({count})"
        section.mount(Static(
            f"  [#a8a29e]{title}[/]",
            classes="detail-section-title",
        ))

        body = render_workflow_list(cluster, session_statuses)
        section.mount(Static(body, classes="detail-section-body"))

    def _mount_single_workflow_detail(
        self,
        workflow: Workflow,
        session_statuses: Optional[dict[str, Status]] = None,
    ) -> None:
        """Mount detail for a single selected workflow."""
        statuses = session_statuses or {}

        # Title
        name = rich_escape(workflow.display_name)
        has_active = any(
            statuses.get(sid) == Status.ACTIVE
            for sid in workflow.sessions
        )
        if has_active:
            title_fmt = f"[bold #788c5d]{name}[/]"
        else:
            title_fmt = f"[#e8e6dc bold]{name}[/]"

        K = "#b0aea5"
        V = "#e8e6dc"

        lines = [
            f"  [{K}]Workflow[/] {title_fmt}",
            f"  [{K}]Sessions[/] [{V}]{workflow.session_count}[/]    "
            f"[{K}]Duration[/] [{V}]{_format_duration(workflow.duration_seconds)}[/]",
        ]

        if workflow.first_timestamp:
            ts_str = workflow.first_timestamp.strftime("%Y-%m-%d %H:%M")
            lines.append(f"  [{K}]Started [/] [{V}]{ts_str}[/]")

        if workflow.fork_branches:
            lines.append(f"  [{K}]Forks   [/] [{V}]{len(workflow.fork_branches)}[/]")

        self._mount_section("🔗 WORKFLOW", "\n".join(lines))

        # Session list within workflow
        session_lines: list[str] = []
        for i, sid in enumerate(workflow.sessions):
            status = statuses.get(sid, Status.DONE)
            tag_markup, _ = _STATUS_TAGS.get(status, ("○ Done", "#78716c"))
            prefix = "━●" if i == 0 else "━◇"
            session_lines.append(
                f"  [#78716c]{prefix}[/] [{V}]{sid[:12]}[/]  {tag_markup}"
            )

        # Fork sessions
        for branch in workflow.fork_branches:
            for sid in branch:
                status = statuses.get(sid, Status.DONE)
                tag_markup, _ = _STATUS_TAGS.get(status, ("○ Done", "#78716c"))
                session_lines.append(
                    f"  [#6a9bcc]  └─◆[/] [{V}]{sid[:12]}[/]  {tag_markup}"
                )

        if session_lines:
            self._mount_section("📋 SESSIONS", "\n".join(session_lines))

    # ── Generic section ──────────────────────────────────────────

    def _mount_section(self, title: str, body: str) -> None:
        section = Vertical(classes="detail-section")
        self.mount(section)
        section.mount(Static(
            f"[#a8a29e]─── {rich_escape(title)} ───[/]",
            classes="detail-section-title",
        ))
        section.mount(Static(body, classes="detail-section-body"))
