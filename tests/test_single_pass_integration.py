"""Integration test: verify parse_session_complete does single file read."""

import json
import tempfile
from pathlib import Path


def test_parse_and_display_uses_single_read(tmp_path, monkeypatch):
    """Verify the pipeline reads each JSONL file only once."""
    import ccsm.core.parser as parser_mod

    # Track _read_lines calls
    read_paths = []
    original_read = parser_mod._read_lines

    def tracking_read(p):
        read_paths.append(str(p))
        return original_read(p)

    monkeypatch.setattr(parser_mod, "_read_lines", tracking_read)

    # Create a fake JSONL
    jsonl = tmp_path / "test-session.jsonl"
    jsonl.write_text(
        json.dumps({"sessionId": "s1", "type": "user", "uuid": "u1", "message": {"content": "hello"}, "timestamp": "2026-04-01T10:00:00Z"}) + "\n"
        + json.dumps({"type": "assistant", "uuid": "a1", "message": {"content": "hi"}, "timestamp": "2026-04-01T10:01:00Z"}) + "\n"
    )

    from ccsm.core.parser import parse_session_complete
    info, signals, msgs = parse_session_complete(jsonl)

    # Should only have 1 _read_lines call (not 3)
    assert read_paths.count(str(jsonl)) == 1
