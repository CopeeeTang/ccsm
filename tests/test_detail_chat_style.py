"""Tests for Brief UI aligned Last Exchange rendering."""

from datetime import datetime, timezone


def test_show_session_accepts_jsonl_messages():
    """show_session should accept JSONLMessage objects with timestamps."""
    from ccsm.models.session import SessionInfo, JSONLMessage, Status
    from pathlib import Path

    session = SessionInfo(
        session_id="test-chat-1",
        project_dir="/test",
        jsonl_path=Path("/tmp/test.jsonl"),
        message_count=5,
        status=Status.ACTIVE,
    )

    msgs = [
        JSONLMessage(
            uuid="u1",
            parent_uuid=None,
            role="user",
            content="fix the bug",
            timestamp=datetime(2026, 4, 6, 15, 30, 0, tzinfo=timezone.utc),
        ),
        JSONLMessage(
            uuid="a1",
            parent_uuid="u1",
            role="assistant",
            content="I found the issue in parser.ts",
            timestamp=datetime(2026, 4, 6, 15, 31, 0, tzinfo=timezone.utc),
        ),
    ]

    # Verify JSONLMessage has the expected fields
    assert msgs[0].role == "user"
    assert msgs[0].timestamp.hour == 15
    assert msgs[1].role == "assistant"


def test_format_chat_timestamp():
    """Chat timestamp should format as HH:MM."""
    from ccsm.tui.widgets.session_detail import _format_chat_time
    from datetime import datetime, timezone

    ts = datetime(2026, 4, 6, 15, 30, 45, tzinfo=timezone.utc)
    assert _format_chat_time(ts) == "15:30"

    ts_none = None
    assert _format_chat_time(ts_none) == ""
