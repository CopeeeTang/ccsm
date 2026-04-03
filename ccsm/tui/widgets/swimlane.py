"""Compact swimlane timeline for middle panel or full-screen.

Horizontal: time (day columns)
Vertical: workflow lanes (one per compact chain)

Works in both:
  - Middle panel (narrow, ~35% width) — compact single-line lanes
  - Full-screen (wide) — expanded lanes with fork branches

Design:
  ● = session start  ◇ = compact continuation  ◆ = fork
  ━ = compact chain link  └─ = fork branch
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.message import Message
from textual.widgets import Static

from ccsm.models.session import Status, Workflow, WorkflowCluster

# Status tags for swimlane labels
_STATUS_TAGS = {
    Status.ACTIVE: ("●", "#22c55e"),
    Status.BACKGROUND: ("◐", "#3b82f6"),
    Status.IDEA: ("◇", "#a855f7"),
    Status.DONE: ("○", "#78716c"),
    Status.NOISE: ("·", "#44403c"),
}


class Swimlane(Static):
    """Swimlane timeline widget — supports narrow and wide modes."""

    DEFAULT_CSS = """
    Swimlane {
        padding: 1 1;
    }
    """

    class WorkflowSelected(Message):
        """Emitted when a workflow lane is clicked."""

        def __init__(self, workflow: Workflow) -> None:
            self.workflow = workflow
            super().__init__()

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._cluster: Optional[WorkflowCluster] = None
        self._statuses: dict[str, Status] = {}
        self._current_session_id: Optional[str] = None
        self._compact_mode: bool = False  # True when embedded in narrow panel
        self._lane_y_map: list[tuple[int, int, Workflow]] = []  # (y_start, y_end, workflow)

    def set_data(
        self,
        cluster: WorkflowCluster,
        statuses: Optional[dict[str, Status]] = None,
        current_session_id: Optional[str] = None,
        compact: bool = False,
    ) -> None:
        self._cluster = cluster
        self._statuses = statuses or {}
        self._current_session_id = current_session_id
        self._compact_mode = compact
        self.update(self._render_content())

    def _render_content(self) -> str:
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

        # ── Adaptive width based on mode ────────────────────────
        available_width = self.size.width or 60
        if self._compact_mode:
            LANE_WIDTH = max(20, available_width - 4)  # Narrow: use most of available width
        else:
            LANE_WIDTH = max(40, min(80, available_width - 20))

        # ── Time axis header ─────────────────────────────────────
        header = _build_time_header(min_t, max_t, LANE_WIDTH)
        lines.append(f" {header}")
        lines.append("")

        # ── Render each workflow lane ────────────────────────────
        self._lane_y_map = []
        current_line = len(lines)  # Track y position

        for wf in workflows:
            lane_start_y = current_line
            lane_line = _build_lane(
                wf, min_t, span, LANE_WIDTH,
                self._statuses, self._current_session_id,
            )

            # Determine dominant status for this workflow
            wf_status = self._get_workflow_status(wf)
            tag_icon, tag_color = _STATUS_TAGS.get(wf_status, ("○", "#78716c"))

            # Prefer AI name over auto-generated name
            name = rich_escape(wf.display_name)
            if wf_status == Status.ACTIVE:
                name_fmt = f"[bold #22c55e]{name}[/]"
            else:
                name_fmt = f"[#a8a29e]{name}[/]"

            # Session count
            count_info = f"[#78716c]{wf.session_count}s[/]"

            if self._compact_mode:
                # Compact: lane on one line, name + tag below
                lines.append(f" {lane_line}")
                current_line += 1
                lines.append(f"   [{tag_color}]{tag_icon}[/] {name_fmt}  {count_info}")
                current_line += 1
            else:
                # Full: lane + name + tag on same line
                lines.append(
                    f" {lane_line}  [{tag_color}]{tag_icon}[/] {name_fmt}  {count_info}"
                )
                current_line += 1

            # Fork branches (compact mode: skip, full mode: show first 2)
            if not self._compact_mode:
                for branch in wf.fork_branches[:2]:
                    fork_label = rich_escape(branch[0][:8]) if branch else "?"
                    lines.append(
                        f" {'':>{LANE_WIDTH}}  [#60a5fa]└─◆ {fork_label}[/]"
                    )
                    current_line += 1

            # Record y range for this workflow
            self._lane_y_map.append((lane_start_y, current_line, wf))

            lines.append("")
            current_line += 1

        # ── Orphans ──────────────────────────────────────────────
        if self._cluster.orphans:
            lines.append(
                f" [#78716c]+ {len(self._cluster.orphans)} standalone sessions[/]"
            )

        # ── Legend ───────────────────────────────────────────────
        lines.append("")
        if self._compact_mode:
            lines.append(
                " [#78716c]● start  ◇ compact  [bold #fb923c]● current[/][/]"
            )
        else:
            lines.append(
                " [#78716c]● start  ◇ compact  ◆ fork  ━ chain  "
                "[bold #fb923c]● current[/][/]"
            )

        return "\n".join(lines)

    def _get_workflow_status(self, wf: Workflow) -> Status:
        """Determine the dominant status of a workflow."""
        all_sids = list(wf.sessions)
        for branch in wf.fork_branches:
            all_sids.extend(branch)

        # Priority: ACTIVE > BACKGROUND > IDEA > DONE
        for status in [Status.ACTIVE, Status.BACKGROUND, Status.IDEA, Status.DONE]:
            if any(self._statuses.get(sid) == status for sid in all_sids):
                return status
        return Status.DONE

    def on_click(self, event) -> None:
        """Map click y-coordinate to workflow lane and emit WorkflowSelected."""
        if not self._lane_y_map:
            return
        # Account for padding (1 line top padding from CSS)
        y = event.y
        for y_start, y_end, wf in self._lane_y_map:
            if y_start <= y < y_end:
                self.post_message(self.WorkflowSelected(wf))
                return

    def on_resize(self, event) -> None:
        """Re-render on resize to adapt lane widths."""
        if self._cluster:
            self.update(self._render_content())


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
