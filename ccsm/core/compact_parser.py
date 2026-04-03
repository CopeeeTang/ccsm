"""Compact summary parser — extracts structured sections from Claude Code's compact summaries.

Claude Code generates structured summaries during the compact operation with
remarkably consistent section headers (97.9% coverage across 95 samples):

  1. Primary Request and Intent
  2. Key Technical Concepts
  3. Files and Code Sections
  4. Current Work
  5. Pending Tasks
  6. Problem Solving
  7. Errors and Fixes (~97% — sometimes "Errors and fixes" lowercase)

This module parses raw compact summary text into CompactSummaryParsed,
then extracts Milestones from the structured sections.
"""

from __future__ import annotations

import re
from typing import Optional

from ccsm.models.session import (
    CompactSummaryParsed,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
)

# ─── Section title patterns ─────────────────────────────────────────────────
# Matches numbered sections like "1. Primary Request and Intent:"
# or "5. Pending Tasks:"
# Also handles bold markdown variants "**Section**"

_SECTION_PATTERN = re.compile(
    r"^\d+\.\s+\*{0,2}(.*?)\*{0,2}\s*:?\s*$",
    re.MULTILINE,
)

# Canonical section name mapping (handles casing and minor variations)
_SECTION_MAP = {
    "primary request and intent": "primary_request",
    "primary request": "primary_request",
    "key technical concepts": "key_concepts",
    "technical concepts": "key_concepts",
    "files and code sections": "files_and_code",
    "files and code": "files_and_code",
    "current work": "current_work",
    "pending tasks": "pending_tasks",
    "optional next step": "pending_tasks",  # Treat as pending
    "optional next steps": "pending_tasks",
    "problem solving": "problem_solving",
    "errors and fixes": "errors_and_fixes",
    "errors and fix": "errors_and_fixes",
}


def parse_compact_summary(raw_text: str) -> CompactSummaryParsed:
    """Parse a Claude Code compact summary into structured sections.

    The parser splits the text at numbered section headers and assigns
    each section's content to the corresponding field. Sections not
    matching known patterns are ignored (their content is still in raw_text).

    Args:
        raw_text: Full text of an isCompactSummary message.

    Returns:
        CompactSummaryParsed with each section populated (or None if absent).
    """
    result = CompactSummaryParsed(raw_text=raw_text)

    if not raw_text or len(raw_text) < 50:
        return result

    # Find all section headers and their positions
    matches = list(_SECTION_PATTERN.finditer(raw_text))
    if not matches:
        # No structured sections — store everything as primary_request
        result.primary_request = raw_text.strip()
        return result

    # Extract content between consecutive section headers
    for i, match in enumerate(matches):
        section_title = match.group(1).strip().lower()
        content_start = match.end()
        content_end = matches[i + 1].start() if i + 1 < len(matches) else len(raw_text)
        content = raw_text[content_start:content_end].strip()

        if not content:
            continue

        # Map to canonical field name
        field_name = _SECTION_MAP.get(section_title)
        if field_name is None:
            # Try partial matching for slightly different titles
            for key, val in _SECTION_MAP.items():
                if key in section_title or section_title in key:
                    field_name = val
                    break

        if field_name and hasattr(result, field_name):
            setattr(result, field_name, content)

    return result


# ─── Milestone extraction from compact summary ──────────────────────────────


def _parse_bullet_items(text: str) -> list[str]:
    """Extract bullet items from a text section.

    Handles:
      - dash bullets: "- Item text"
      - asterisk bullets: "* Item text"
      - numbered sub-items: "  1. Sub-item"
    """
    items: list[str] = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        # Remove leading bullets/numbers
        cleaned = re.sub(r"^[-*•]\s+", "", line)
        cleaned = re.sub(r"^\d+\.\s+", "", cleaned)
        if cleaned and len(cleaned) > 3:
            items.append(cleaned)
    return items


def extract_milestones_from_compact(
    parsed: CompactSummaryParsed,
) -> list[Milestone]:
    """Generate milestones from compact summary's structured sections.

    Strategy:
    - Primary Request → first DONE milestone (the goal/background)
    - Problem Solving (resolved items) → DONE milestones
    - Current Work → IN_PROGRESS milestone
    - Pending Tasks → PENDING milestones

    This provides milestones at zero API cost for sessions that have
    been compacted.
    """
    milestones: list[Milestone] = []

    # 1. Primary Request as the first (background/goal) milestone
    if parsed.primary_request:
        # Take first 2 lines as description
        lines = [l.strip() for l in parsed.primary_request.split("\n") if l.strip()]
        detail = lines[0][:80] if lines else None
        sub_items = []
        for line in lines[1:4]:  # Up to 3 sub-items
            cleaned = re.sub(r"^[-*•]\s+", "", line).strip()
            if cleaned and len(cleaned) > 3:
                sub_items.append(MilestoneItem(
                    label=cleaned[:60],
                    status=MilestoneStatus.DONE,
                ))

        milestones.append(Milestone(
            label="🎯 目标",
            detail=detail,
            status=MilestoneStatus.DONE,
            sub_items=sub_items,
        ))

    # 2. Problem Solving → DONE milestones (problems already resolved)
    if parsed.problem_solving:
        items = _parse_bullet_items(parsed.problem_solving)
        if items:
            sub_items = [
                MilestoneItem(label=item[:60], status=MilestoneStatus.DONE)
                for item in items[:5]
            ]
            milestones.append(Milestone(
                label="🔧 已解决",
                detail=f"{len(items)} 个问题已处理",
                status=MilestoneStatus.DONE,
                sub_items=sub_items,
            ))

    # 3. Current Work → IN_PROGRESS milestone
    if parsed.current_work:
        items = _parse_bullet_items(parsed.current_work)
        detail = items[0][:80] if items else parsed.current_work.split("\n")[0][:80]
        sub_items = [
            MilestoneItem(label=item[:60], status=MilestoneStatus.IN_PROGRESS)
            for item in items[1:4]
        ]
        milestones.append(Milestone(
            label="▶ 进行中",
            detail=detail,
            status=MilestoneStatus.IN_PROGRESS,
            sub_items=sub_items,
        ))

    # 4. Pending Tasks → PENDING milestones
    if parsed.pending_tasks:
        items = _parse_bullet_items(parsed.pending_tasks)
        if items:
            sub_items = [
                MilestoneItem(label=item[:60], status=MilestoneStatus.PENDING)
                for item in items[:5]
            ]
            milestones.append(Milestone(
                label="○ 待完成",
                detail=f"{len(items)} 项待处理",
                status=MilestoneStatus.PENDING,
                sub_items=sub_items,
            ))

    return milestones
