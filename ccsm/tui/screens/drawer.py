"""Session Detail Drawer — ModalScreen overlay for session detail.

Replaces the fixed right panel with a 66%-width right-aligned drawer
that opens when a session card is selected and closes with Escape.

The drawer embeds the existing SessionDetail widget, keeping all
6-zone data rendering logic from Round 10 intact.
"""

from __future__ import annotations

from typing import Optional

from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from ccsm.models.session import (
    CompactSummaryParsed,
    SessionDetailData,
    SessionInfo,
    SessionMeta,
    SessionSummary,
    Status,
    Workflow,
    WorkflowCluster,
)
from ccsm.tui.widgets.session_detail import SessionDetail


class SessionDetailDrawer(ModalScreen):
    """Right-side drawer overlay for session detail.

    Usage:
        drawer = SessionDetailDrawer()
        self.app.push_screen(drawer)
        drawer.show_session(session, meta=meta, summary=summary, ...)
    """

    BINDINGS = [
        Binding("escape", "dismiss_drawer", "Close", show=False),
        Binding("g", "toggle_graph", "View", show=False),
        Binding("r", "resume_session", "Resume", show=False),
        Binding("s", "summarize_llm", "AI", show=False),
        Binding("D", "batch_archive", "Archive", show=False),
    ]

    DEFAULT_CSS = """
    SessionDetailDrawer {
        align: right middle;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._pending_show: Optional[dict] = None

    def compose(self):
        with Vertical(id="drawer-panel"):
            yield Static(
                " [#d97757 bold]DETAIL[/]  [#78716c]Esc to close[/]",
                classes="drawer-title",
            )
            yield SessionDetail()

    def on_mount(self) -> None:
        """Apply pending show_session call if data was set before mount."""
        if self._pending_show is not None:
            self._apply_show(**self._pending_show)
            self._pending_show = None

    def show_session(
        self,
        session: SessionInfo,
        meta: Optional[SessionMeta] = None,
        summary: Optional[SessionSummary] = None,
        last_replies: Optional[list[str]] = None,
        detail_data: Optional[SessionDetailData] = None,
        compact_parsed: Optional[CompactSummaryParsed] = None,
    ) -> None:
        """Display session detail in the drawer.

        If called before mount (common with async loading), the data is
        queued and applied in on_mount.
        """
        kwargs = dict(
            session=session,
            meta=meta,
            summary=summary,
            last_replies=last_replies,
            detail_data=detail_data,
            compact_parsed=compact_parsed,
        )
        try:
            self._apply_show(**kwargs)
        except Exception:
            # Widget tree not ready yet — queue for on_mount
            self._pending_show = kwargs

    def _apply_show(self, **kwargs) -> None:
        """Actually call SessionDetail.show_session()."""
        detail = self.query_one(SessionDetail)
        detail.show_session(**kwargs)

    def show_workflows(
        self,
        cluster: Optional[WorkflowCluster],
        session_statuses: Optional[dict[str, Status]] = None,
    ) -> None:
        """Display workflow overview in the drawer."""
        try:
            detail = self.query_one(SessionDetail)
            detail.show_workflows(cluster, session_statuses)
        except Exception:
            pass

    def show_workflow_detail(
        self,
        workflow: Workflow,
        session_statuses: Optional[dict[str, Status]] = None,
    ) -> None:
        """Display single workflow detail in the drawer."""
        try:
            detail = self.query_one(SessionDetail)
            detail.show_workflow_detail(workflow, session_statuses)
        except Exception:
            pass

    def action_dismiss_drawer(self) -> None:
        """Close the drawer and return to session list."""
        self.dismiss()

    def action_toggle_graph(self) -> None:
        """Delegate view toggle to MainScreen (close drawer first)."""
        self.dismiss()
        # After dismiss, MainScreen is active — trigger its action
        self.app.call_after_refresh(
            lambda: self.app.screen.action_toggle_graph()
            if hasattr(self.app.screen, "action_toggle_graph") else None
        )

    def action_resume_session(self) -> None:
        """Delegate resume to MainScreen."""
        self.dismiss()
        self.app.call_after_refresh(
            lambda: self.app.screen.action_resume_session()
            if hasattr(self.app.screen, "action_resume_session") else None
        )

    def action_summarize_llm(self) -> None:
        """Delegate AI summary to MainScreen."""
        self.dismiss()
        self.app.call_after_refresh(
            lambda: self.app.screen.action_summarize_llm()
            if hasattr(self.app.screen, "action_summarize_llm") else None
        )

    def action_batch_archive(self) -> None:
        """Delegate archive to MainScreen."""
        self.dismiss()
        self.app.call_after_refresh(
            lambda: self.app.screen.action_batch_archive()
            if hasattr(self.app.screen, "action_batch_archive") else None
        )
