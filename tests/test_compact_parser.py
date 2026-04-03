"""Tests for compact_parser — structured parsing of Claude Code compact summaries."""

import pytest

from ccsm.core.compact_parser import (
    extract_milestones_from_compact,
    parse_compact_summary,
)
from ccsm.models.session import CompactSummaryParsed, MilestoneStatus


# ─── parse_compact_summary tests ─────────────────────────────────────────────


_SAMPLE_COMPACT = """\
This session is being continued from a previous conversation.

Summary:
1. Primary Request and Intent:
   - User asked to optimize the CCSM parser for performance
   - Specific goal: reduce JSONL parsing time from 4s to <1s

2. Key Technical Concepts:
   - **JSONL format**: line-delimited JSON for session logs
   - **Tail reading**: seek-from-end strategy for large files
   - **TF-IDF indexing**: full-text search without external deps

3. Files and Code Sections:
   - ccsm/core/parser.py — main parsing logic
   - ccsm/core/index.py — search index

4. Problem Solving:
   - Fixed ReDoS vulnerability in XML sanitizer regex
   - Resolved off-by-one in timestamp extraction

5. Current Work:
   - Implementing batched parsing for large worktrees
   - Testing with 500+ session datasets

6. Pending Tasks:
   - Benchmark comparison before/after optimization
   - Update README with new performance numbers

7. Errors and Fixes:
   - TypeError on naive datetime comparison — fixed with UTC aware
"""


def test_parse_compact_standard_format():
    """Standard compact summary with all 7 sections."""
    parsed = parse_compact_summary(_SAMPLE_COMPACT)

    assert parsed.primary_request is not None
    assert "optimize" in parsed.primary_request.lower() or "parser" in parsed.primary_request.lower()

    assert parsed.key_concepts is not None
    assert "JSONL" in parsed.key_concepts

    assert parsed.files_and_code is not None
    assert "parser.py" in parsed.files_and_code

    assert parsed.current_work is not None
    assert "batch" in parsed.current_work.lower() or "parsing" in parsed.current_work.lower()

    assert parsed.pending_tasks is not None
    assert "benchmark" in parsed.pending_tasks.lower() or "Benchmark" in parsed.pending_tasks

    assert parsed.problem_solving is not None
    assert "ReDoS" in parsed.problem_solving

    assert parsed.errors_and_fixes is not None


def test_parse_compact_empty():
    """Empty or too-short input returns empty result."""
    result = parse_compact_summary("")
    assert result.primary_request is None

    result = parse_compact_summary("short")
    assert result.primary_request is None


def test_parse_compact_no_sections():
    """Input without numbered sections stores everything as primary_request."""
    text = "This is a free-form compact summary without any numbered sections. It describes the user's work."
    result = parse_compact_summary(text)
    assert result.primary_request is not None
    assert "free-form" in result.primary_request


def test_parse_compact_preserves_raw_text():
    """raw_text always stores the full original text."""
    parsed = parse_compact_summary(_SAMPLE_COMPACT)
    assert parsed.raw_text == _SAMPLE_COMPACT


# ─── extract_milestones_from_compact tests ────────────────────────────────────


def test_milestones_from_compact_full():
    """Extract milestones from a fully-populated compact summary."""
    parsed = parse_compact_summary(_SAMPLE_COMPACT)
    milestones = extract_milestones_from_compact(parsed)

    assert len(milestones) == 4  # goal + solved + in_progress + pending

    # First milestone: goal (DONE)
    assert milestones[0].status == MilestoneStatus.DONE
    assert "目标" in milestones[0].label

    # Second: solved (DONE)
    assert milestones[1].status == MilestoneStatus.DONE
    assert "已解决" in milestones[1].label

    # Third: in progress (IN_PROGRESS / WIP)
    assert milestones[2].status == MilestoneStatus.IN_PROGRESS

    # Fourth: pending (PENDING)
    assert milestones[3].status == MilestoneStatus.PENDING


def test_milestones_from_compact_partial():
    """Extract milestones when only some sections exist."""
    parsed = CompactSummaryParsed(
        primary_request="User wants to fix a bug",
        current_work="Debugging the parser",
    )
    milestones = extract_milestones_from_compact(parsed)

    assert len(milestones) == 2
    assert milestones[0].status == MilestoneStatus.DONE
    assert milestones[1].status == MilestoneStatus.IN_PROGRESS


def test_milestones_from_compact_empty():
    """No sections → no milestones."""
    parsed = CompactSummaryParsed()
    milestones = extract_milestones_from_compact(parsed)
    assert len(milestones) == 0


def test_milestones_sub_items():
    """Sub-items are extracted from bullet lists."""
    parsed = parse_compact_summary(_SAMPLE_COMPACT)
    milestones = extract_milestones_from_compact(parsed)

    # Goal milestone should have sub-items from primary_request bullets
    goal_ms = milestones[0]
    assert len(goal_ms.sub_items) > 0

    # Pending milestone should have sub-items
    pending_ms = milestones[3]
    assert len(pending_ms.sub_items) > 0
