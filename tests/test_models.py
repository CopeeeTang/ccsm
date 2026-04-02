"""Tests for extended session models."""
from ccsm.models.session import (
    LineageType, SessionLineage, SessionMeta,
)


def test_lineage_type_values():
    assert LineageType.FORK.value == "fork"
    assert LineageType.COMPACT.value == "compact"
    assert LineageType.DUPLICATE.value == "duplicate"
    assert LineageType.ROOT.value == "root"


def test_session_lineage_defaults():
    lin = SessionLineage(session_id="abc-123")
    assert lin.session_id == "abc-123"
    assert lin.lineage_type == LineageType.ROOT
    assert lin.parent_id is None
    assert lin.children == []
    assert lin.compact_predecessor is None
    assert lin.fork_source is None
    assert lin.fork_label is None
    assert lin.depth == 0


def test_session_lineage_fork():
    lin = SessionLineage(
        session_id="child-1",
        lineage_type=LineageType.FORK,
        parent_id="parent-1",
        fork_source="parent-1",
        fork_label="refactor-branch",
        depth=1,
    )
    assert lin.lineage_type == LineageType.FORK
    assert lin.parent_id == "parent-1"
    assert lin.fork_label == "refactor-branch"


def test_session_meta_new_fields():
    meta = SessionMeta(session_id="abc-123")
    assert meta.title_locked is False
    assert meta.last_message_at is None
    assert meta.last_accessed_at is None
    assert meta.lineage is None
