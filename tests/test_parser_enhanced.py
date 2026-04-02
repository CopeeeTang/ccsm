"""Tests for parse_session_timestamps() in ccsm.core.parser."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ccsm.core.parser import parse_session_timestamps


def _write_jsonl(lines: list[dict]) -> Path:
    """Write a list of dicts as JSONL to a temp file and return its Path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for obj in lines:
        f.write(json.dumps(obj) + "\n")
    f.flush()
    f.close()
    return Path(f.name)


def test_parse_timestamps_basic():
    """Two messages → correct first/last timestamps, compact_count=0."""
    ts1 = "2026-04-01T10:00:00+00:00"
    ts2 = "2026-04-01T11:30:00+00:00"

    path = _write_jsonl([
        {
            "type": "user",
            "uuid": "u1",
            "timestamp": ts1,
            "message": {"content": "Hello"},
        },
        {
            "type": "assistant",
            "uuid": "a1",
            "timestamp": ts2,
            "message": {"content": "Hi there"},
        },
    ])

    result = parse_session_timestamps(path)

    assert result.first_message_at == datetime(2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc)
    assert result.last_message_at == datetime(2026, 4, 1, 11, 30, 0, tzinfo=timezone.utc)
    assert result.compact_count == 0


def test_parse_timestamps_ignores_metadata_lines():
    """Metadata lines (custom-title, last-prompt) must NOT affect timestamps."""
    ts_user = "2026-04-01T09:00:00+00:00"
    ts_title = "2026-04-01T12:00:00+00:00"
    ts_prompt = "2026-04-01T13:00:00+00:00"

    path = _write_jsonl([
        {
            "type": "user",
            "uuid": "u1",
            "timestamp": ts_user,
            "message": {"content": "Do something"},
        },
        {
            "type": "custom-title",
            "timestamp": ts_title,
            "title": "My Session",
        },
        {
            "type": "last-prompt",
            "timestamp": ts_prompt,
            "prompt": "Do something",
        },
    ])

    result = parse_session_timestamps(path)

    # Only the user message timestamp should be picked up
    assert result.first_message_at == datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert result.last_message_at == datetime(2026, 4, 1, 9, 0, 0, tzinfo=timezone.utc)
    assert result.compact_count == 0


def test_parse_timestamps_with_compact():
    """User + compact_boundary + user → compact_count=1, correct timestamps."""
    ts1 = "2026-04-01T08:00:00+00:00"
    ts2 = "2026-04-01T14:00:00+00:00"

    path = _write_jsonl([
        {
            "type": "user",
            "uuid": "u1",
            "timestamp": ts1,
            "message": {"content": "First message"},
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "timestamp": "2026-04-01T10:00:00+00:00",
        },
        {
            "type": "user",
            "uuid": "u2",
            "timestamp": ts2,
            "message": {"content": "After compact"},
        },
    ])

    result = parse_session_timestamps(path)

    assert result.compact_count == 1
    assert result.first_message_at == datetime(2026, 4, 1, 8, 0, 0, tzinfo=timezone.utc)
    assert result.last_message_at == datetime(2026, 4, 1, 14, 0, 0, tzinfo=timezone.utc)
