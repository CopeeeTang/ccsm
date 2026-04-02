"""Full-screen dual-axis swimlane timeline.

Horizontal: time (day columns)
Vertical: workflow lanes (one per compact chain)

Replaces session_graph.py with a more informative visualization
that clearly separates compact chains from forks.

Design:
  ● = session start  ◇ = compact continuation  ◆ = fork
  ━ = compact chain link  └─ = fork branch
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.widgets import Static

from ccsm.models.session import Status, Workflow, WorkflowCluster


class Swimlane(Static):
    """Full-screen swimlane timeline widget."""

    DEFAULT_CSS = """
    Swimlane {
        padding: 1 2;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cluster: Optional[WorkflowCluster] = None
        self._statuses: dict[str, Status] = {}
        self._current_session_id: Optional[str] = None

    def set_data(
        self,
        cluster: WorkflowCluster,
        statuses: Optional[dict[str, Status]] = None,
        current_session_id: Optional[str] = None,
    ) -> None:
        self._cluster = cluster
        self._statuses = statuses or {}
        self._current_session_id = current_session_id
        self.update(self._render())

    def _render(self) -> str:
        if not self._cluster or not self._cluster.workflows:
            return "[#78716c]No workflows to display[/]"

        workflows = self._cluster.workflows
        lines: list[str] = []

        # ── Compute time range ───────────────────────────────────
        all_times: list[datetime] = []
        for wf in workflows:
            if wf.first_timestamp:
                all_times.append(wf.first_timestamp)
            if wf.last_timestamp:
                all_times.append(wf.last_timestamp)

        if not all_times:
            return "[#78716c]No timestamp data[/]"

        min_t = min(all_times)
        max_t = max(all_times)
        span = (max_t - min_t).total_seconds()
        if span < 1:
            span = 3600  # Minimum 1 hour span

        # ── Time axis header ─────────────────────────────────────
        LANE_WIDTH = 60
        LABEL_WIDTH = 16

        header = _build_time_header(min_t, max_t, LANE_WIDTH)
        lines.append(f" {header}")
        lines.append("")

        # ── Render each workflow lane ────────────────────────────
        for wf in workflows:
            lane_line = _build_lane(
                wf, min_t, span, LANE_WIDTH,
                self._statuses, self._current_session_id,
            )
            name = rich_escape(wf.display_name)
            has_active = any(
                self._statuses.get(sid) == Status.ACTIVE
                for sid in wf.sessions
            )
            name_fmt = f"[bold #22c55e]{name}[/]" if has_active else f"[#a8a29e]{name}[/]"
            lines.append(f" {lane_line}  {name_fmt}")

            # Fork branches — fork_branches is list[list[str]]
            for branch in wf.fork_branches[:2]:
                fork_label = branch[0][:8] if branch else "?"
                lines.append(f" {'':>{LANE_WIDTH}}  [#60a5fa]└─◆ {fork_label}[/]")

            lines.append("")

        # ── Orphans ──────────────────────────────────────────────
        if self._cluster.orphans:
            lines.append(f" [#78716c]+ {len(self._cluster.orphans)} standalone sessions[/]")

        # ── Legend ───────────────────────────────────────────────
        lines.append("")
        lines.append(" [#78716c]● start  ◇ compact  ◆ fork  ━ chain  [bold #fb923c]● current[/][/]")

        return "\n".join(lines)


def _build_time_header(
    min_t: datetime, max_t: datetime, width: int,
) -> str:
    """Build a time axis header with day markers."""
    span = (max_t - min_t).total_seconds()
    if span < 1:
        span = 3600

    dates: list[datetime] = []
    current = min_t.replace(hour=0, minute=0, second=0, microsecond=0)
    while current <= max_t + timedelta(days=1):
        dates.append(current)
        current += timedelta(days=1)

    axis = [" "] * width
    for d in dates:
        pos = int(((d - min_t).total_seconds() / span) * (width - 1))
        pos = max(0, min(pos, width - 1))
        label = d.strftime("%b %d")
        for i, ch in enumerate(label):
            if pos + i < width:
                axis[pos + i] = ch

    header = "".join(axis)
    sep = [" "] * width
    for d in dates:
        pos = int(((d - min_t).total_seconds() / span) * (width - 1))
        pos = max(0, min(pos, width - 1))
        if pos < width:
            sep[pos] = "│"

    return f"[#78716c]{header}[/]\n [#78716c]{''.join(sep)}[/]"


def _build_lane(
    wf: Workflow,
    min_t: datetime,
    span: float,
    width: int,
    statuses: dict[str, Status],
    current_id: Optional[str],
) -> str:
    """Build a single workflow lane as a character array."""
    lane = [" "] * width

    if not wf.first_timestamp or not wf.last_timestamp:
        return "".join(lane)

    all_sids = wf.sessions
    positions = []

    for i, sid in enumerate(all_sids):
        wf_span = (wf.last_timestamp - wf.first_timestamp).total_seconds()
        if wf_span < 1 or len(all_sids) == 1:
            t_offset = (wf.first_timestamp - min_t).total_seconds()
        else:
            frac = i / (len(all_sids) - 1)
            t_offset = (wf.first_timestamp - min_t).total_seconds() + frac * wf_span

        pos = int((t_offset / span) * (width - 1))
        pos = max(0, min(pos, width - 1))
        positions.append(pos)

        if sid == current_id:
            lane[pos] = "★"
        elif i == 0:
            lane[pos] = "●"
        else:
            lane[pos] = "◇"

    # Fill chain connections
    for j in range(len(positions) - 1):
        start = positions[j] + 1
        end = positions[j + 1]
        for k in range(start, end):
            if k < width and lane[k] == " ":
                lane[k] = "━"

    # Convert to Rich markup
    result_parts: list[str] = []
    for ch in lane:
        if ch == "★":
            result_parts.append("[bold #fb923c]●[/]")
        elif ch == "●":
            result_parts.append("[#22c55e]●[/]")
        elif ch == "◇":
            result_parts.append("[#a78bfa]◇[/]")
        elif ch == "━":
            result_parts.append("[#78716c]━[/]")
        else:
            result_parts.append(ch)

    return "".join(result_parts)
