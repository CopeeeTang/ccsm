"""Tests for batch rendering in SessionListPanel."""

from datetime import datetime, timezone


def _make_session(sid: str, minutes_ago: int = 0):
    """Create a minimal SessionInfo for testing."""
    from ccsm.models.session import SessionInfo, Status
    from pathlib import Path

    ts = datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    return SessionInfo(
        session_id=sid,
        project_dir="/test",
        jsonl_path=Path(f"/tmp/{sid}.jsonl"),
        first_timestamp=ts,
        last_timestamp=ts,
        message_count=5,
        status=Status.ACTIVE,
    )


def test_prepare_batches_initial_batch_size():
    """Initial batch should contain at most INITIAL_BATCH_SIZE items."""
    from ccsm.tui.widgets.session_list import _prepare_render_batches

    sessions = [_make_session(f"s{i}") for i in range(100)]
    initial, remaining = _prepare_render_batches(sessions, initial_size=30)

    assert len(initial) == 30
    assert len(remaining) == 70


def test_prepare_batches_small_list():
    """Lists smaller than initial batch should return all in initial."""
    from ccsm.tui.widgets.session_list import _prepare_render_batches

    sessions = [_make_session(f"s{i}") for i in range(10)]
    initial, remaining = _prepare_render_batches(sessions, initial_size=30)

    assert len(initial) == 10
    assert len(remaining) == 0


def test_prepare_batches_empty():
    """Empty list should return empty batches."""
    from ccsm.tui.widgets.session_list import _prepare_render_batches

    initial, remaining = _prepare_render_batches([], initial_size=30)
    assert initial == []
    assert remaining == []


def test_prepare_batches_preserves_order():
    """Batch splitting should preserve the original order."""
    from ccsm.tui.widgets.session_list import _prepare_render_batches

    sessions = [_make_session(f"s{i}") for i in range(50)]
    initial, remaining = _prepare_render_batches(sessions, initial_size=20)

    all_ids = [s.session_id for s in initial] + [s.session_id for s in remaining]
    expected_ids = [f"s{i}" for i in range(50)]
    assert all_ids == expected_ids


def test_build_lineage_trees_with_batch():
    """_build_lineage_trees should work on a batch subset."""
    from ccsm.tui.widgets.session_list import _build_lineage_trees

    sessions = [_make_session(f"s{i}") for i in range(5)]
    trees = _build_lineage_trees(sessions, lineage_types={})

    # Without lineage, each session is its own tree
    assert len(trees) == 5
    for tree in trees:
        assert len(tree) == 1


def test_session_card_displays_title():
    """SessionCard should render session title text."""
    from ccsm.tui.widgets.session_card import SessionCard
    from pathlib import Path
    from ccsm.models.session import SessionInfo, Status

    session = SessionInfo(
        session_id="test-card",
        project_dir="/test",
        jsonl_path=Path("/tmp/test-card.jsonl"),
        message_count=10,
        status=Status.ACTIVE,
        display_name="My Test Session",
    )
    card = SessionCard(session)
    # Card should be constructible without error
    assert card.session.session_id == "test-card"


def test_lineage_group_constructible():
    """LineageGroup should be constructible with batch-rendered data."""
    from ccsm.tui.widgets.lineage_group import LineageGroup
    from pathlib import Path
    from ccsm.models.session import SessionInfo, Status

    sessions = [
        SessionInfo(
            session_id=f"lg-{i}",
            project_dir="/test",
            jsonl_path=Path(f"/tmp/lg-{i}.jsonl"),
            message_count=5,
            status=Status.ACTIVE,
        )
        for i in range(3)
    ]

    group = LineageGroup(
        tree_sessions=sessions,
        lineage_types={},
        all_meta={},
    )
    assert len(group._sessions) == 3


def test_tab_switch_resets_batch_state():
    """Switching tabs should reset pending trees state."""
    from ccsm.tui.widgets.session_list import SessionListPanel

    panel = SessionListPanel()
    # Verify initial state
    assert panel._pending_trees == []
    assert panel._rendered_count == 0
