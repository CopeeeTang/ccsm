"""Session DAG visualization widget.

Renders a tree/DAG of session relationships using Unicode box-drawing.
Nodes are sessions, edges represent fork/compact/duplicate relationships.
Time flows top-to-bottom, with the mainline (ROOT sessions) on the left.

Example output:
  ● fix-login-bug                    Apr 1 10:00
  │
  ├── ◆ add-auth (fork)              Apr 1 12:00
  │   └── ◇ auth-debug (compact)     Apr 1 14:00
  │
  ├── ◉ fix-login-v2 (dup)           Apr 1 10:28
  │
  └── ● refactor-api                 Apr 2 09:00
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from rich.text import Text
from textual.widgets import Static

from ccsm.models.session import LineageType, SessionLineage


class SessionGraph(Static):
    """Renders a session lineage tree as Unicode art."""

    DEFAULT_CSS = """
    SessionGraph {
        padding: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._nodes: list[_GraphNode] = []

    def set_data(
        self,
        lineages: dict[str, SessionLineage],
        titles: dict[str, str],
        timestamps: dict[str, Optional[datetime]],
        current_session_id: Optional[str] = None,
    ) -> None:
        """Build graph from lineage data.

        Args:
            lineages: session_id -> SessionLineage mapping
            titles: session_id -> display title
            timestamps: session_id -> last_message_at
            current_session_id: highlight this node
        """
        self._nodes = _build_tree(lineages, titles, timestamps, current_session_id)
        self.update(self._render())

    def _render(self) -> str:
        if not self._nodes:
            return "[#78716c]No lineage data available[/]"

        lines: list[str] = []
        for node in self._nodes:
            indent = node.indent_str
            icon = _type_icon(node.lineage_type)
            highlight = "[bold #fb923c]" if node.is_current else ""
            end_hl = "[/]" if node.is_current else ""
            time_str = node.time_str or ""

            badge = ""
            if node.lineage_type == LineageType.FORK:
                badge = " [#60a5fa]fork[/]"
            elif node.lineage_type == LineageType.COMPACT:
                badge = " [#a78bfa]compact[/]"
            elif node.lineage_type == LineageType.DUPLICATE:
                badge = " [#f87171]dup[/]"

            title = node.title[:30] if node.title else node.session_id[:8]
            line = f"{indent}{icon} {highlight}{title}{end_hl}{badge}"
            # Right-align timestamp: estimate visible width from Text markup
            try:
                visible_len = len(Text.from_markup(line).plain)
            except Exception:
                visible_len = len(line) // 2
            pad = max(1, 60 - visible_len)
            line += " " * pad + f"[#78716c]{time_str}[/]"
            lines.append(line)

            # Draw connector to next sibling/child
            if node.has_children:
                lines.append(f"{indent}│")

        return "\n".join(lines)


class _GraphNode:
    """Internal node for rendering."""

    __slots__ = (
        "session_id",
        "title",
        "lineage_type",
        "depth",
        "indent_str",
        "time_str",
        "is_current",
        "has_children",
    )

    def __init__(
        self,
        session_id: str,
        title: str,
        lineage_type: LineageType,
        depth: int,
        indent_str: str,
        time_str: Optional[str],
        is_current: bool,
        has_children: bool,
    ) -> None:
        self.session_id = session_id
        self.title = title
        self.lineage_type = lineage_type
        self.depth = depth
        self.indent_str = indent_str
        self.time_str = time_str
        self.is_current = is_current
        self.has_children = has_children


def _build_tree(
    lineages: dict[str, SessionLineage],
    titles: dict[str, str],
    timestamps: dict[str, Optional[datetime]],
    current_id: Optional[str],
) -> list[_GraphNode]:
    """Flatten DAG into ordered list of _GraphNode for rendering."""
    # Find roots (no parent)
    roots = [sid for sid, lin in lineages.items() if lin.parent_id is None]
    # Sort roots by timestamp
    roots.sort(key=lambda s: timestamps.get(s) or datetime.min.replace(tzinfo=timezone.utc))

    nodes: list[_GraphNode] = []

    def _walk(sid: str, depth: int, prefix: str, is_last: bool) -> None:
        lin = lineages.get(sid)
        if not lin:
            return
        children = [c for c in lin.children if c in lineages]
        children.sort(key=lambda c: timestamps.get(c) or datetime.min.replace(tzinfo=timezone.utc))

        # Build indent string
        if depth == 0:
            indent = ""
        elif is_last:
            indent = prefix + "└── "
        else:
            indent = prefix + "├── "

        # Time string
        ts = timestamps.get(sid)
        time_str = ts.strftime("%b %d %H:%M") if ts else None

        nodes.append(
            _GraphNode(
                session_id=sid,
                title=titles.get(sid, sid[:8]),
                lineage_type=lin.lineage_type,
                depth=depth,
                indent_str=indent,
                time_str=time_str,
                is_current=(sid == current_id),
                has_children=bool(children),
            )
        )

        # Recurse children
        child_prefix = prefix + ("    " if is_last else "│   ") if depth > 0 else ""
        for i, child in enumerate(children):
            _walk(child, depth + 1, child_prefix, i == len(children) - 1)

    for i, root in enumerate(roots):
        _walk(root, 0, "", i == len(roots) - 1)

    return nodes


def _type_icon(lt: LineageType) -> str:
    """Unicode icon for each lineage type."""
    return {
        LineageType.ROOT: "●",
        LineageType.FORK: "◆",
        LineageType.COMPACT: "◇",
        LineageType.DUPLICATE: "◉",
    }.get(lt, "○")
