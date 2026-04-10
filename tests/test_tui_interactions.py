"""Integration tests for top-level TUI interactions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

from textual.app import App, ComposeResult

from ccsm.models.session import SessionInfo, Status, Workflow, WorkflowCluster
from ccsm.tui.app import CCSMApp
from ccsm.tui.screens.main import MainScreen
from ccsm.tui.widgets.session_card import SessionCard
from ccsm.tui.widgets.session_list import SessionListPanel
from ccsm.tui.widgets.swimlane import Swimlane


def _make_session(session_id: str, hours_ago: int) -> SessionInfo:
    now = datetime.now(timezone.utc)
    ts = now - timedelta(hours=hours_ago)
    return SessionInfo(
        session_id=session_id,
        project_dir="GUI/main",
        jsonl_path=Path(f"/tmp/{session_id}.jsonl"),
        first_timestamp=ts - timedelta(minutes=5),
        last_timestamp=ts,
        message_count=8,
        user_message_count=4,
        total_user_chars=120,
        status=Status.ACTIVE,
        first_user_content=f"intent for {session_id}",
    )


def test_session_list_supports_keyboard_navigation():
    """Focused session panel should support up/down selection and Enter."""

    sessions = [
        _make_session("session-1", hours_ago=3),
        _make_session("session-2", hours_ago=2),
        _make_session("session-3", hours_ago=1),
    ]

    class SessionListApp(App):
        CSS = "SessionListPanel { width: 80; height: 20; }"

        def __init__(self) -> None:
            super().__init__()
            self.opened_session_id: str | None = None

        def compose(self) -> ComposeResult:
            yield SessionListPanel()

        def on_mount(self) -> None:
            panel = self.query_one(SessionListPanel)
            panel.load_sessions(sessions)
            panel.focus()

        def on_session_list_panel_session_selected(
            self, event: SessionListPanel.SessionSelected
        ) -> None:
            self.opened_session_id = event.session.session_id

    async def run_case() -> None:
        app = SessionListApp()
        async with app.run_test() as pilot:
            panel = app.query_one(SessionListPanel)
            await pilot.pause(0.2)

            cards = list(panel.query(SessionCard))
            assert [card.session.session_id for card in cards[:3]] == [
                "session-3",
                "session-2",
                "session-1",
            ]
            assert panel._selected_id == "session-3"

            await pilot.press("down")
            await pilot.pause(0.1)
            assert panel._selected_id == "session-2"

            await pilot.press("up")
            await pilot.pause(0.1)
            assert panel._selected_id == "session-3"

            await pilot.press("enter")
            await pilot.pause(0.1)
            assert app.opened_session_id == "session-3"

    asyncio.run(run_case())


def test_main_screen_g_toggles_workflow_view(monkeypatch):
    """Main screen should expose the workflow graph view on `g`."""

    monkeypatch.setattr(MainScreen, "_load_data", lambda self: None)

    workflow = Workflow(
        workflow_id="wf-1",
        sessions=["session-1", "session-2"],
        name="workflow one",
        root_session_id="session-1",
        first_timestamp=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        last_timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
    )
    cluster = WorkflowCluster(
        worktree="main",
        project="GUI",
        workflows=[workflow],
        orphans=[],
    )

    async def run_case() -> None:
        app = CCSMApp()
        async with app.run_test() as pilot:
            await pilot.pause(0.5)
            screen = app.screen

            screen._current_sessions = [
                _make_session("session-1", hours_ago=2),
                _make_session("session-2", hours_ago=1),
            ]
            screen._workflow_cluster = cluster

            await pilot.press("g")
            await pilot.pause(0.2)

            swimlane = screen.query_one(Swimlane)
            assert swimlane.display is True
            assert swimlane._cluster is cluster

    asyncio.run(run_case())
