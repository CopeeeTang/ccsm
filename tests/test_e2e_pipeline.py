"""End-to-end smoke test for the enhanced CCSM pipeline.

Verifies the complete flow: JSONL → lineage signals → index → search → graph.
Covers all 8 pain points from the CCSM resume improvement plan.
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ccsm.core.lineage import parse_lineage_signals, build_lineage_graph, LineageSignals
from ccsm.core.index import SessionIndex, IndexEntry
from ccsm.core.parser import parse_session_timestamps
from ccsm.core.discovery import detect_duplicates
from ccsm.models.session import LineageType, SessionLineage


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def test_full_pipeline():
    """Complete pipeline: parse → lineage → index → search.

    Exercises pain points #1 (fork), #4 (search), #5 (compact), #6 (timestamps), #7 (dup).
    """
    tmp = Path(tempfile.mkdtemp())

    # ── Create two sessions: parent and fork ──
    parent_path = tmp / "parent-001.jsonl"
    _write_jsonl(parent_path, [
        {
            "type": "user",
            "message": {"role": "user", "content": "实现一个登录页面"},
            "sessionId": "parent-001",
            "cwd": "/home/user/project",
            "gitBranch": "main",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "好的，我来实现登录页面..."},
            "sessionId": "parent-001",
            "timestamp": "2026-04-01T10:05:00Z",
        },
    ])

    fork_path = tmp / "fork-002.jsonl"
    _write_jsonl(fork_path, [
        {
            "type": "user",
            "message": {"role": "user", "content": "调试一下CSS样式问题"},
            "sessionId": "fork-002",
            "cwd": "/home/user/project",
            "gitBranch": "main",
            "timestamp": "2026-04-01T10:03:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "让我检查CSS..."},
            "sessionId": "fork-002",
            "timestamp": "2026-04-01T10:08:00Z",
        },
    ])

    # ── Phase 1: Parse lineage signals (pain point #1) ──
    sig_parent = parse_lineage_signals(parent_path)
    sig_fork = parse_lineage_signals(fork_path, display_name="login-page (branch)")

    assert sig_parent.is_fork is False
    assert sig_fork.is_fork is True
    assert sig_fork.fork_hint == "display_name_branch_suffix"

    # ── Phase 2: Parse timestamps (pain point #6) ──
    ts_parent = parse_session_timestamps(parent_path)
    assert ts_parent.last_message_at == datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc)

    # ── Phase 3: Build lineage graph (pain point #8) ──
    signals_map = {
        "parent-001": sig_parent,
        "fork-002": sig_fork,
    }
    graph = build_lineage_graph(signals_map)
    assert graph["fork-002"].lineage_type == LineageType.FORK

    # ── Phase 4: Detect duplicates (pain point #7) ──
    dups = detect_duplicates(signals_map)
    assert len(dups) == 1  # They overlap in time and share cwd+branch

    # ── Phase 5: Build search index (pain point #4) ──
    idx = SessionIndex()
    idx.update_entries([
        IndexEntry(
            session_id="parent-001",
            title="login-page",
            intent="实现登录页面",
            git_branch="main",
            first_user_content="实现一个登录页面",
            last_message_at=ts_parent.last_message_at,
            status="active",
        ),
        IndexEntry(
            session_id="fork-002",
            title="login-page (branch)",
            intent="调试CSS样式",
            git_branch="main",
            first_user_content="调试一下CSS样式问题",
            last_message_at=sig_fork.last_message_at,
            status="active",
        ),
    ])

    # Search by Chinese content
    results = idx.search("登录")
    assert len(results) >= 1
    assert any(r.session_id == "parent-001" for r in results)

    # Search by intent
    results = idx.search("CSS")
    assert len(results) == 1
    assert results[0].session_id == "fork-002"


def test_compact_boundary_detected():
    """Pain point #5: compact_boundary is detected and counted."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "compact-session.jsonl"
    _write_jsonl(path, [
        {
            "type": "user",
            "message": {"role": "user", "content": "start"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": "s1",
            "timestamp": "2026-04-01T11:00:00Z",
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "continue after compact"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T11:05:00Z",
        },
    ])

    sig = parse_lineage_signals(path)
    assert sig.has_compact_boundary is True
    assert sig.compact_count == 1
    assert sig.last_message_at == datetime(2026, 4, 1, 11, 5, tzinfo=timezone.utc)

    ts = parse_session_timestamps(path)
    assert ts.compact_count == 1


def test_title_lock_survives_roundtrip(tmp_path, monkeypatch):
    """Pain point #2: locked title persists in sidecar."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    import importlib
    from ccsm.core import meta as m
    importlib.reload(m)

    result = m.lock_title("test-session", "我的稳定标题")
    assert result.title_locked is True
    assert result.name == "我的稳定标题"

    # Reload and verify
    loaded = m.load_meta("test-session")
    assert loaded.name == "我的稳定标题"
    assert loaded.title_locked is True


