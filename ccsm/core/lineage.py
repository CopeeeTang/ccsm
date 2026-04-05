"""Cross-session lineage detection from JSONL files.

Detects fork, compaction, and duplicate signals by scanning JSONL
session files and builds a lineage DAG (directed acyclic graph).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ccsm.models.session import LineageType, SessionLineage


# ─── Compact summary prefixes (case-insensitive startswith) ──────────────────

_COMPACT_SUMMARY_PREFIXES = (
    "Here is a summary of the conversation",
    "Here's a summary of the conversation",
    "Here is a summary of our conversation",
    "Here's a summary of our conversation",
    # v3+ compact summaries
    "This session is being continued from a previous conversation",
)

# Branch suffix pattern: "(branch)" or "(Branch 2)" etc., case-insensitive
_BRANCH_SUFFIX = re.compile(r'\(branch(?:\s+\d+)?\)\s*$', re.IGNORECASE)

# Maximum gap (seconds) between sessions to consider them duplicates
_DUPLICATE_GAP_THRESHOLD = 300  # 5 minutes


# ─── Lineage Signals ─────────────────────────────────────────────────────────


@dataclass
class LineageSignals:
    """Raw lineage signals extracted from a single JSONL file."""

    session_id: Optional[str] = None
    is_fork: bool = False
    fork_hint: Optional[str] = None
    fork_source_id: Optional[str] = None  # sessionId from forkedFrom field
    has_compact_boundary: bool = False
    compact_count: int = 0
    last_message_at: Optional[datetime] = None
    first_message_at: Optional[datetime] = None
    first_user_content: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None


# ─── Signal Extraction ───────────────────────────────────────────────────────


def extract_signals_from_lines(
    lines: list[str],
    display_name: Optional[str] = None,
) -> LineageSignals:
    """Extract lineage signals from pre-read JSONL lines.

    Same logic as parse_lineage_signals() but avoids file I/O by
    accepting already-read lines.  Used by parse_session_complete()
    to eliminate the second file read.
    """
    signals = LineageSignals()

    if display_name and _BRANCH_SUFFIX.search(display_name):
        signals.is_fork = True
        signals.fork_hint = "display_name_branch_suffix"

    first_user_seen = False

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            entry = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        # Extract sessionId (first occurrence wins)
        if signals.session_id is None and "sessionId" in entry:
            signals.session_id = entry["sessionId"]

        # Extract cwd / gitBranch (last seen wins)
        if "cwd" in entry:
            signals.cwd = entry["cwd"]
        if "gitBranch" in entry:
            signals.git_branch = entry["gitBranch"]

        entry_type = entry.get("type", "")
        entry_subtype = entry.get("subtype", "")

        # Compact boundary detection (including microcompact_boundary subtype)
        if entry_type == "system" and entry_subtype in (
            "compact_boundary",
            "microcompact_boundary",
        ):
            signals.compact_count += 1
            signals.has_compact_boundary = True

        # forkedFrom field detection (any message type)
        if not signals.is_fork:
            forked_from = entry.get("forkedFrom")
            if isinstance(forked_from, dict):
                forked_session_id = forked_from.get("sessionId")
                if forked_session_id:
                    signals.is_fork = True
                    signals.fork_hint = "forkedFrom_field"
                    signals.fork_source_id = forked_session_id

        # Timestamp tracking for user/assistant messages
        if entry_type in ("user", "assistant"):
            ts = _parse_timestamp(entry)
            if ts is not None:
                if signals.first_message_at is None or ts < signals.first_message_at:
                    signals.first_message_at = ts
                if signals.last_message_at is None or ts > signals.last_message_at:
                    signals.last_message_at = ts

        # First user message analysis
        if entry_type == "user" and not first_user_seen:
            first_user_seen = True
            content = _extract_content(entry)
            if content:
                signals.first_user_content = content
                # Check for compact summary as first user message
                for prefix in _COMPACT_SUMMARY_PREFIXES:
                    if content.startswith(prefix):
                        signals.is_fork = True
                        signals.fork_hint = "compact_summary_first_message"
                        break

    return signals


def parse_lineage_signals(
    jsonl_path: Path,
    display_name: Optional[str] = None,
) -> LineageSignals:
    """Parse a JSONL session file and extract lineage signals.

    Args:
        jsonl_path: Path to the JSONL file.
        display_name: Optional display name from history.jsonl
                      (e.g. "fix-bug (branch)").

    Returns:
        LineageSignals with all detected signals populated.
    """
    if not jsonl_path.exists():
        signals = LineageSignals()
        if display_name and _BRANCH_SUFFIX.search(display_name):
            signals.is_fork = True
            signals.fork_hint = "display_name_branch_suffix"
        return signals

    with open(jsonl_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    return extract_signals_from_lines(lines, display_name=display_name)


# ─── Graph Construction ──────────────────────────────────────────────────────


def build_lineage_graph(
    signals_map: dict[str, LineageSignals],
) -> dict[str, SessionLineage]:
    """Build a lineage DAG from per-session signals.

    Args:
        signals_map: Mapping of session_id → LineageSignals.

    Returns:
        Mapping of session_id → SessionLineage with parent/child links,
        lineage types, and depth values assigned.
    """
    graph: dict[str, SessionLineage] = {}

    # ── Phase 1: Create nodes from individual signals ────────────────────
    for sid, sig in signals_map.items():
        lineage_type = LineageType.FORK if sig.is_fork else LineageType.ROOT
        node = SessionLineage(
            session_id=sid,
            lineage_type=lineage_type,
            fork_label=sig.fork_hint if sig.is_fork else None,
        )
        graph[sid] = node

    # ── Phase 1.5: Mark compact sessions ────────────────────────────────
    # Sessions with compact_boundary are marked COMPACT (unless already FORK).
    # Compact sessions are continuations within the same JSONL file, so they
    # don't have a cross-file parent — but we record the signal for the DAG.
    for sid, sig in signals_map.items():
        if sig.has_compact_boundary and graph[sid].lineage_type == LineageType.ROOT:
            graph[sid].lineage_type = LineageType.COMPACT
            graph[sid].compact_predecessor = sid  # self-reference: compacted in-place

    # ── Phase 2: Detect duplicates ───────────────────────────────────────
    # Group sessions by (cwd, git_branch)
    groups: dict[tuple[Optional[str], Optional[str]], list[str]] = {}
    for sid, sig in signals_map.items():
        key = (sig.cwd, sig.git_branch)
        groups.setdefault(key, []).append(sid)

    for key, sids in groups.items():
        if len(sids) < 2:
            continue
        # Skip groups where cwd and branch are both None
        if key[0] is None and key[1] is None:
            continue

        # Sort by first_message_at (None goes to end)
        def _sort_key(sid: str) -> datetime:
            ts = signals_map[sid].first_message_at
            return ts if ts is not None else datetime.max.replace(tzinfo=timezone.utc)

        sorted_sids = sorted(sids, key=_sort_key)

        for i in range(1, len(sorted_sids)):
            prev_sid = sorted_sids[i - 1]
            curr_sid = sorted_sids[i]

            prev_sig = signals_map[prev_sid]
            curr_sig = signals_map[curr_sid]

            # Skip if already marked as fork (preserve fork signals)
            if graph[curr_sid].lineage_type == LineageType.FORK:
                continue

            prev_last = prev_sig.last_message_at
            curr_first = curr_sig.first_message_at

            if prev_last is not None and curr_first is not None:
                # Negative gap = sessions overlap → definitely duplicate
                # Small positive gap (< threshold) = near-overlap → likely duplicate
                gap = (curr_first - prev_last).total_seconds()
                if gap < _DUPLICATE_GAP_THRESHOLD:
                    graph[curr_sid].lineage_type = LineageType.DUPLICATE
                    graph[curr_sid].parent_id = prev_sid
                    graph[prev_sid].children.append(curr_sid)

    # ── Phase 3: Assign depth ────────────────────────────────────────────
    # Walk from roots (nodes with no parent_id)
    visited: set[str] = set()

    def _assign_depth(sid: str, depth: int) -> None:
        if sid in visited:
            return
        visited.add(sid)
        graph[sid].depth = depth
        for child_id in graph[sid].children:
            if child_id in graph:
                _assign_depth(child_id, depth + 1)

    for sid, node in graph.items():
        if node.parent_id is None:
            _assign_depth(sid, 0)

    # Assign depth 0 to any unvisited nodes (disconnected)
    for sid in graph:
        if sid not in visited:
            graph[sid].depth = 0

    return graph


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _parse_timestamp(entry: dict) -> Optional[datetime]:
    """Extract and parse a timestamp from a JSONL entry."""
    ts_raw = entry.get("timestamp")
    if ts_raw is None:
        return None
    if isinstance(ts_raw, (int, float)):
        # Unix epoch (seconds or milliseconds)
        if ts_raw > 1e12:
            ts_raw = ts_raw / 1000.0
        return datetime.fromtimestamp(ts_raw, tz=timezone.utc)
    if isinstance(ts_raw, str):
        try:
            return datetime.fromisoformat(ts_raw)
        except ValueError:
            return None
    return None


def _extract_content(entry: dict) -> Optional[str]:
    """Extract text content from a JSONL message entry."""
    message = entry.get("message", entry)
    content = message.get("content")
    if content is None:
        return None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        # Flatten content blocks
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") == "text":
                parts.append(block.get("text", ""))
        return "".join(parts) if parts else None
    return None
