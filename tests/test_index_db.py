"""Tests for SQLite persistent session index."""
from datetime import datetime, timezone

import pytest

from ccsm.core.index_db import SessionIndexDB


@pytest.fixture
def db(tmp_path):
    """Create a temporary SQLite index."""
    return SessionIndexDB(db_path=tmp_path / "test_index.db")


def test_upsert_and_get(db):
    """Upsert a session record and retrieve it."""
    db.upsert(
        session_id="abc-123",
        jsonl_path="/fake/path.jsonl",
        jsonl_mtime=1234567890.0,
        title="Test Session",
        slug="test-session-slug",
        status="active",
        message_count=42,
        last_timestamp=datetime(2026, 4, 4, tzinfo=timezone.utc),
    )
    row = db.get("abc-123")
    assert row is not None
    assert row["title"] == "Test Session"
    assert row["message_count"] == 42


def test_needs_refresh_detects_mtime_change(db):
    """needs_refresh returns True when mtime differs."""
    db.upsert(
        session_id="abc-123",
        jsonl_path="/fake/path.jsonl",
        jsonl_mtime=1000.0,
        title="Old",
        slug="old",
        status="active",
        message_count=10,
    )
    assert db.needs_refresh("abc-123", current_mtime=1000.0) is False
    assert db.needs_refresh("abc-123", current_mtime=2000.0) is True
    assert db.needs_refresh("unknown-id", current_mtime=1000.0) is True


def test_list_all(db):
    """list_all returns all indexed sessions."""
    for i in range(5):
        db.upsert(
            session_id=f"session-{i}",
            jsonl_path=f"/fake/{i}.jsonl",
            jsonl_mtime=float(i),
            title=f"Session {i}",
            slug=f"slug-{i}",
            status="active",
            message_count=i * 10,
        )
    results = db.list_all()
    assert len(results) == 5


def test_delete(db):
    """delete removes a session from the index."""
    db.upsert(
        session_id="to-delete",
        jsonl_path="/fake/del.jsonl",
        jsonl_mtime=100.0,
        title="Delete Me",
        slug="del",
        status="noise",
        message_count=1,
    )
    assert db.get("to-delete") is not None
    db.delete("to-delete")
    assert db.get("to-delete") is None


def test_upsert_updates_existing(db):
    """Upsert on existing session_id updates the record."""
    db.upsert(
        session_id="update-me",
        jsonl_path="/fake/old.jsonl",
        jsonl_mtime=100.0,
        title="Old Title",
        slug="old",
        status="active",
        message_count=5,
    )
    db.upsert(
        session_id="update-me",
        jsonl_path="/fake/new.jsonl",
        jsonl_mtime=200.0,
        title="New Title",
        slug="new",
        status="done",
        message_count=15,
    )
    row = db.get("update-me")
    assert row["title"] == "New Title"
    assert row["message_count"] == 15
    assert row["jsonl_mtime"] == 200.0
    # Should only be one record
    assert len(db.list_all()) == 1
