"""Tests for ccsm.core.lineage — cross-session lineage detection."""

from __future__ import annotations

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ccsm.core.lineage import (
    LineageSignals,
    build_lineage_graph,
    parse_lineage_signals,
)
from ccsm.models.session import LineageType


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _write_jsonl(lines: list[dict]) -> Path:
    """Write a list of dicts as JSONL to a temp file and return its Path."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for entry in lines:
        f.write(json.dumps(entry) + "\n")
    f.flush()
    f.close()
    return Path(f.name)


def _ts(seconds_offset: int = 0) -> float:
    """Return a Unix-epoch timestamp (seconds) with an optional offset."""
    base = 1700000000  # 2023-11-14T22:13:20Z
    return base + seconds_offset


# ─── Tests: parse_lineage_signals ────────────────────────────────────────────


class TestParseLineageSignals:
    """Tests for parse_lineage_signals."""

    def test_detect_fork_from_branch_name(self, tmp_path: Path) -> None:
        """display_name ending with '(branch)' → is_fork=True."""
        jsonl_path = _write_jsonl([
            {"sessionId": "s1", "type": "user", "timestamp": _ts(),
             "message": {"content": "hello"}},
        ])
        signals = parse_lineage_signals(jsonl_path, display_name="fix-bug (branch)")
        assert signals.is_fork is True
        assert signals.fork_hint == "display_name_branch_suffix"

    def test_detect_compact_boundary(self, tmp_path: Path) -> None:
        """System message with subtype compact_boundary is detected."""
        jsonl_path = _write_jsonl([
            {"sessionId": "s2", "type": "user", "timestamp": _ts(0),
             "message": {"content": "start"}},
            {"type": "system", "subtype": "compact_boundary"},
            {"type": "assistant", "timestamp": _ts(60),
             "message": {"content": "ok"}},
        ])
        signals = parse_lineage_signals(jsonl_path)
        assert signals.has_compact_boundary is True
        assert signals.compact_count == 1

    def test_detect_no_signals(self, tmp_path: Path) -> None:
        """Normal session with no special markers."""
        jsonl_path = _write_jsonl([
            {"sessionId": "s3", "type": "user", "timestamp": _ts(0),
             "message": {"content": "What is Python?"}},
            {"type": "assistant", "timestamp": _ts(5),
             "message": {"content": "Python is a language."}},
        ])
        signals = parse_lineage_signals(jsonl_path)
        assert signals.is_fork is False
        assert signals.has_compact_boundary is False
        assert signals.session_id == "s3"

    def test_extract_last_message_timestamp(self, tmp_path: Path) -> None:
        """last_message_at should be the last user/assistant timestamp."""
        jsonl_path = _write_jsonl([
            {"sessionId": "s4", "type": "user", "timestamp": _ts(0),
             "message": {"content": "q1"}},
            {"type": "assistant", "timestamp": _ts(10),
             "message": {"content": "a1"}},
            {"type": "user", "timestamp": _ts(100),
             "message": {"content": "q2"}},
            {"type": "assistant", "timestamp": _ts(110),
             "message": {"content": "a2"}},
            # Metadata entry — should NOT update last_message_at
            {"type": "metadata", "timestamp": _ts(999)},
        ])
        signals = parse_lineage_signals(jsonl_path)
        assert signals.first_message_at == datetime.fromtimestamp(_ts(0), tz=timezone.utc)
        assert signals.last_message_at == datetime.fromtimestamp(_ts(110), tz=timezone.utc)

    def test_detect_fork_from_compact_summary_first_message(self, tmp_path: Path) -> None:
        """First user message starting with compact summary prefix → fork."""
        jsonl_path = _write_jsonl([
            {"sessionId": "s5", "type": "user", "timestamp": _ts(0),
             "message": {"content": "Here is a summary of the conversation so far..."}},
            {"type": "assistant", "timestamp": _ts(10),
             "message": {"content": "Got it."}},
        ])
        signals = parse_lineage_signals(jsonl_path)
        assert signals.is_fork is True
        assert signals.fork_hint == "compact_summary_first_message"

    def test_extract_session_cwd_and_branch(self, tmp_path: Path) -> None:
        """cwd and git_branch extracted from JSONL entries."""
        jsonl_path = _write_jsonl([
            {"sessionId": "s6", "cwd": "/home/user/project",
             "gitBranch": "main", "type": "user", "timestamp": _ts(0),
             "message": {"content": "hi"}},
            {"cwd": "/home/user/project/sub", "gitBranch": "feature-x",
             "type": "assistant", "timestamp": _ts(10),
             "message": {"content": "hello"}},
        ])
        signals = parse_lineage_signals(jsonl_path)
        # Last seen wins
        assert signals.cwd == "/home/user/project/sub"
        assert signals.git_branch == "feature-x"


# ─── Tests: build_lineage_graph ──────────────────────────────────────────────


class TestBuildLineageGraph:
    """Tests for build_lineage_graph."""

    def test_build_graph_detects_duplicate(self) -> None:
        """Two sessions with same cwd+branch and overlapping time → DUPLICATE."""
        signals_map = {
            "a": LineageSignals(
                session_id="a",
                cwd="/proj",
                git_branch="main",
                first_message_at=datetime.fromtimestamp(_ts(0), tz=timezone.utc),
                last_message_at=datetime.fromtimestamp(_ts(100), tz=timezone.utc),
            ),
            "b": LineageSignals(
                session_id="b",
                cwd="/proj",
                git_branch="main",
                first_message_at=datetime.fromtimestamp(_ts(150), tz=timezone.utc),
                last_message_at=datetime.fromtimestamp(_ts(200), tz=timezone.utc),
            ),
        }
        graph = build_lineage_graph(signals_map)
        assert graph["a"].lineage_type == LineageType.ROOT
        assert graph["b"].lineage_type == LineageType.DUPLICATE
        assert graph["b"].parent_id == "a"
        assert "b" in graph["a"].children

    def test_build_graph_no_false_duplicate(self) -> None:
        """Same cwd+branch but 1-hour gap → both ROOT (no duplicate)."""
        signals_map = {
            "x": LineageSignals(
                session_id="x",
                cwd="/proj",
                git_branch="main",
                first_message_at=datetime.fromtimestamp(_ts(0), tz=timezone.utc),
                last_message_at=datetime.fromtimestamp(_ts(100), tz=timezone.utc),
            ),
            "y": LineageSignals(
                session_id="y",
                cwd="/proj",
                git_branch="main",
                first_message_at=datetime.fromtimestamp(_ts(3700), tz=timezone.utc),
                last_message_at=datetime.fromtimestamp(_ts(3800), tz=timezone.utc),
            ),
        }
        graph = build_lineage_graph(signals_map)
        assert graph["x"].lineage_type == LineageType.ROOT
        assert graph["y"].lineage_type == LineageType.ROOT
        assert graph["y"].parent_id is None

    def test_build_graph_fork_preserved(self) -> None:
        """Fork signal from parse_lineage_signals is preserved in graph."""
        signals_map = {
            "root": LineageSignals(
                session_id="root",
                cwd="/proj",
                git_branch="main",
                first_message_at=datetime.fromtimestamp(_ts(0), tz=timezone.utc),
                last_message_at=datetime.fromtimestamp(_ts(100), tz=timezone.utc),
            ),
            "forked": LineageSignals(
                session_id="forked",
                is_fork=True,
                fork_hint="display_name_branch_suffix",
                cwd="/proj",
                git_branch="main",
                first_message_at=datetime.fromtimestamp(_ts(50), tz=timezone.utc),
                last_message_at=datetime.fromtimestamp(_ts(120), tz=timezone.utc),
            ),
        }
        graph = build_lineage_graph(signals_map)
        assert graph["forked"].lineage_type == LineageType.FORK
        assert graph["forked"].fork_label == "display_name_branch_suffix"
        # Fork should NOT be marked as duplicate even with overlapping time
        assert graph["forked"].parent_id is None


# ─── Tests: extract_signals_from_lines ──────────────────────────────────────


from ccsm.core.lineage import extract_signals_from_lines


def test_extract_signals_from_lines_basic():
    """extract_signals_from_lines should produce same result as parse_lineage_signals."""
    lines = [
        json.dumps({"sessionId": "abc123", "type": "user", "message": {"content": "hello"}, "timestamp": "2026-04-01T10:00:00Z"}) + "\n",
        json.dumps({"type": "assistant", "message": {"content": "hi"}, "timestamp": "2026-04-01T10:01:00Z"}) + "\n",
    ]
    signals = extract_signals_from_lines(lines, display_name=None)
    assert signals.session_id == "abc123"
    assert signals.first_message_at is not None
    assert signals.last_message_at is not None


def test_extract_signals_from_lines_fork_detection():
    """Should detect fork from forkedFrom field."""
    lines = [
        json.dumps({"sessionId": "child1", "forkedFrom": {"sessionId": "parent1"}, "type": "user", "message": {"content": "x"}, "timestamp": "2026-04-01T10:00:00Z"}) + "\n",
    ]
    signals = extract_signals_from_lines(lines)
    assert signals.is_fork is True
    assert signals.fork_source_id == "parent1"


def test_extract_signals_from_lines_compact_detection():
    """Should detect compact boundary."""
    lines = [
        json.dumps({"type": "system", "subtype": "compact_boundary"}) + "\n",
        json.dumps({"type": "user", "message": {"content": "continue"}, "timestamp": "2026-04-01T11:00:00Z"}) + "\n",
    ]
    signals = extract_signals_from_lines(lines)
    assert signals.has_compact_boundary is True
    assert signals.compact_count == 1
