"""Folded workflow list widget for the detail panel.

Renders compact workflow summaries with compact-chain previews,
fork branches, and time spans. Designed as a section within
SessionDetail, not a standalone screen.

Visual design:
  ━● 登录系统                   3 sessions
    fix-login → c1 → c2
                 └─ auth (fork)
    Apr 1 10:00 — Apr 1 16:00        6h
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.widgets import Static

from ccsm.models.session import (
    Status,
    Workflow,
    WorkflowCluster,
)


def render_workflow_list(
    cluster: Optional[WorkflowCluster],
    session_statuses: Optional[dict[str, Status]] = None,
) -> str:
    """Render a WorkflowCluster as Rich markup text.

    Args:
        cluster: The workflow cluster to render
        session_statuses: Optional {session_id: Status} for active indicators

    Returns:
        Rich markup string for mounting in a Static widget
    """
    if not cluster or not cluster.workflows:
        return "  [#78716c italic]No workflows detected yet[/]"

    statuses = session_statuses or {}
    lines: list[str] = []

    for wf in cluster.workflows:
        # Determine if workflow has any active session
        # fork_branches is list[list[str]], flatten for status check
        fork_sids = [s for branch in wf.fork_branches for s in branch]
        has_active = any(
            statuses.get(sid) == Status.ACTIVE
            for sid in list(wf.sessions) + fork_sids
        )
        icon = "[#22c55e]●[/]" if has_active else "[#78716c]○[/]"

        # Title line: icon + name + session count
        name = rich_escape(wf.display_name)
        count_str = f"{wf.session_count} session{'s' if wf.session_count != 1 else ''}"

        name_display = f"[bold]{name}[/]" if has_active else name
        lines.append(f" ━{icon} {name_display}")

        # Chain preview: title1 → title2 → title3
        if wf.name:
            chain_text = wf.name
            if len(chain_text) > 50:
                chain_text = chain_text[:47] + "…"
            lines.append(f"   [#a8a29e]{rich_escape(chain_text)}[/]")

        # Fork branches (if any) — fork_branches is list[list[str]]
        for branch in wf.fork_branches[:3]:
            fork_label = branch[0][:8] if branch else "?"
            branch_len = f" ({len(branch)} sessions)" if len(branch) > 1 else ""
            lines.append(f"   [#a8a29e]        └─[/] [#60a5fa]{rich_escape(fork_label)}{branch_len}[/] [#60a5fa](fork)[/]")
        if len(wf.fork_branches) > 3:
            lines.append(f"   [#a8a29e]        … +{len(wf.fork_branches) - 3} more forks[/]")

        # Time span
        time_str = _format_span(wf.first_timestamp, wf.last_timestamp)
        dur_str = _format_duration_short(wf.duration_seconds)
        right = f"[#78716c]{count_str}[/]"
        if dur_str:
            right += f"  [#78716c]{dur_str}[/]"

        lines.append(f"   [#78716c]{time_str}[/]    {right}")
        lines.append("")  # blank line between workflows

    # Orphans
    if cluster.orphans:
        lines.append(f" [#78716c]+ {len(cluster.orphans)} standalone session{'s' if len(cluster.orphans) != 1 else ''}[/]")

    return "\n".join(lines)


def _format_span(
    first: Optional[datetime],
    last: Optional[datetime],
) -> str:
    if first is None:
        return "—"
    fmt = "%b %d %H:%M"
    if last and first.date() == last.date():
        return f"{first.strftime(fmt)} — {last.strftime('%H:%M')}"
    if last:
        return f"{first.strftime(fmt)} — {last.strftime(fmt)}"
    return first.strftime(fmt)


def _format_duration_short(seconds: Optional[float]) -> str:
    if seconds is None:
        return ""
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    hours = int(seconds / 3600)
    if hours < 24:
        return f"{hours}h"
    days = hours // 24
    return f"{days}d"
