"""JSONL parser for Claude Code session files.

Parses the .jsonl files stored in ~/.claude/projects/{encoded-path}/
Each line is a JSON object with one of these types:
  - worktree-state: Session metadata (worktreeSession, sessionId)
  - file-history-snapshot: State snapshot (ignored)
  - user / assistant: Conversation messages
  - system: API errors, retries, etc. (ignored)
  - last-prompt: Last user prompt text (ignored)

Message content can be:
  - A plain string (common for user messages)
  - A list of content blocks, each with a "type" field:
    - "text": Has "text" field → extracted as content
    - "tool_use": Tool call (skipped for text extraction)
    - "tool_result": Tool result (skipped for text extraction)
    - "thinking": Model reasoning (skipped for text extraction)
"""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ccsm.models.session import JSONLMessage, SessionInfo

logger = logging.getLogger(__name__)

# ─── Message types to extract ────────────────────────────────────────────────

_MESSAGE_TYPES = frozenset({"user", "assistant"})

# ─── Content extraction ──────────────────────────────────────────────────────


def _extract_text(content) -> str:
    """Flatten message.content (string or list of blocks) into plain text.

    Only extracts "text" type blocks; tool_use, tool_result, and thinking
    blocks are skipped since they don't contain user-visible conversation text.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return ""


def _sanitize_content(text: str) -> Optional[str]:
    """Strip system-injected XML tags and noise from user content for display.

    Removes: <command-message>, <system-reminder>, <task-notification>,
    <local-command-stdout>, <command-name>, <command-args>, <*> etc.
    """
    if not text:
        return text
    # Remove antml: namespaced tags (e.g. <function_calls>, <invoke>)
    text = re.sub(r'</?antml:[a-z_]+[^>]{0,200}>', '', text, flags=re.IGNORECASE)
    # Remove complete XML tag blocks (non-greedy, length-bounded to prevent ReDoS)
    text = re.sub(r'<[a-z_:-][^>]{0,200}>.*?</[a-z_:-][^>]{0,50}>', '', text, flags=re.DOTALL | re.IGNORECASE)
    # Remove remaining self-closing and orphan tags
    text = re.sub(r'</?[a-z_:-][^>]{0,200}>', '', text, flags=re.IGNORECASE)
    # Remove system-injected boilerplate lines
    text = re.sub(
        r'^(Base directory for this skill:?\s*.+|'
        r'This session is being continued.+|'
        r'If you need specific details from before compaction.+|'
        r'Recent messages are preserved verbatim.+|'
        r'Continue the conversation from where it left off.+|'
        r'Caveat: The messages below.+|'
        r'Copied to clipboard.+|'
        r'Compacted \(ctrl.+|'
        r'ARGUMENTS:?\s*.*)$',
        '', text, flags=re.MULTILINE | re.IGNORECASE,
    )
    # Clean up excessive whitespace
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = text.strip()
    # If nothing meaningful remains after cleaning
    if not text or len(text) < 3:
        return None
    return text


def _parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string into a timezone-aware datetime."""
    if not ts_str:
        return None
    try:
        # Handle both "Z" suffix and "+00:00" format
        ts_str = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def _parse_message_line(data: dict) -> Optional[JSONLMessage]:
    """Parse a user/assistant JSON line into a JSONLMessage.

    Returns None if the line is not a valid message or has no content.
    """
    msg_type = data.get("type")
    if msg_type not in _MESSAGE_TYPES:
        return None

    uuid = data.get("uuid")
    if not uuid:
        return None

    timestamp = _parse_timestamp(data.get("timestamp"))
    if not timestamp:
        return None

    message = data.get("message", {})
    content = _extract_text(message.get("content", ""))

    return JSONLMessage(
        uuid=uuid,
        parent_uuid=data.get("parentUuid"),
        role=msg_type,
        content=content,
        timestamp=timestamp,
        is_sidechain=data.get("isSidechain", False),
    )


# ─── File reading helpers ────────────────────────────────────────────────────


def _read_lines(path: Path) -> list[str]:
    """Read all lines from a JSONL file. Returns empty list on error."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.readlines()
    except (OSError, IOError) as e:
        logger.warning("Failed to read %s: %s", path, e)
        return []


def _read_tail_lines(path: Path, n: int = 50) -> list[str]:
    """Read the last N lines of a file efficiently.

    Uses seek-from-end strategy for large files.
    """
    try:
        file_size = path.stat().st_size
        if file_size == 0:
            return []

        # For small files, just read everything
        if file_size < 64 * 1024:
            with open(path, "r", encoding="utf-8") as f:
                return f.readlines()[-n:]

        # For large files, read progressively from the end
        chunk_size = min(file_size, 8 * 1024)
        lines: list[str] = []
        with open(path, "rb") as f:
            while len(lines) <= n and chunk_size <= file_size:
                f.seek(-chunk_size, os.SEEK_END)
                raw = f.read(chunk_size)
                lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
                if len(lines) > n:
                    break
                chunk_size = min(chunk_size * 2, file_size)

        return lines[-n:]
    except (OSError, IOError) as e:
        logger.warning("Failed to tail-read %s: %s", path, e)
        return []


# ─── Public API ──────────────────────────────────────────────────────────────


def parse_session_info(jsonl_path: Path) -> SessionInfo:
    """Quick scan of a JSONL file to extract session metadata.

    Strategy: read the first line and the last ~50 lines to extract:
    - session_id: from worktreeSession line or any message's sessionId field
    - slug: from assistant messages (only some have it)
    - first_timestamp / last_timestamp
    - message_count / user_message_count (approximate from full scan of lines)
    - cwd, git_branch

    This avoids loading full message content, keeping it lightweight for
    listing views.
    """
    session_id = jsonl_path.stem  # fallback: filename without .jsonl
    slug: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    message_count = 0
    user_message_count = 0

    # Content signals for NOISE detection and card display
    first_user_content: Optional[str] = None
    total_user_chars = 0
    all_slash_commands = True  # Assume true, set false on first non-slash user msg

    # New metadata fields (Task 2+6)
    custom_title: Optional[str] = None
    ai_title: Optional[str] = None
    forked_from: Optional[str] = None

    lines = _read_lines(jsonl_path)

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        # Extract session_id from worktreeSession or sessionId field
        if "worktreeSession" in data:
            ws = data.get("worktreeSession")
            sid = data.get("sessionId")
            if sid:
                session_id = sid
            elif isinstance(ws, dict):
                sid = ws.get("sessionId")
                if sid:
                    session_id = sid
            continue

        msg_type = data.get("type")

        # ── Parse special metadata entry types ────────────────────────────
        if msg_type == "custom-title":
            if custom_title is None:
                custom_title = data.get("title") or data.get("customTitle")
            continue

        if msg_type == "ai-title":
            if ai_title is None:
                ai_title = data.get("title") or data.get("aiTitle")
            continue

        # ── Extract forkedFrom from any entry ─────────────────────────────
        if forked_from is None:
            fk = data.get("forkedFrom")
            if isinstance(fk, dict):
                fk_sid = fk.get("sessionId")
                if fk_sid:
                    forked_from = fk_sid

        # Skip non-message lines
        if msg_type not in _MESSAGE_TYPES:
            continue

        # Skip compact summary messages and meta messages (not real user input)
        if data.get("isCompactSummary") or data.get("isMeta"):
            continue

        # Count messages
        message_count += 1
        if msg_type == "user":
            user_message_count += 1

            # Extract user message content for NOISE detection & card display
            msg_obj = data.get("message") or {}
            msg_content = _extract_text(msg_obj.get("content", ""))
            total_user_chars += len(msg_content)

            # Track first user message (for card display)
            if first_user_content is None and msg_content.strip():
                cleaned = _sanitize_content(msg_content.strip())
                if cleaned:
                    first_user_content = cleaned[:200]

            # Track if all user messages are slash commands
            stripped = msg_content.strip()
            if stripped and not stripped.startswith("/"):
                all_slash_commands = False

        # Extract sessionId from any message line (first occurrence wins)
        sid = data.get("sessionId")
        if sid and session_id == jsonl_path.stem:
            session_id = sid

        # Extract slug (assistant messages may have it)
        if not slug and data.get("slug"):
            slug = data["slug"]

        # Extract cwd and git_branch (use last seen values)
        if data.get("cwd"):
            cwd = data["cwd"]
        if data.get("gitBranch"):
            git_branch = data["gitBranch"]

        # Track timestamps
        ts = _parse_timestamp(data.get("timestamp"))
        if ts:
            if first_timestamp is None or ts < first_timestamp:
                first_timestamp = ts
            if last_timestamp is None or ts > last_timestamp:
                last_timestamp = ts

    # If no user messages, all_slash_commands should be False
    if user_message_count == 0:
        all_slash_commands = False

    return SessionInfo(
        session_id=session_id,
        project_dir=str(jsonl_path.parent.name),
        jsonl_path=jsonl_path,
        slug=slug,
        cwd=cwd,
        git_branch=git_branch,
        first_timestamp=first_timestamp,
        last_timestamp=last_timestamp,
        message_count=message_count,
        user_message_count=user_message_count,
        first_user_content=first_user_content,
        total_user_chars=total_user_chars,
        all_slash_commands=all_slash_commands,
        custom_title=custom_title,
        ai_title_from_cc=ai_title,
        forked_from_session=forked_from,
    )


def parse_session_messages(jsonl_path: Path) -> list[JSONLMessage]:
    """Parse all user/assistant messages from a JSONL file.

    Used for the Detail view where full message content is needed.
    Skips snapshot, worktreeSession, system, and last-prompt lines.
    Content (string or list of blocks) is flattened to plain text.

    Messages are returned in file order (chronological).
    """
    messages: list[JSONLMessage] = []
    lines = _read_lines(jsonl_path)

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        msg = _parse_message_line(data)
        if msg is not None:
            messages.append(msg)

    return messages


def get_last_assistant_messages(
    jsonl_path: Path, count: int = 5
) -> list[JSONLMessage]:
    """Extract the last N assistant messages from a JSONL file.

    Scans from the file tail for efficiency. Used for the
    "Claude's last replies" panel in the TUI.

    Returns messages in chronological order (oldest first).
    """
    tail_lines = _read_tail_lines(jsonl_path, n=count * 10)

    # Parse assistant messages from tail
    assistant_msgs: list[JSONLMessage] = []
    for line in tail_lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        if data.get("type") != "assistant":
            continue

        msg = _parse_message_line(data)
        if msg is not None and msg.content:  # Skip tool-only messages
            assistant_msgs.append(msg)

    # Return last N in chronological order
    return assistant_msgs[-count:]


# ─── Lightweight timestamp extraction ──────────────────────────────────────────


@dataclass
class SessionTimestamps:
    """Lightweight timestamp extraction result."""
    first_message_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    compact_count: int = 0


def parse_session_timestamps(jsonl_path: Path) -> SessionTimestamps:
    """Fast extraction of message timestamps and compact boundaries.

    Reads only timestamp and type fields — skips message content parsing.
    Used to get last_message_at for correct timeline ordering (pain point #6).

    Only user/assistant messages contribute to timestamps.
    Metadata lines (custom-title, last-prompt, etc.) are ignored.
    """
    result = SessionTimestamps()
    lines = _read_lines(jsonl_path)

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        entry_type = data.get("type", "")

        # Compact boundary detection
        if entry_type == "system" and data.get("subtype") == "compact_boundary":
            result.compact_count += 1
            continue

        # Only user/assistant messages contribute to timestamp
        if entry_type not in ("user", "assistant"):
            continue

        ts = _parse_timestamp(data.get("timestamp"))
        if ts is None:
            continue

        if result.first_message_at is None or ts < result.first_message_at:
            result.first_message_at = ts
        if result.last_message_at is None or ts > result.last_message_at:
            result.last_message_at = ts

    return result
