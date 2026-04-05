"""Tests for cache staleness detection."""

import json
import time
import tempfile
from pathlib import Path


def test_summary_is_stale_after_jsonl_update(tmp_path):
    """Summary cached before JSONL update should be marked stale."""
    from ccsm.core.meta import is_summary_stale

    # Create JSONL file
    jsonl = tmp_path / "session.jsonl"
    jsonl.write_text('{"type":"user","uuid":"u1","timestamp":"2026-04-01T10:00:00Z","message":{"content":"hi"}}\n')

    # Create summary file (older than JSONL)
    summary = tmp_path / "session.summary.json"
    summary.write_text(json.dumps({"milestones": [], "mode": "extract"}))

    # Summary written at same time or after JSONL — NOT stale
    assert not is_summary_stale(summary, jsonl)

    # Now update JSONL
    time.sleep(0.05)
    with open(jsonl, "a") as f:
        f.write('{"type":"user","uuid":"u2","timestamp":"2026-04-01T11:00:00Z","message":{"content":"update"}}\n')

    # Summary is now stale (JSONL newer)
    assert is_summary_stale(summary, jsonl)


def test_summary_not_stale_when_no_jsonl():
    """Missing JSONL should not mark summary as stale."""
    from ccsm.core.meta import is_summary_stale

    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        f.write(b'{}')
        summary = Path(f.name)

    assert not is_summary_stale(summary, Path("/nonexistent.jsonl"))
    summary.unlink()
