"""Test that detail panel is inline (not modal) in three-column layout."""
import pytest
from ccsm.tui.app import CCSMApp
from ccsm.tui.screens.main import MainScreen


@pytest.mark.asyncio
async def test_detail_panel_is_inline_not_modal():
    """Detail panel should be a regular widget inside MainScreen, not a ModalScreen."""
    async with CCSMApp().run_test() as pilot:
        await pilot.pause()  # Wait for on_mount push_screen
        from ccsm.tui.screens.drawer import SessionDetailPanel
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        # query_one should find the panel directly on the active screen
        panel = screen.query_one(SessionDetailPanel)
        assert panel is not None
        # panel should be inside MainScreen, not pushed onto screen stack
        assert panel.screen is screen


@pytest.mark.asyncio
async def test_three_column_layout_widths():
    """Three panels should all be present and visible."""
    async with CCSMApp().run_test() as pilot:
        await pilot.pause()  # Wait for on_mount push_screen
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        worktree = screen.query_one("#worktree-panel")
        sessions = screen.query_one("#session-panel")
        detail = screen.query_one("#detail-panel")
        # All three present and visible
        assert worktree.display is True
        assert sessions.display is True
        assert detail.display is True


@pytest.mark.asyncio
async def test_session_detail_queryable_by_id():
    """SessionDetail widget should be queryable via #detail-content."""
    async with CCSMApp().run_test() as pilot:
        await pilot.pause()  # Wait for on_mount push_screen
        screen = pilot.app.screen
        assert isinstance(screen, MainScreen)
        from ccsm.tui.widgets.session_detail import SessionDetail
        detail = screen.query_one("#detail-content", SessionDetail)
        assert detail is not None
