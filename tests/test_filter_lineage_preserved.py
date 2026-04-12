"""After switching filter tab, fork/compact relationships must remain visible.

Regression test for the bug where Active->All filter switch would lose
lineage tree grouping — the _rebuild_list method was building trees from
the filtered session set instead of the full session set.
"""
from __future__ import annotations

import pytest
from datetime import datetime, timezone
from types import SimpleNamespace

from ccsm.models.session import SessionInfo, SessionMeta, Status
from ccsm.tui.app import CCSMApp
from ccsm.tui.widgets.session_list import SessionListPanel, _build_lineage_trees
from ccsm.tui.widgets.lineage_group import LineageGroup


def _mk(sid: str, status: Status, ts_hour: int) -> SessionInfo:
    """Create a minimal SessionInfo with controlled status/timestamp."""
    return SessionInfo(
        session_id=sid,
        project_dir="test-project",
        jsonl_path=f"/tmp/{sid}.jsonl",
        display_name=f"Session {sid}",
        status=status,
        message_count=10,
        last_timestamp=datetime(2026, 4, 12, ts_hour, tzinfo=timezone.utc),
    )


def _mk_node(parent_id=None, children=None, lineage_type_value="root"):
    """Create a mock lineage graph node."""
    class _FakeType:
        def __init__(self, v):
            self.value = v
    return SimpleNamespace(
        parent_id=parent_id,
        children=children or [],
        lineage_type=_FakeType(lineage_type_value),
    )


class TestBuildLineageTreesPreservation:
    """Unit tests for _build_lineage_trees with filtered vs full session sets."""

    def test_full_set_produces_single_tree(self):
        """With all 3 sessions, _build_lineage_trees should produce 1 tree."""
        sessions = [
            _mk("root", Status.ACTIVE, 10),
            _mk("compact_1", Status.DONE, 11),
            _mk("compact_2", Status.ACTIVE, 12),
        ]
        lineage_types = {"compact_1": "compact", "compact_2": "compact"}
        lineage_graph = {
            "root": _mk_node(parent_id=None, children=["compact_1"]),
            "compact_1": _mk_node(parent_id="root", children=["compact_2"],
                                   lineage_type_value="compact"),
            "compact_2": _mk_node(parent_id="compact_1", children=[],
                                   lineage_type_value="compact"),
        }

        trees = _build_lineage_trees(sessions, lineage_types, lineage_graph)
        assert len(trees) == 1, f"Expected 1 tree, got {len(trees)}"
        member_ids = {s.session_id for s in trees[0]}
        assert member_ids == {"root", "compact_1", "compact_2"}

    def test_active_filter_still_builds_single_tree_from_full_set(self):
        """Even with ACTIVE filter, _build_lineage_trees with full sessions should keep 1 tree.

        This verifies the fix: _rebuild_list should call _build_lineage_trees
        with the FULL session set, then mask visibility per-card.
        """
        all_sessions = [
            _mk("root", Status.ACTIVE, 10),
            _mk("compact_1", Status.DONE, 11),
            _mk("compact_2", Status.ACTIVE, 12),
        ]
        lineage_types = {"compact_1": "compact", "compact_2": "compact"}
        lineage_graph = {
            "root": _mk_node(parent_id=None, children=["compact_1"]),
            "compact_1": _mk_node(parent_id="root", children=["compact_2"],
                                   lineage_type_value="compact"),
            "compact_2": _mk_node(parent_id="compact_1", children=[],
                                   lineage_type_value="compact"),
        }

        # Build trees from FULL set (the correct approach)
        trees = _build_lineage_trees(all_sessions, lineage_types, lineage_graph)
        assert len(trees) == 1, (
            f"Full session set should produce 1 tree, got {len(trees)}"
        )
        member_ids = {s.session_id for s in trees[0]}
        assert member_ids == {"root", "compact_1", "compact_2"}


@pytest.mark.asyncio
async def test_active_filter_shows_lineage_group_not_fragments():
    """Under ACTIVE filter, sessions in a lineage tree should still appear in a LineageGroup
    (with non-ACTIVE members hidden), not as scattered individual cards."""
    from textual.app import App, ComposeResult

    class _TestApp(App):
        def compose(self) -> ComposeResult:
            yield SessionListPanel()

    sessions = [
        _mk("root", Status.ACTIVE, 10),
        _mk("compact_1", Status.DONE, 11),
        _mk("compact_2", Status.ACTIVE, 12),
    ]
    lineage_types = {"compact_1": "compact", "compact_2": "compact"}
    lineage_graph = {
        "root": _mk_node(parent_id=None, children=["compact_1"]),
        "compact_1": _mk_node(parent_id="root", children=["compact_2"],
                               lineage_type_value="compact"),
        "compact_2": _mk_node(parent_id="compact_1", children=[],
                               lineage_type_value="compact"),
    }

    async with _TestApp().run_test() as pilot:
        panel = pilot.app.query_one(SessionListPanel)
        panel.load_sessions(
            sessions=sessions,
            all_meta={},
            lineage_types=lineage_types,
            lineage_graph=lineage_graph,
            last_thoughts={},
        )
        await pilot.pause()

        # Switch to ACTIVE filter
        panel.set_active_tab(Status.ACTIVE)
        await pilot.pause()

        # Should still have a LineageGroup (tree topology preserved)
        groups = list(panel.query(LineageGroup))
        # The tree has 3 members but only 2 are ACTIVE,
        # so tree should still exist as a group (not fragmented into individual cards)
        assert len(groups) == 1, (
            f"Expected 1 lineage group under ACTIVE filter, got {len(groups)}. "
            f"Tree was fragmented instead of keeping topology."
        )


@pytest.mark.asyncio
async def test_switching_from_active_to_all_preserves_lineage():
    """After ALL -> ACTIVE -> ALL, the lineage group should still contain all 3 members."""
    from textual.app import App, ComposeResult
    from textual.containers import Vertical

    class _TestApp(App):
        """Minimal app that mounts just a SessionListPanel for testing."""
        def compose(self) -> ComposeResult:
            yield SessionListPanel()

    sessions = [
        _mk("root", Status.ACTIVE, 10),
        _mk("compact_1", Status.DONE, 11),
        _mk("compact_2", Status.ACTIVE, 12),
    ]
    lineage_types = {"compact_1": "compact", "compact_2": "compact"}
    lineage_graph = {
        "root": _mk_node(parent_id=None, children=["compact_1"]),
        "compact_1": _mk_node(parent_id="root", children=["compact_2"],
                               lineage_type_value="compact"),
        "compact_2": _mk_node(parent_id="compact_1", children=[],
                               lineage_type_value="compact"),
    }

    async with _TestApp().run_test() as pilot:
        panel = pilot.app.query_one(SessionListPanel)
        panel.load_sessions(
            sessions=sessions,
            all_meta={},
            lineage_types=lineage_types,
            lineage_graph=lineage_graph,
            last_thoughts={},
        )
        await pilot.pause()

        # Initial (ALL): lineage group should contain all 3
        groups = list(panel.query(LineageGroup))
        assert len(groups) == 1, f"Expected single lineage tree, got {len(groups)}"
        initial_members = {s.session_id for s in groups[0]._sessions}
        assert initial_members == {"root", "compact_1", "compact_2"}

        # Switch to ACTIVE filter
        panel.set_active_tab(Status.ACTIVE)
        await pilot.pause()

        # Switch back to ALL
        panel.set_filter_all()
        await pilot.pause()

        # Lineage group should STILL contain all 3
        groups = list(panel.query(LineageGroup))
        assert len(groups) == 1, f"Lineage tree lost after filter round-trip! Got {len(groups)} groups."
        final_members = {s.session_id for s in groups[0]._sessions}
        assert final_members == initial_members, (
            f"Lineage members changed: {initial_members} -> {final_members}"
        )
