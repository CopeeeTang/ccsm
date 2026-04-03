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

import os
from datetime import datetime, timezone
from typing import Optional

from rich.markup import escape as rich_escape
from textual.message import Message
from textual.widgets import Static

from ccsm.models.session import Status, Workflow, WorkflowCluster

# Status tags for swimlane labels
_STATUS_TAGS = {
    Status.ACTIVE: "A",
    Status.BACKGROUND: "B",
    Status.IDEA: "I",
    Status.DONE: "D",
    Status.NOISE: "N",
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
        self._needs_render: bool = False  # Deferred rendering flag

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
        self._needs_render = True
        # Render only when mounted; otherwise on_mount will pick it up.
        if self.is_mounted:
            self.call_after_refresh(self._try_render)

    def _try_render(self) -> None:
        """Render content if we have valid dimensions and data."""
        if not self._needs_render or not self._cluster:
            return
        # Wait until layout establishes a non-zero width.
        if self.size.width <= 0:
            return
        content = self._render_content()
        self._apply_content(content)
        self._needs_render = False

    def _apply_content(self, content: str) -> None:
        """Update renderable and force a concrete height for live TTY layout.

        In the real terminal, `Static.update()` can keep the widget at its
        initial 1-line auto height when content is filled after mount. That
        leaves swimlane mode looking blank even though the renderable exists.
        """
        line_count = max(1, content.count("\n") + 1)
        # Vertical padding is 1 top + 1 bottom in both widget CSS blocks.
        self.styles.height = line_count + 2
        self.update(content)
        self.refresh(layout=True)
        self._debug_dump(content)

    def _debug_dump(self, content: str) -> None:
        """Optionally dump rendered swimlane content for terminal debugging."""
        path = os.getenv("CCSM_DEBUG_RENDER_FILE")
        if not path:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            preview = content[:2000]
            with open(path, "a", encoding="utf-8") as f:
                f.write(
                    f"\n=== {now} ===\n"
                    f"mounted={self.is_mounted} size={self.size} "
                    f"content_lines={content.count(chr(10)) + 1}\n"
                    f"{preview}\n"
                )
        except Exception:
            # Debug logging must never affect UI behavior.
            pass

    def on_mount(self) -> None:
        """Ensure content is rendered after mount."""
        if self._cluster:
            self.call_after_refresh(self._try_render)

    def _deferred_render(self) -> None:
        """Re-render after layout has settled."""
        self._needs_render = True
        self._try_render()

    def _render_content(self) -> str:
        if not self._cluster:
            return "No workflows to display"
        content, lane_map = render_swimlane_text(
            self._cluster,
            statuses=self._statuses,
            current_session_id=self._current_session_id,
            compact=self._compact_mode,
            available_width=self.size.width or 60,
        )
        self._lane_y_map = lane_map
        return content

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
            self._needs_render = True
            self._try_render()


def _build_time_header(
    min_t: datetime, max_t: datetime, width: int,
) -> str:
    """Build a clean non-overlapping time header."""
    width = max(12, width)
    start = min_t.strftime("%m-%d %H:%M")
    end = max_t.strftime("%m-%d %H:%M")
    label = f"{start} -> {end}"
    if len(label) > width:
        label = f"{min_t.strftime('%m-%d')} -> {max_t.strftime('%m-%d')}"
    if len(label) > width:
        label = label[: width - 1] + "…"
    line1 = label.ljust(width)
    line2 = "|" + "-" * (width - 2) + "|" if width >= 3 else "-" * width
    return f"{line1}\n {line2}"


def render_swimlane_text(
    cluster: WorkflowCluster,
    statuses: Optional[dict[str, Status]] = None,
    current_session_id: Optional[str] = None,
    compact: bool = True,
    available_width: int = 60,
) -> tuple[str, list[tuple[int, int, Workflow]]]:
    """Render swimlane into plain text plus lane map.

    This helper is reused by both the Swimlane widget and a Static fallback path.
    """
    statuses = statuses or {}
    workflows = cluster.workflows
    if not workflows:
        return "No workflows to display", []

    all_times: list[datetime] = []
    for wf in workflows:
        if wf.first_timestamp:
            all_times.append(wf.first_timestamp)
        if wf.last_timestamp:
            all_times.append(wf.last_timestamp)
    if not all_times:
        return "No timestamp data", []

    min_t = min(all_times)
    max_t = max(all_times)
    span = (max_t - min_t).total_seconds()
    if span < 1:
        span = 3600

    if compact:
        lane_width = max(20, available_width - 4)
    else:
        lane_width = max(40, min(80, available_width - 20))

    lines: list[str] = []
    lane_map: list[tuple[int, int, Workflow]] = []
    lines.append(f" {_build_time_header(min_t, max_t, lane_width)}")
    lines.append("")
    current_line = len(lines)

    for wf in workflows:
        lane_start_y = current_line
        lane_line = _build_lane(wf, min_t, span, lane_width, statuses, current_session_id)

        # Determine dominant status for label icon
        all_sids = list(wf.sessions)
        for branch in wf.fork_branches:
            all_sids.extend(branch)
        wf_status = Status.DONE
        for status in [Status.ACTIVE, Status.BACKGROUND, Status.IDEA, Status.DONE]:
            if any(statuses.get(sid) == status for sid in all_sids):
                wf_status = status
                break
        tag_icon = _STATUS_TAGS.get(wf_status, "D")

        name_raw = (wf.display_name or "").replace("\n", " ").strip()
        max_name_len = max(14, lane_width - 18) if compact else 64
        if len(name_raw) > max_name_len:
            name_raw = name_raw[: max_name_len - 1] + "…"
        name_raw = rich_escape(name_raw)
        count_info = f"{wf.session_count}s"

        if compact:
            lines.append(f" {lane_line}")
            current_line += 1
            lines.append(f"   {tag_icon} {name_raw}  {count_info}")
            current_line += 1
        else:
            lines.append(f" {lane_line}  {tag_icon} {name_raw}  {count_info}")
            current_line += 1
            for branch in wf.fork_branches[:2]:
                fork_label = rich_escape(branch[0][:8]) if branch else "?"
                lines.append(f" {'':>{lane_width}}  ->x {fork_label}")
                current_line += 1

        lane_map.append((lane_start_y, current_line, wf))
        lines.append("")
        current_line += 1

    if cluster.orphans:
        lines.append(f" + {len(cluster.orphans)} standalone sessions")
    lines.append("")
    if compact:
        lines.append(" o start  + compact  * current")
    else:
        lines.append(" o start  + compact  x fork  - chain  * current")

    return "\n".join(lines), lane_map


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
            lane[pos] = "*"
        elif i == 0:
            lane[pos] = "o"
        else:
            lane[pos] = "+"

    # Fill chain connections
    for j in range(len(positions) - 1):
        start = positions[j] + 1
        end = positions[j + 1]
        for k in range(start, end):
            if k < width and lane[k] == " ":
                lane[k] = "-"

    return "".join(lane)
