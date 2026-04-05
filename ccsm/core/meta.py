"""Sidecar metadata read/write module.

All user-defined metadata is stored under ~/.ccsm/ (independent of Claude Code data).
Never modifies anything under ~/.claude/.

Storage layout:
    ~/.ccsm/
    ├── meta/
    │   └── {session_id}.meta.json
    └── summaries/
        └── {session_id}.summary.json
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ccsm.models.session import (
    Breakpoint,
    LineageType,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
    Priority,
    SessionDigest,
    SessionFact,
    SessionLineage,
    SessionMeta,
    SessionSummary,
    Status,
)

logger = logging.getLogger(__name__)

# ─── Directory helpers ───────────────────────────────────────────────────────

# Session IDs are UUIDs — only allow alphanumeric, hyphens, and underscores.
_SAFE_SESSION_ID = re.compile(r"^[a-zA-Z0-9_-]+$")


def _validate_session_id(session_id: str) -> str:
    """Validate session_id to prevent path traversal attacks.

    Session IDs should be UUID strings (e.g., "550e8400-e29b-41d4-a716-446655440000").
    Rejects any input containing '/', '..', or other filesystem-unsafe characters.

    Raises:
        ValueError: If session_id contains unsafe characters.
    """
    if not session_id or not _SAFE_SESSION_ID.match(session_id):
        raise ValueError(
            f"Invalid session_id: {session_id!r}. "
            "Only alphanumeric characters, hyphens, and underscores are allowed."
        )
    return session_id


def get_ccsm_dir() -> Path:
    """Return ~/.ccsm/ path, creating it (and sub-dirs) if absent.

    Sets owner-only permissions (0o700) to prevent other users from
    reading session metadata on shared servers.
    """
    ccsm_dir = Path.home() / ".ccsm"
    ccsm_dir.mkdir(parents=True, exist_ok=True)
    # Secure permissions: owner-only access (rwx------)
    ccsm_dir.chmod(0o700)
    (ccsm_dir / "meta").mkdir(exist_ok=True)
    (ccsm_dir / "summaries").mkdir(exist_ok=True)
    return ccsm_dir


def _meta_path(session_id: str) -> Path:
    return get_ccsm_dir() / "meta" / f"{_validate_session_id(session_id)}.meta.json"


def _summary_path(session_id: str) -> Path:
    return get_ccsm_dir() / "summaries" / f"{_validate_session_id(session_id)}.summary.json"


# ─── Serialization helpers ───────────────────────────────────────────────────


def _dt_to_iso(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to ISO 8601 string, or None."""
    if dt is None:
        return None
    return dt.isoformat()


def _iso_to_dt(s: Optional[str]) -> Optional[datetime]:
    """Deserialize an ISO 8601 string to datetime, or None."""
    if s is None:
        return None
    return datetime.fromisoformat(s)


def _enum_to_str(v: Any) -> Optional[str]:
    """Serialize an enum to its .value string, or None."""
    if v is None:
        return None
    return v.value


def _str_to_status(v: Optional[str]) -> Optional[Status]:
    if v is None:
        return None
    return Status(v)


def _str_to_priority(v: Optional[str]) -> Optional[Priority]:
    if v is None:
        return None
    return Priority(v)


# ─── Atomic file write ──────────────────────────────────────────────────────


def _atomic_write_json(path: Path, data: dict) -> None:
    """Write JSON to *path* atomically: write to a tmp file, then rename.

    This guards against data corruption from interrupted writes (e.g. power loss).
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create temp file in the same directory so os.replace is guaranteed atomic
    fd, tmp_path = tempfile.mkstemp(
        dir=path.parent, suffix=".tmp", prefix=path.stem
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        # Clean up temp file on any failure
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _safe_read_json(path: Path) -> Optional[dict]:
    """Read and parse a JSON file, returning None on any error (missing, corrupt, etc.)."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError) as exc:
        if not isinstance(exc, FileNotFoundError):
            logger.warning("Failed to read %s: %s", path, exc)
        return None


# ─── SessionMeta serialization ───────────────────────────────────────────────


def _meta_to_dict(meta: SessionMeta) -> dict:
    d = {
        "session_id": meta.session_id,
        "name": meta.name,
        "status_override": _enum_to_str(meta.status_override),
        "priority": _enum_to_str(meta.priority_override),
        "tags": meta.tags,
        "pinned_messages": meta.pinned_messages,
        "notes": meta.notes,
        "ai_intent": meta.ai_intent,
        "created_at": _dt_to_iso(meta.created_at),
        "updated_at": _dt_to_iso(meta.updated_at),
        "title_locked": meta.title_locked,
        "last_message_at": _dt_to_iso(meta.last_message_at),
        "last_accessed_at": _dt_to_iso(meta.last_accessed_at),
    }
    if meta.lineage is not None:
        d["lineage"] = {
            "session_id": meta.lineage.session_id,
            "lineage_type": meta.lineage.lineage_type.value,
            "parent_id": meta.lineage.parent_id,
            "children": meta.lineage.children,
            "compact_predecessor": meta.lineage.compact_predecessor,
            "fork_source": meta.lineage.fork_source,
            "fork_label": meta.lineage.fork_label,
            "depth": meta.lineage.depth,
        }
    else:
        d["lineage"] = None
    return d


def _dict_to_meta(d: dict) -> SessionMeta:
    meta = SessionMeta(
        session_id=d["session_id"],
        name=d.get("name"),
        status_override=_str_to_status(d.get("status_override")),
        priority_override=_str_to_priority(d.get("priority")),
        tags=d.get("tags", []),
        pinned_messages=d.get("pinned_messages", []),
        notes=d.get("notes"),
        ai_intent=d.get("ai_intent"),
        created_at=_iso_to_dt(d.get("created_at")),
        updated_at=_iso_to_dt(d.get("updated_at")),
    )
    meta.title_locked = d.get("title_locked", False)
    meta.last_message_at = _iso_to_dt(d.get("last_message_at"))
    meta.last_accessed_at = _iso_to_dt(d.get("last_accessed_at"))
    lineage_data = d.get("lineage")
    if lineage_data and isinstance(lineage_data, dict):
        meta.lineage = SessionLineage(
            session_id=lineage_data.get("session_id", meta.session_id),
            lineage_type=LineageType(lineage_data.get("lineage_type", "root")),
            parent_id=lineage_data.get("parent_id"),
            children=lineage_data.get("children", []),
            compact_predecessor=lineage_data.get("compact_predecessor"),
            fork_source=lineage_data.get("fork_source"),
            fork_label=lineage_data.get("fork_label"),
            depth=lineage_data.get("depth", 0),
        )
    return meta


# ─── SessionSummary serialization ────────────────────────────────────────────


def _summary_to_dict(summary: SessionSummary) -> dict:
    milestones_data = []
    for ms in (summary.milestones or []):
        ms_dict: dict = {
            "label": ms.label,
            "detail": ms.detail,
            "status": ms.status.value,
        }
        if ms.sub_items:
            ms_dict["sub_items"] = [
                {"label": si.label, "status": si.status.value}
                for si in ms.sub_items
            ]
        if ms.start_msg_idx is not None:
            ms_dict["start_msg_idx"] = ms.start_msg_idx
        if ms.end_msg_idx is not None:
            ms_dict["end_msg_idx"] = ms.end_msg_idx
        milestones_data.append(ms_dict)

    breakpoint_data = None
    if summary.breakpoint:
        bp = summary.breakpoint
        breakpoint_data = {
            "milestone_label": bp.milestone_label,
            "detail": bp.detail,
            "sub_item_label": bp.sub_item_label,
            "last_topic": bp.last_topic,
        }

    return {
        "session_id": summary.session_id,
        "mode": summary.mode,
        "description": summary.description,
        "decision_trail": summary.decision_trail,
        "key_insights": summary.key_insights,
        "tasks_completed": summary.tasks_completed,
        "tasks_pending": summary.tasks_pending,
        "code_changes": summary.code_changes,
        "last_context": summary.last_context,
        "milestones": milestones_data,
        "breakpoint": breakpoint_data,
        "generated_at": _dt_to_iso(summary.generated_at),
        "model": summary.model,
        # Phase 2: AI Digest + Facts (C-1 fix: include decisions + todo)
        "digest": {
            "progress": summary.digest.progress,
            "breakpoint": summary.digest.breakpoint,
            "decisions": summary.digest.decisions,
            "todo": summary.digest.todo,
            # Legacy fields for backward compat
            "goal": summary.digest.goal,
            "next_steps": summary.digest.next_steps,
            "blocker": summary.digest.blocker,
        } if summary.digest else None,
        "facts": [
            {"content": f.content, "type": f.fact_type, "source": f.source}
            for f in (summary.facts or [])
        ],
    }


def _dict_to_summary(d: dict) -> SessionSummary:
    # Parse milestones
    milestones: list[Milestone] = []
    for ms_data in d.get("milestones", []):
        sub_items = [
            MilestoneItem(
                label=si.get("label", ""),
                status=MilestoneStatus(si.get("status", "pending")),
            )
            for si in ms_data.get("sub_items", [])
        ]
        milestones.append(Milestone(
            label=ms_data.get("label", ""),
            detail=ms_data.get("detail"),
            status=MilestoneStatus(ms_data.get("status", "pending")),
            sub_items=sub_items,
            start_msg_idx=ms_data.get("start_msg_idx"),
            end_msg_idx=ms_data.get("end_msg_idx"),
        ))

    # Parse breakpoint
    breakpoint = None
    bp_data = d.get("breakpoint")
    if bp_data and isinstance(bp_data, dict):
        breakpoint = Breakpoint(
            milestone_label=bp_data.get("milestone_label", ""),
            detail=bp_data.get("detail", ""),
            sub_item_label=bp_data.get("sub_item_label"),
            last_topic=bp_data.get("last_topic"),
        )

    # Parse digest (Phase 2: backward-compatible — old files lack this key)
    # C-1 fix: read decisions + todo; fall back to legacy fields for old files
    digest = None
    digest_data = d.get("digest")
    if digest_data and isinstance(digest_data, dict):
        digest = SessionDigest(
            progress=digest_data.get("progress", ""),
            breakpoint=digest_data.get("breakpoint", ""),
            decisions=digest_data.get("decisions", []),
            todo=digest_data.get("todo", digest_data.get("next_steps", [])),
            goal=digest_data.get("goal", ""),
            next_steps=digest_data.get("next_steps", []),
            blocker=digest_data.get("blocker"),
        )

    # Parse facts (Phase 2: backward-compatible — old files lack this key)
    facts = [
        SessionFact(
            content=fd["content"],
            fact_type=fd.get("type"),
            source=fd.get("source"),
        )
        for fd in d.get("facts", [])
        if isinstance(fd, dict) and "content" in fd
    ]

    return SessionSummary(
        session_id=d["session_id"],
        mode=d.get("mode", "extract"),
        description=d.get("description"),
        decision_trail=d.get("decision_trail", []),
        key_insights=d.get("key_insights", []),
        tasks_completed=d.get("tasks_completed", []),
        tasks_pending=d.get("tasks_pending", []),
        code_changes=d.get("code_changes", []),
        last_context=d.get("last_context"),
        milestones=milestones,
        breakpoint=breakpoint,
        generated_at=_iso_to_dt(d.get("generated_at")),
        model=d.get("model"),
        digest=digest,
        facts=facts,
    )


# ─── Public API ──────────────────────────────────────────────────────────────


def load_meta(session_id: str) -> SessionMeta:
    """Load sidecar metadata for a session.

    Returns a default (empty) SessionMeta if the file does not exist or is corrupt.
    """
    d = _safe_read_json(_meta_path(session_id))
    if d is None or not isinstance(d, dict):
        return SessionMeta(session_id=session_id)
    try:
        return _dict_to_meta(d)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Corrupt meta for %s, returning default: %s", session_id, exc)
        return SessionMeta(session_id=session_id)


def save_meta(meta: SessionMeta) -> None:
    """Save sidecar metadata to JSON. Automatically bumps *updated_at*."""
    now = datetime.now(timezone.utc)
    if meta.created_at is None:
        meta.created_at = now
    meta.updated_at = now
    _atomic_write_json(_meta_path(meta.session_id), _meta_to_dict(meta))


def load_summary(session_id: str) -> Optional[SessionSummary]:
    """Load cached session summary. Returns None if absent or corrupt."""
    d = _safe_read_json(_summary_path(session_id))
    if d is None or not isinstance(d, dict):
        return None
    try:
        return _dict_to_summary(d)
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        logger.warning(
            "Corrupt summary for %s, returning None: %s", session_id, exc
        )
        return None


def save_summary(summary: SessionSummary) -> None:
    """Save session summary to cache file."""
    _atomic_write_json(
        _summary_path(summary.session_id), _summary_to_dict(summary)
    )


def is_summary_stale(summary_path: Path, jsonl_path: Path) -> bool:
    """Check if a cached summary is older than its source JSONL.

    Returns True if the JSONL has been modified after the summary was written.
    Returns False if either file doesn't exist (conservative: don't invalidate).
    """
    if not summary_path.exists() or not jsonl_path.exists():
        return False
    return jsonl_path.stat().st_mtime > summary_path.stat().st_mtime


def update_meta(session_id: str, **kwargs: Any) -> SessionMeta:
    """Convenience: load → update fields → save → return.

    Supported kwargs:
        name, status_override, priority_override, notes  — direct replacement
        tags         — list to replace, OR use add_tags / remove_tags
        pinned_messages — list to replace, OR use add_pinned / remove_pinned
        add_tags     — list of tags to append (deduplicated)
        remove_tags  — list of tags to remove
        add_pinned   — list of message UUIDs to append (deduplicated)
        remove_pinned — list of message UUIDs to remove
    """
    meta = load_meta(session_id)

    # ── Direct-replace fields ────────────────────────────────────────────
    if "name" in kwargs:
        meta.name = kwargs["name"]
    if "status_override" in kwargs:
        v = kwargs["status_override"]
        meta.status_override = Status(v) if isinstance(v, str) else v
    if "priority_override" in kwargs:
        v = kwargs["priority_override"]
        meta.priority_override = Priority(v) if isinstance(v, str) else v
    if "notes" in kwargs:
        meta.notes = kwargs["notes"]

    # ── Tags: full replace or incremental ────────────────────────────────
    if "tags" in kwargs:
        meta.tags = list(kwargs["tags"])
    else:
        if "add_tags" in kwargs:
            for t in kwargs["add_tags"]:
                if t not in meta.tags:
                    meta.tags.append(t)
        if "remove_tags" in kwargs:
            meta.tags = [t for t in meta.tags if t not in kwargs["remove_tags"]]

    # ── Pinned messages: full replace or incremental ─────────────────────
    if "pinned_messages" in kwargs:
        meta.pinned_messages = list(kwargs["pinned_messages"])
    else:
        if "add_pinned" in kwargs:
            for p in kwargs["add_pinned"]:
                if p not in meta.pinned_messages:
                    meta.pinned_messages.append(p)
        if "remove_pinned" in kwargs:
            meta.pinned_messages = [
                p for p in meta.pinned_messages if p not in kwargs["remove_pinned"]
            ]

    save_meta(meta)
    return meta


def load_all_meta() -> dict[str, SessionMeta]:
    """Load all existing meta files. Returns ``{session_id: SessionMeta}``."""
    meta_dir = get_ccsm_dir() / "meta"
    result: dict[str, SessionMeta] = {}
    for path in meta_dir.glob("*.meta.json"):
        d = _safe_read_json(path)
        if d is None:
            continue
        try:
            m = _dict_to_meta(d)
            result[m.session_id] = m
        except (KeyError, ValueError) as exc:
            logger.warning("Skipping corrupt meta %s: %s", path.name, exc)
    return result


def lock_title(session_id: str, title: str) -> SessionMeta:
    """Set a permanent title that won't be overwritten by AI or Claude Code.

    Fixes pain point #2: renamed titles reverting to last/first prompt.
    The title is stored in CCSM's sidecar file (~/.ccsm/meta/), completely
    independent of Claude Code's 64KB head/tail window.
    """
    meta = load_meta(session_id)
    meta.name = title
    meta.title_locked = True
    save_meta(meta)
    return meta


# ─── Workflow Cache I/O ─────────────────────────────────────────────────────


def _workflows_dir() -> Path:
    d = get_ccsm_dir() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _workflow_path(project: str, worktree: str) -> Path:
    import hashlib
    # Use a hash of the original (project, worktree) tuple to avoid collisions
    # from lossy character substitution (e.g., "a/b" + "c" vs "a" + "b/c").
    safe_project = re.sub(r"[^a-zA-Z0-9_-]", "_", project)
    safe_wt = re.sub(r"[^a-zA-Z0-9_-]", "_", worktree)
    # Append a short hash of the original values for collision resistance
    key = f"{project}\0{worktree}"
    suffix = hashlib.sha256(key.encode()).hexdigest()[:8]
    return _workflows_dir() / f"{safe_project}--{safe_wt}--{suffix}.json"


def save_workflows(cluster: "WorkflowCluster") -> None:
    """Save a WorkflowCluster to the cache directory."""
    from ccsm.models.session import WorkflowCluster  # noqa: avoid circular at module level

    data = {
        "worktree": cluster.worktree,
        "project": cluster.project,
        "orphans": cluster.orphans,
        "generated_at": _dt_to_iso(cluster.generated_at),
        "model": cluster.model,
        "workflows": [],
    }
    for wf in cluster.workflows:
        data["workflows"].append({
            "workflow_id": wf.workflow_id,
            "sessions": wf.sessions,
            "name": wf.name,
            "ai_name": wf.ai_name,
            "fork_branches": wf.fork_branches,
            "root_session_id": wf.root_session_id,
            "first_timestamp": _dt_to_iso(wf.first_timestamp),
            "last_timestamp": _dt_to_iso(wf.last_timestamp),
            "is_active": wf.is_active,
        })

    path = _workflow_path(cluster.project, cluster.worktree)
    _atomic_write_json(path, data)


def load_workflows(project: str, worktree: str) -> "Optional[WorkflowCluster]":
    """Load a cached WorkflowCluster, or return None if not cached."""
    from ccsm.models.session import Workflow, WorkflowCluster

    path = _workflow_path(project, worktree)
    data = _safe_read_json(path)
    if data is None or not isinstance(data, dict):
        return None

    try:
        workflows = []
        for wd in data.get("workflows", []):
            if not isinstance(wd, dict):
                continue  # Skip corrupt entries
            workflows.append(Workflow(
                workflow_id=wd.get("workflow_id", ""),
                sessions=wd.get("sessions", []),
                name=wd.get("name"),
                ai_name=wd.get("ai_name"),
                fork_branches=wd.get("fork_branches", []),
                root_session_id=wd.get("root_session_id"),
                first_timestamp=_iso_to_dt(wd.get("first_timestamp")),
                last_timestamp=_iso_to_dt(wd.get("last_timestamp")),
                is_active=wd.get("is_active", False),
            ))

        return WorkflowCluster(
            worktree=data.get("worktree", worktree),
            project=data.get("project", project),
            workflows=workflows,
            orphans=data.get("orphans", []),
            generated_at=_iso_to_dt(data.get("generated_at")),
            model=data.get("model"),
        )
    except (KeyError, ValueError, TypeError, AttributeError) as exc:
        logger.warning("Corrupt workflow cache for %s/%s: %s", project, worktree, exc)
        return None
