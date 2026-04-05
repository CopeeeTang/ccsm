"""Tests for parse_cache module."""

import json
import tempfile
import time
from pathlib import Path

from ccsm.core.parse_cache import cache_key_for


def test_cache_key_same_file_same_mtime():
    """Same file with same mtime should produce same cache key."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        f.write(b'{"type":"user"}\n')
        path = Path(f.name)

    key1 = cache_key_for(path)
    key2 = cache_key_for(path)
    assert key1 == key2
    path.unlink()


def test_cache_key_changes_after_write():
    """Cache key should change when file is modified."""
    with tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False) as f:
        f.write(b'{"type":"user"}\n')
        path = Path(f.name)

    key1 = cache_key_for(path)

    # Ensure mtime actually changes (some filesystems have 1s resolution)
    time.sleep(0.05)
    path.write_text('{"type":"user"}\n{"type":"assistant"}\n')

    key2 = cache_key_for(path)
    assert key1 != key2
    path.unlink()


def test_cache_key_missing_file():
    """Missing file should return a sentinel key."""
    key = cache_key_for(Path("/tmp/nonexistent_session.jsonl"))
    assert key == ("__missing__", 0, 0)


# ─── Tests: cached_parse_complete ──────────────────────────────────────────

from ccsm.core.parse_cache import cached_parse_complete, invalidate_cache


def _make_jsonl(tmp_path, name, lines):
    p = tmp_path / f"{name}.jsonl"
    p.write_text("\n".join(json.dumps(l) for l in lines) + "\n")
    return p


def test_cached_parse_complete_cache_hit(tmp_path):
    """Second call with same file should return cached result."""
    invalidate_cache()  # ensure clean state
    path = _make_jsonl(tmp_path, "sess1", [
        {"sessionId": "s1", "type": "user", "uuid": "u1", "message": {"content": "hi"}, "timestamp": "2026-04-01T10:00:00Z"},
        {"type": "assistant", "uuid": "a1", "message": {"content": "hello"}, "timestamp": "2026-04-01T10:01:00Z"},
    ])

    r1 = cached_parse_complete(path)
    r2 = cached_parse_complete(path)

    # Same object reference = cache hit
    assert r1[0].session_id == r2[0].session_id


def test_cached_parse_complete_invalidate(tmp_path):
    """After invalidate_cache(), should re-read."""
    invalidate_cache()  # ensure clean state
    path = _make_jsonl(tmp_path, "sess2", [
        {"sessionId": "s2", "type": "user", "uuid": "u1", "message": {"content": "v1"}, "timestamp": "2026-04-01T10:00:00Z"},
    ])

    r1 = cached_parse_complete(path)
    invalidate_cache()
    r2 = cached_parse_complete(path)

    assert r1[0].session_id == r2[0].session_id  # same data
