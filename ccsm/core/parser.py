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

from ccsm.models.session import JSONLMessage, SessionDetailData, SessionInfo
from ccsm.core.lineage import (
    BRANCH_SUFFIX_RE,
    COMPACT_SUMMARY_PREFIXES,
    LineageSignals,
)

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
                if chunk_size >= file_size:
                    # Already reading entire file — can't get more lines
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

    # New: previously-skipped high-value data
    last_prompt: Optional[str] = None
    compact_summaries: list[str] = []
    model_name: Optional[str] = None
    total_input_tokens = 0
    total_output_tokens = 0
    last_user_message: Optional[str] = None

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

        # ── Extract last-prompt (user's last input before session end) ──
        if msg_type == "last-prompt":
            lp = data.get("lastPrompt")
            if lp and isinstance(lp, str) and len(lp.strip()) > 2:
                last_prompt = lp.strip()
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

        # ── Extract isCompactSummary content (previously skipped!) ──────
        if data.get("isCompactSummary"):
            msg_obj = data.get("message") or {}
            cs_content = _extract_text(msg_obj.get("content", ""))
            if cs_content and len(cs_content) > 50:
                compact_summaries.append(cs_content)
            continue

        # Skip meta messages (not real user input)
        if data.get("isMeta"):
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

            # Track last user message (for WHERE YOU LEFT OFF)
            stripped_content = msg_content.strip()
            if stripped_content and not stripped_content.startswith("/"):
                cleaned_last = _sanitize_content(stripped_content)
                if cleaned_last and len(cleaned_last) > 5:
                    last_user_message = cleaned_last[:500]

            # Track if all user messages are slash commands
            stripped = msg_content.strip()
            if stripped and not stripped.startswith("/"):
                all_slash_commands = False

        elif msg_type == "assistant":
            # Extract model name and token usage
            msg_obj = data.get("message") or {}
            m = msg_obj.get("model")
            if m:
                model_name = m
            usage = msg_obj.get("usage")
            if isinstance(usage, dict):
                total_input_tokens += usage.get("input_tokens", 0)
                total_output_tokens += usage.get("output_tokens", 0)

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
        last_prompt=last_prompt,
        compact_summaries=compact_summaries,
        model_name=model_name,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        last_user_message=last_user_message,
    )


@dataclass
class FullParseResult:
    """Combined result from single-pass JSONL parsing.

    Merges the data that was previously extracted by three separate
    file reads: parse_session_info + parse_lineage_signals +
    get_last_assistant_messages.
    """
    info: SessionInfo
    lineage: LineageSignals
    last_thought: str  # last assistant message content[:200]


def parse_session_full(
    jsonl_path: Path,
    display_name: str | None = None,
) -> FullParseResult:
    """Single-pass JSONL parse: extract info + lineage + last_thought in one read.

    Replaces the triple-read pattern of calling parse_session_info(),
    parse_lineage_signals(), and get_last_assistant_messages() separately.
    Reduces file I/O by ~66%.
    """
    # ── SessionInfo state ──
    session_id = jsonl_path.stem
    slug: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    message_count = 0
    user_message_count = 0
    first_user_content: Optional[str] = None
    total_user_chars = 0
    all_slash_commands = True
    custom_title: Optional[str] = None
    ai_title: Optional[str] = None
    forked_from: Optional[str] = None
    last_prompt: Optional[str] = None
    compact_summaries: list[str] = []
    model_name: Optional[str] = None
    total_input_tokens = 0
    total_output_tokens = 0
    last_user_message: Optional[str] = None

    # ── LineageSignals state ──
    signals = LineageSignals()
    if display_name and BRANCH_SUFFIX_RE.search(display_name):
        signals.is_fork = True
        signals.fork_hint = "display_name_branch_suffix"
    first_user_seen = False

    # ── Last thought state ──
    last_thought = ""

    # ── Single file read ──
    if not jsonl_path.exists():
        info = SessionInfo(
            session_id=session_id,
            project_dir=str(jsonl_path.parent.name),
            jsonl_path=jsonl_path,
        )
        return FullParseResult(info=info, lineage=signals, last_thought="")

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    data = json.loads(raw_line)
                except (json.JSONDecodeError, ValueError):
                    continue

                # ── worktreeSession: extract session_id ──
                if "worktreeSession" in data:
                    ws = data.get("worktreeSession")
                    sid = data.get("sessionId")
                    if sid:
                        session_id = sid
                        if signals.session_id is None:
                            signals.session_id = sid
                    elif isinstance(ws, dict):
                        sid = ws.get("sessionId")
                        if sid:
                            session_id = sid
                            if signals.session_id is None:
                                signals.session_id = sid
                    continue

                msg_type = data.get("type", "")
                entry_subtype = data.get("subtype", "")

                # ── Lineage: session_id from any entry ──
                if signals.session_id is None and "sessionId" in data:
                    signals.session_id = data["sessionId"]

                # ── Lineage: cwd / gitBranch (last wins) ──
                if "cwd" in data:
                    cwd = data["cwd"]
                    signals.cwd = data["cwd"]
                if "gitBranch" in data:
                    git_branch = data["gitBranch"]
                    signals.git_branch = data["gitBranch"]

                # ── Lineage: compact boundary ──
                if msg_type == "system" and entry_subtype in (
                    "compact_boundary", "microcompact_boundary",
                ):
                    signals.compact_count += 1
                    signals.has_compact_boundary = True

                # ── Lineage: forkedFrom ──
                if not signals.is_fork:
                    forked_from_entry = data.get("forkedFrom")
                    if isinstance(forked_from_entry, dict):
                        fk_sid = forked_from_entry.get("sessionId")
                        if fk_sid:
                            signals.is_fork = True
                            signals.fork_hint = "forkedFrom_field"
                            signals.fork_source_id = fk_sid

                # ── Info: forkedFrom (first occurrence) ──
                if forked_from is None:
                    fk = data.get("forkedFrom")
                    if isinstance(fk, dict):
                        fk_sid = fk.get("sessionId")
                        if fk_sid:
                            forked_from = fk_sid

                # ── Info: special metadata types ──
                if msg_type == "custom-title":
                    if custom_title is None:
                        custom_title = data.get("title") or data.get("customTitle")
                    continue
                if msg_type == "ai-title":
                    if ai_title is None:
                        ai_title = data.get("title") or data.get("aiTitle")
                    continue
                if msg_type == "last-prompt":
                    lp = data.get("lastPrompt")
                    if lp and isinstance(lp, str) and len(lp.strip()) > 2:
                        last_prompt = lp.strip()
                    continue

                # ── Skip non-message lines ──
                if msg_type not in _MESSAGE_TYPES:
                    continue

                # ── Info: compact summaries ──
                if data.get("isCompactSummary"):
                    msg_obj = data.get("message") or {}
                    cs_content = _extract_text(msg_obj.get("content", ""))
                    if cs_content and len(cs_content) > 50:
                        compact_summaries.append(cs_content)
                    continue

                # Skip meta messages
                if data.get("isMeta"):
                    continue

                # ── Count messages ──
                message_count += 1

                # ── Timestamps (shared by info + lineage) ──
                ts = _parse_timestamp(data.get("timestamp"))
                if ts:
                    if first_timestamp is None or ts < first_timestamp:
                        first_timestamp = ts
                    if last_timestamp is None or ts > last_timestamp:
                        last_timestamp = ts
                    if signals.first_message_at is None or ts < signals.first_message_at:
                        signals.first_message_at = ts
                    if signals.last_message_at is None or ts > signals.last_message_at:
                        signals.last_message_at = ts

                if msg_type == "user":
                    user_message_count += 1
                    msg_obj = data.get("message") or {}
                    msg_content = _extract_text(msg_obj.get("content", ""))
                    total_user_chars += len(msg_content)

                    if first_user_content is None and msg_content.strip():
                        cleaned = _sanitize_content(msg_content.strip())
                        if cleaned:
                            first_user_content = cleaned[:200]

                    stripped_content = msg_content.strip()
                    if stripped_content and not stripped_content.startswith("/"):
                        cleaned_last = _sanitize_content(stripped_content)
                        if cleaned_last and len(cleaned_last) > 5:
                            last_user_message = cleaned_last[:500]

                    stripped = msg_content.strip()
                    if stripped and not stripped.startswith("/"):
                        all_slash_commands = False

                    # ── Lineage: first user content analysis ──
                    if not first_user_seen:
                        first_user_seen = True
                        # Use lineage's content extraction for consistency
                        content_for_lineage = msg_content
                        if content_for_lineage:
                            signals.first_user_content = content_for_lineage
                            for prefix in COMPACT_SUMMARY_PREFIXES:
                                if content_for_lineage.startswith(prefix):
                                    signals.is_fork = True
                                    signals.fork_hint = "compact_summary_first_message"
                                    break

                elif msg_type == "assistant":
                    msg_obj = data.get("message") or {}
                    m = msg_obj.get("model")
                    if m:
                        model_name = m
                    usage = msg_obj.get("usage")
                    if isinstance(usage, dict):
                        total_input_tokens += usage.get("input_tokens", 0)
                        total_output_tokens += usage.get("output_tokens", 0)

                    # ── Track last assistant message (for last_thought) ──
                    content = _extract_text(msg_obj.get("content", ""))
                    if content and content.strip():
                        last_thought = content.strip()[:200]

                # ── Info: sessionId / slug from message lines ──
                sid = data.get("sessionId")
                if sid and session_id == jsonl_path.stem:
                    session_id = sid
                if not slug and data.get("slug"):
                    slug = data["slug"]

    except (OSError, IOError) as e:
        logger.warning("Failed to read %s: %s", jsonl_path, e)

    if user_message_count == 0:
        all_slash_commands = False

    info = SessionInfo(
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
        last_prompt=last_prompt,
        compact_summaries=compact_summaries,
        model_name=model_name,
        total_input_tokens=total_input_tokens,
        total_output_tokens=total_output_tokens,
        last_user_message=last_user_message,
    )

    return FullParseResult(info=info, lineage=signals, last_thought=last_thought)


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

        # Skip compact summaries and meta entries — not real conversation
        if data.get("isCompactSummary") or data.get("isMeta"):
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


# ─── Deep detail parsing (on-demand for Detail panel) ───────────────────────


def _summarize_tool_input(tool_name: str, tool_input: dict) -> Optional[str]:
    """Extract a short summary from a tool_use input dict.

    Returns file path for file operations, command for Bash, etc.
    """
    if not isinstance(tool_input, dict):
        return None

    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
        return tool_input.get("file_path") or tool_input.get("notebook_path")
    if tool_name == "Bash":
        cmd = tool_input.get("command", "")
        # Truncate long commands
        if len(cmd) > 100:
            cmd = cmd[:97] + "..."
        return cmd
    if tool_name == "Read":
        return tool_input.get("file_path")
    if tool_name in ("Grep", "Glob"):
        pattern = tool_input.get("pattern", "")
        path = tool_input.get("path", "")
        return f"{pattern} in {path}" if path else pattern
    if tool_name == "Agent":
        return tool_input.get("description") or tool_input.get("prompt", "")[:80]
    return None


def parse_session_detail(jsonl_path: Path) -> SessionDetailData:
    """Deep parse a JSONL file for the Detail panel.

    Extracts tool_use operations (edited files, bash commands, read files)
    and the last user+assistant exchange. Only called when a session is
    selected in the TUI — heavier than parse_session_info().
    """
    files_edited: set[str] = set()
    commands_run: list[str] = []
    files_read: set[str] = set()
    searches: set[str] = set()
    agents_spawned: list[str] = []
    last_user_msg: Optional[str] = None
    last_assistant_msg: Optional[str] = None

    session_id = jsonl_path.stem
    lines = _read_lines(jsonl_path)

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue

        msg_type = data.get("type")
        if msg_type not in _MESSAGE_TYPES:
            continue
        if data.get("isCompactSummary") or data.get("isMeta"):
            continue

        msg_obj = data.get("message") or {}

        if msg_type == "user":
            content = _extract_text(msg_obj.get("content", ""))
            cleaned = _sanitize_content(content.strip()) if content.strip() else None
            if cleaned and len(cleaned) > 5:
                last_user_msg = cleaned[:1000]

        elif msg_type == "assistant":
            # Extract text content
            text_content = _extract_text(msg_obj.get("content", ""))
            if text_content and len(text_content.strip()) > 10:
                last_assistant_msg = text_content.strip()[:1000]

            # Extract tool_use operations
            raw_content = msg_obj.get("content", [])
            if isinstance(raw_content, list):
                for block in raw_content:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") != "tool_use":
                        continue
                    tool_name = block.get("name", "")
                    tool_input = block.get("input", {})
                    summary = _summarize_tool_input(tool_name, tool_input)
                    if not summary:
                        continue

                    if tool_name in ("Edit", "Write", "MultiEdit", "NotebookEdit"):
                        files_edited.add(summary)
                    elif tool_name == "Bash":
                        if len(commands_run) < 20:  # Cap to avoid spam
                            commands_run.append(summary)
                    elif tool_name == "Read":
                        files_read.add(summary)
                    elif tool_name in ("Grep", "Glob"):
                        searches.add(summary)
                    elif tool_name == "Agent":
                        if len(agents_spawned) < 10:
                            agents_spawned.append(summary)

    return SessionDetailData(
        session_id=session_id,
        files_edited=sorted(files_edited),
        commands_run=commands_run[-10:],  # Keep last 10 commands
        files_read=sorted(files_read),
        searches=sorted(searches),
        agents_spawned=agents_spawned,
        last_user_msg=last_user_msg,
        last_assistant_msg=last_assistant_msg,
    )
