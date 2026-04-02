"""Tests for enhanced discovery: duplicate detection."""
from datetime import datetime, timezone

from ccsm.core.lineage import LineageSignals
from ccsm.core.discovery import detect_duplicates


def test_detect_duplicates_overlapping():
    """Sessions with same cwd+branch and <5min gap are duplicates."""
    signals = {
        "s1": LineageSignals(
            session_id="s1",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
            first_user_content="帮我修复登录的bug",
        ),
        "s2": LineageSignals(
            session_id="s2",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 28, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
            first_user_content="帮我修复登录的bug",
        ),
    }
    groups = detect_duplicates(signals)
    assert len(groups) == 1
    assert set(groups[0]) == {"s1", "s2"}


def test_no_false_duplicates_different_branch():
    """Different branches = independent, even if same cwd and time."""
    signals = {
        "s1": LineageSignals(
            session_id="s1",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
        "s2": LineageSignals(
            session_id="s2",
            cwd="/home/user/project",
            git_branch="feature",  # different branch
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
    }
    groups = detect_duplicates(signals)
    assert len(groups) == 0


def test_no_false_duplicates_large_gap():
    """Same cwd+branch but 1-hour gap = independent."""
    signals = {
        "s1": LineageSignals(
            session_id="s1",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
        "s2": LineageSignals(
            session_id="s2",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 11, 30, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        ),
    }
    groups = detect_duplicates(signals)
    assert len(groups) == 0
