"""CCSM MCP Server — exposes Claude Code session management as MCP tools.

6 tools:
    list_sessions      — List sessions with optional filters (worktree, status, priority, tag)
    get_session_detail  — Full detail for a single session (description + last replies + metadata)
    search_sessions     — Fuzzy search across session titles, slugs, and tags
    resume_session      — Generate `claude --resume {session_id}` command
    summarize_session   — Structured extraction of session summary
    update_session_meta — Update sidecar metadata (name, priority, tags, pin)

Usage:
    python -m ccsm.mcp.server          # stdio transport (default)
    python -m ccsm.mcp.server --sse    # SSE transport
"""

from __future__ import annotations

import logging
import time
from datetime import timezone
from typing import Optional

from mcp.server.fastmcp import FastMCP

from ccsm.core.discovery import (
    decode_project_path,
    discover_projects,
    load_display_names,
    load_running_sessions,
)
from ccsm.core.meta import load_all_meta, load_meta, update_meta
from ccsm.core.parser import get_last_assistant_messages, parse_session_info
from ccsm.core.status import classify_all
from ccsm.models.session import Priority, SessionInfo, SessionMeta, Status

logger = logging.getLogger(__name__)

# ─── FastMCP instance ──────────────────────────────────────────────────────

mcp = FastMCP(
    name="ccsm",
    instructions=(
        "Claude Code Session Manager — query, search, and manage "
        "Claude Code conversation sessions across projects and worktrees."
    ),
)

# ─── Data loading pipeline (with TTL cache) ──────────────────────────────

# Module-level cache to avoid re-parsing 3000+ JSONL files on every tool call.
# TTL = 30 seconds — short enough for near-real-time, long enough for burst calls.
_CACHE_TTL_SECONDS = 30.0
_cache: dict[str, object] = {
    "session_map": None,    # dict[str, SessionInfo]
    "context_map": None,    # dict[str, tuple[str, str | None]]
    "all_meta": None,       # dict[str, SessionMeta]
    "timestamp": 0.0,       # monotonic time of last load
}


def _session_to_dict(
    session: SessionInfo,
    project_name: str,
    worktree_name: str | None,
    all_meta: dict[str, SessionMeta] | None = None,
) -> dict:
    """Serialize a SessionInfo to the standard JSON response dict."""
    duration_secs = session.duration_seconds
    duration_minutes = round(duration_secs / 60, 1) if duration_secs else None

    last_activity = None
    if session.last_timestamp:
        ts = session.last_timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        last_activity = ts.isoformat()

    # Reuse pre-loaded meta if available, avoid redundant disk I/O
    if all_meta is not None:
        meta = all_meta.get(session.session_id, SessionMeta(session_id=session.session_id))
    else:
        meta = load_meta(session.session_id)

    return {
        "session_id": session.session_id,
        "title": meta.name or session.display_title,
        "status": session.status.value,
        "priority": session.priority.value,
        "tags": meta.tags,
        "worktree": worktree_name or "main",
        "project": project_name,
        "last_activity": last_activity,
        "duration_minutes": duration_minutes,
        "message_count": session.message_count,
        "is_running": session.is_running,
        "is_archived": session.is_archived,
    }


def _build_session_map(
    force_refresh: bool = False,
) -> tuple[dict[str, SessionInfo], dict[str, tuple[str, str | None]], dict[str, SessionMeta]]:
    """Build lookup maps with TTL caching + SQLite incremental index.

    Returns:
        (session_map, context_map, all_meta)
        - session_map:  session_id → SessionInfo
        - context_map:  session_id → (project_name, worktree_name)
        - all_meta:     session_id → SessionMeta
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _cache["session_map"] is not None
        and (now - _cache["timestamp"]) < _CACHE_TTL_SECONDS  # type: ignore[operator]
    ):
        return _cache["session_map"], _cache["context_map"], _cache["all_meta"]  # type: ignore[return-value]

    # Try incremental refresh via SQLite (best-effort acceleration)
    try:
        from ccsm.core.index_db import incremental_refresh
        refreshed_count = incremental_refresh()
        if refreshed_count > 0:
            logger.info("Incrementally refreshed %d sessions", refreshed_count)
    except Exception as e:
        logger.debug("SQLite incremental refresh unavailable: %s", e)

    projects = discover_projects()
    running = load_running_sessions()
    display_names = load_display_names()
    all_meta = load_all_meta()

    session_map: dict[str, SessionInfo] = {}
    context_map: dict[str, tuple[str, str | None]] = {}

    for project in projects:
        # Main worktree
        if project.main_worktree:
            for session in project.main_worktree.sessions:
                info = parse_session_info(session.jsonl_path)
                info.project_dir = session.project_dir
                info.is_archived = session.is_archived
                info.is_running = running.get(info.session_id, False)
                if info.session_id in display_names:
                    info.display_name = display_names[info.session_id]
                session_map[info.session_id] = info
                context_map[info.session_id] = (project.name, None)

        # Named worktrees
        for wt in project.worktrees:
            for session in wt.sessions:
                info = parse_session_info(session.jsonl_path)
                info.project_dir = session.project_dir
                info.is_archived = session.is_archived
                info.is_running = running.get(info.session_id, False)
                if info.session_id in display_names:
                    info.display_name = display_names[info.session_id]
                session_map[info.session_id] = info
                context_map[info.session_id] = (project.name, wt.name)

    # Classify all
    classify_all(list(session_map.values()), all_meta)

    # Apply user-meta name overrides
    for sid, info in session_map.items():
        meta = all_meta.get(sid)
        if meta and meta.name:
            info.display_name = meta.name

    # Update cache
    _cache["session_map"] = session_map
    _cache["context_map"] = context_map
    _cache["all_meta"] = all_meta
    _cache["timestamp"] = now

    return session_map, context_map, all_meta


# ─── Tool: list_sessions ───────────────────────────────────────────────────


@mcp.tool(
    name="list_sessions",
    description=(
        "List Claude Code sessions with optional filters. "
        "Returns a JSON array of session objects."
    ),
)
def list_sessions(
    worktree: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[str] = None,
    tag: Optional[str] = None,
) -> list[dict]:
    """List sessions, optionally filtered by worktree, status, priority, or tag."""
    session_map, context_map, all_meta = _build_session_map()

    results: list[dict] = []

    for sid, info in session_map.items():
        project_name, wt_name = context_map[sid]
        effective_wt = wt_name or "main"

        # Filter: worktree
        if worktree and effective_wt != worktree:
            continue

        # Filter: status
        if status:
            try:
                target_status = Status(status)
                if info.status != target_status:
                    continue
            except ValueError:
                continue

        # Filter: priority
        if priority:
            try:
                target_priority = Priority(priority)
                if info.priority != target_priority:
                    continue
            except ValueError:
                continue

        # Filter: tag
        if tag:
            meta = all_meta.get(sid)
            if not meta or tag not in meta.tags:
                continue

        results.append(_session_to_dict(info, project_name, wt_name, all_meta))

    # Sort by last_activity descending (most recent first)
    results.sort(key=lambda d: d.get("last_activity") or "", reverse=True)
    return results


# ─── Tool: get_session_detail ──────────────────────────────────────────────


@mcp.tool(
    name="get_session_detail",
    description=(
        "Get full details for a session: metadata, last assistant replies, "
        "and sidecar user-metadata."
    ),
)
def get_session_detail(session_id: str) -> dict:
    """Return complete session detail including last assistant messages."""
    session_map, context_map, all_meta = _build_session_map()

    if session_id not in session_map:
        return {"error": f"Session not found: {session_id}"}

    info = session_map[session_id]
    project_name, wt_name = context_map[session_id]

    # Base session dict
    result = _session_to_dict(info, project_name, wt_name, all_meta)

    # Add last assistant replies
    last_msgs = get_last_assistant_messages(info.jsonl_path, count=3)
    result["last_assistant_messages"] = [
        {
            "uuid": m.uuid,
            "content": m.content[:500],  # Truncate for readability
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        }
        for m in last_msgs
    ]

    # Add full sidecar metadata
    meta = load_meta(session_id)
    result["meta"] = {
        "name": meta.name,
        "status_override": meta.status_override.value if meta.status_override else None,
        "priority_override": (
            meta.priority_override.value if meta.priority_override else None
        ),
        "tags": meta.tags,
        "pinned_messages": meta.pinned_messages,
        "notes": meta.notes,
    }

    # Add extra info
    result["slug"] = info.slug
    result["cwd"] = info.cwd
    result["git_branch"] = info.git_branch
    result["user_message_count"] = info.user_message_count

    return result


# ─── Tool: search_sessions ────────────────────────────────────────────────


@mcp.tool(
    name="search_sessions",
    description=(
        "Fuzzy search across session titles, slugs, display names, and tags. "
        "Returns matching sessions ranked by relevance."
    ),
)
def search_sessions(query: str) -> list[dict]:
    """Search sessions by query string (case-insensitive substring match)."""
    session_map, context_map, all_meta = _build_session_map()
    query_lower = query.lower()

    scored: list[tuple[int, dict]] = []

    for sid, info in session_map.items():
        project_name, wt_name = context_map[sid]
        score = 0

        # Match against various fields
        title = info.display_title.lower()
        slug = (info.slug or "").lower()
        display_name = (info.display_name or "").lower()
        sid_lower = sid.lower()
        cwd = (info.cwd or "").lower()
        branch = (info.git_branch or "").lower()

        # User-meta fields
        meta = all_meta.get(sid)
        meta_name = (meta.name or "").lower() if meta else ""
        meta_tags = [t.lower() for t in meta.tags] if meta else []
        meta_notes = (meta.notes or "").lower() if meta else ""

        # Scoring: exact match > title > slug > tags > cwd/branch > session_id
        if query_lower == title:
            score = 100
        elif query_lower in title:
            score = 80
        elif query_lower in meta_name:
            score = 75
        elif query_lower in slug:
            score = 70
        elif any(query_lower in t for t in meta_tags):
            score = 60
        elif query_lower in display_name:
            score = 50
        elif query_lower in cwd:
            score = 30
        elif query_lower in branch:
            score = 30
        elif query_lower in meta_notes:
            score = 25
        elif query_lower in sid_lower:
            score = 10

        if score > 0:
            d = _session_to_dict(info, project_name, wt_name, all_meta)
            d["match_score"] = score
            scored.append((score, d))

    # Sort by score descending, then by last_activity
    scored.sort(key=lambda x: (x[0], x[1].get("last_activity") or ""), reverse=True)
    return [d for _, d in scored]


# ─── Tool: resume_session ─────────────────────────────────────────────────


@mcp.tool(
    name="resume_session",
    description=(
        "Generate the CLI command to resume a session. "
        "Returns a ready-to-execute `claude --resume` command string."
    ),
)
def resume_session(session_id: str) -> dict:
    """Return the `claude --resume` command for a session.

    Uses JSONL file path instead of session_id to ensure cross-worktree
    resume works. Claude Code's session_id lookup depends on cwd matching
    the project directory, but JSONL path works from any directory.
    """
    session_map, context_map, _all_meta = _build_session_map()

    if session_id not in session_map:
        return {"error": f"Session not found: {session_id}"}

    info = session_map[session_id]
    project_name, wt_name = context_map[session_id]

    # Prefer JSONL path for cross-worktree reliability
    if info.jsonl_path and info.jsonl_path.exists():
        command = f"claude --resume {info.jsonl_path}"
    else:
        command = f"claude --resume {session_id}"

    return {
        "session_id": session_id,
        "title": info.display_title,
        "command": command,
        "is_running": info.is_running,
        "status": info.status.value,
        "cwd": info.cwd,
    }


# ─── Tool: enter_session ─────────────────────────────────────────────────


@mcp.tool(
    name="enter_session",
    description=(
        "Prepare context for entering/resuming a previous Claude Code session. "
        "Returns session summary, breakpoint, last milestones, and the resume command. "
        "Use this to understand what happened before resuming."
    ),
)
def enter_session(session_id: str) -> dict:
    """Provide rich context for resuming a session."""
    from ccsm.core.meta import load_summary

    session_map, context_map, all_meta = _build_session_map()

    if session_id not in session_map:
        return {"error": f"Session not found: {session_id}"}

    info = session_map[session_id]
    project_name, wt_name = context_map[session_id]
    meta = all_meta.get(session_id, SessionMeta(session_id=session_id))

    # Build context
    # Prefer JSONL path for cross-worktree reliability
    if info.jsonl_path and info.jsonl_path.exists():
        resume_cmd = f"claude --resume {info.jsonl_path}"
    else:
        resume_cmd = f"claude --resume {session_id}"

    result = {
        "session_id": session_id,
        "title": meta.name or info.display_title,
        "command": resume_cmd,
        "is_running": info.is_running,
        "status": info.status.value,
        "cwd": info.cwd,
        "git_branch": info.git_branch,
        "message_count": info.message_count,
    }

    # Add summary context if available
    cached = load_summary(session_id)
    if cached:
        result["summary"] = {
            "description": cached.description,
            "milestones": [
                {"label": ms.label, "status": ms.status.value}
                for ms in (cached.milestones or [])[-5:]  # Last 5 milestones
            ],
        }
        if cached.breakpoint:
            result["breakpoint"] = {
                "milestone": cached.breakpoint.milestone_label,
                "detail": cached.breakpoint.detail,
                "last_topic": cached.breakpoint.last_topic,
            }
        if cached.digest:
            result["digest"] = {
                "goal": cached.digest.goal,
                "progress": cached.digest.progress,
                "next_steps": cached.digest.next_steps,
                "blocker": cached.digest.blocker,
            }

    # Add last assistant snippet for quick context
    last_msgs = get_last_assistant_messages(info.jsonl_path, count=1)
    if last_msgs:
        result["last_reply_snippet"] = last_msgs[0].content[:300]

    return result


# ─── Tool: summarize_session ──────────────────────────────────────────────


@mcp.tool(
    name="summarize_session",
    description=(
        "Extract a structured summary from a session: description, "
        "key decisions, and last context. Uses cached summary if available."
    ),
)
def summarize_session(session_id: str) -> dict:
    """Return a structured summary extracted from session messages."""
    from ccsm.core.meta import load_summary

    session_map, context_map, _all_meta = _build_session_map()

    if session_id not in session_map:
        return {"error": f"Session not found: {session_id}"}

    info = session_map[session_id]

    # Check for cached summary first
    cached = load_summary(session_id)
    if cached:
        return {
            "session_id": session_id,
            "title": info.display_title,
            "mode": cached.mode,
            "description": cached.description,
            "decision_trail": cached.decision_trail,
            "key_insights": cached.key_insights,
            "tasks_completed": cached.tasks_completed,
            "tasks_pending": cached.tasks_pending,
            "last_context": cached.last_context,
            "generated_at": (
                cached.generated_at.isoformat() if cached.generated_at else None
            ),
        }

    # No cached summary — do lightweight extraction from last messages
    last_msgs = get_last_assistant_messages(info.jsonl_path, count=5)

    # Build a basic extractive summary from the last assistant messages
    last_texts = [m.content[:300] for m in last_msgs if m.content]
    last_context = last_texts[-1] if last_texts else None

    # Use slug / display name as description fallback
    description = info.display_name or info.slug or "No description available"

    return {
        "session_id": session_id,
        "title": info.display_title,
        "mode": "extract",
        "description": description,
        "decision_trail": [],
        "key_insights": [],
        "tasks_completed": [],
        "tasks_pending": [],
        "last_context": last_context,
        "last_assistant_snippets": last_texts,
        "generated_at": None,
        "note": "Extractive summary from last messages. Run LLM summarization for richer output.",
    }


# ─── Tool: update_session_meta ────────────────────────────────────────────


@mcp.tool(
    name="update_session_meta",
    description=(
        "Update sidecar metadata for a session: name, priority, tags, or pin status. "
        "Returns the updated metadata."
    ),
)
def update_session_meta(
    session_id: str,
    name: Optional[str] = None,
    priority: Optional[str] = None,
    tags: Optional[list[str]] = None,
    pin: Optional[str] = None,
) -> dict:
    """Update user-defined metadata for a session."""
    # Build kwargs for update_meta
    kwargs: dict = {}

    if name is not None:
        kwargs["name"] = name

    if priority is not None:
        try:
            Priority(priority)  # Validate
            kwargs["priority_override"] = priority
        except ValueError:
            return {
                "error": f"Invalid priority: {priority}. "
                f"Valid values: {[p.value for p in Priority]}"
            }

    if tags is not None:
        kwargs["tags"] = tags

    if pin is not None:
        # pin is a message UUID to add to pinned_messages
        kwargs["add_pinned"] = [pin]

    if not kwargs:
        return {"error": "No fields to update. Provide at least one of: name, priority, tags, pin"}

    meta = update_meta(session_id, **kwargs)

    return {
        "session_id": session_id,
        "updated": True,
        "meta": {
            "name": meta.name,
            "status_override": (
                meta.status_override.value if meta.status_override else None
            ),
            "priority_override": (
                meta.priority_override.value if meta.priority_override else None
            ),
            "tags": meta.tags,
            "pinned_messages": meta.pinned_messages,
            "notes": meta.notes,
            "updated_at": meta.updated_at.isoformat() if meta.updated_at else None,
        },
    }


# ─── Tool: batch_summarize ───────────────────────────────────────────────


@mcp.tool(
    name="batch_summarize",
    description=(
        "Generate AI summaries for sessions that don't have one yet. "
        "Uses external API mode. Returns count of newly summarized sessions."
    ),
)
def batch_summarize(
    limit: int = 10,
    status: Optional[str] = None,
) -> dict:
    """Batch generate summaries for un-summarized sessions."""
    from ccsm.core.meta import load_summary as _load_summary
    from ccsm.core.summarizer import summarize_session as _summarize

    session_map, context_map, _all_meta = _build_session_map()

    candidates = []
    for sid, info in session_map.items():
        if info.message_count < 8:
            continue
        if status:
            try:
                if info.status != Status(status):
                    continue
            except ValueError:
                continue
        cached = _load_summary(sid)
        if cached and cached.mode == "llm":
            continue
        candidates.append((sid, info))

    candidates.sort(key=lambda x: x[1].message_count, reverse=True)
    candidates = candidates[:limit]

    summarized = 0
    errors = 0
    for sid, info in candidates:
        try:
            _summarize(
                session_id=sid,
                jsonl_path=info.jsonl_path,
                mode="llm",
                force=True,
            )
            summarized += 1
        except Exception as e:
            logger.warning("Batch summarize failed for %s: %s", sid, e)
            errors += 1

    return {
        "summarized": summarized,
        "errors": errors,
        "total_candidates": len(candidates),
    }


# ─── Entry point ───────────────────────────────────────────────────────────


def main():
    """Run the CCSM MCP server."""
    import sys

    transport = "stdio"
    if "--sse" in sys.argv:
        transport = "sse"
    elif "--http" in sys.argv:
        transport = "streamable-http"

    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
