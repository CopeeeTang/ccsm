"""Tests for SessionCard shimmer/skeleton loading state."""

from pathlib import Path


def _make_session(sid="test-shimmer"):
    from ccsm.models.session import SessionInfo, Status
    return SessionInfo(
        session_id=sid,
        project_dir="/test",
        jsonl_path=Path(f"/tmp/{sid}.jsonl"),
        message_count=5,
        status=Status.ACTIVE,
        display_name="Test Session",
    )


def test_session_card_loading_attribute():
    """SessionCard should have a loading attribute defaulting to False."""
    from ccsm.tui.widgets.session_card import SessionCard

    card = SessionCard(_make_session())
    assert hasattr(card, 'loading')
    assert card.loading is False


def test_session_card_skeleton_factory():
    """SessionCard.skeleton() should create a loading-state card."""
    from ccsm.tui.widgets.session_card import SessionCard

    card = SessionCard.skeleton()
    assert card.loading is True
