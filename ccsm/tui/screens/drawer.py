"""Inline detail panel — third column of MainScreen.

Historically this was a ModalScreen overlay; it is now a regular
container widget embedded directly in MainScreen.compose(), so
detail updates follow keyboard cursor without screen stack churn.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.containers import Vertical

from ccsm.tui.widgets.session_detail import SessionDetail


class SessionDetailPanel(Vertical):
    """Third-column detail panel. Shows detail of the cursored session."""

    DEFAULT_CSS = ""  # styles live in claude_native.tcss under #detail-panel

    def compose(self) -> ComposeResult:
        yield SessionDetail()


# Back-compat alias for any legacy import path.
SessionDetailDrawer = SessionDetailPanel
