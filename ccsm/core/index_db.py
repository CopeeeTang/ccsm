"""SQLite-backed persistent session index for incremental processing.

Stores session stubs with mtime tracking. On startup, only re-parses JSONL
files whose mtime has changed since last index.

Storage: ~/.ccsm/index.db
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".ccsm" / "index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    jsonl_path TEXT NOT NULL,
    jsonl_mtime REAL NOT NULL,
    title TEXT,
    slug TEXT,
    status TEXT,
    message_count INTEGER DEFAULT 0,
    last_timestamp TEXT,
    project_name TEXT,
    worktree_name TEXT,
    display_name TEXT,
    is_archived INTEGER DEFAULT 0,
    is_running INTEGER DEFAULT 0,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_mtime ON sessions(jsonl_mtime);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
"""


class SessionIndexDB:
    """SQLite persistent index for session metadata."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def upsert(
        self,
        session_id: str,
        jsonl_path: str,
        jsonl_mtime: float,
        title: str | None = None,
        slug: str | None = None,
        status: str | None = None,
        message_count: int = 0,
        last_timestamp: datetime | None = None,
        project_name: str | None = None,
        worktree_name: str | None = None,
        display_name: str | None = None,
        is_archived: bool = False,
        is_running: bool = False,
    ) -> None:
        """Insert or update a session record."""
        now = datetime.now(timezone.utc).isoformat()
        last_ts_str = last_timestamp.isoformat() if last_timestamp else None
        self._conn.execute(
            """INSERT INTO sessions
               (session_id, jsonl_path, jsonl_mtime, title, slug, status,
                message_count, last_timestamp, project_name, worktree_name,
                display_name, is_archived, is_running, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                jsonl_path=excluded.jsonl_path,
                jsonl_mtime=excluded.jsonl_mtime,
                title=excluded.title,
                slug=excluded.slug,
                status=excluded.status,
                message_count=excluded.message_count,
                last_timestamp=excluded.last_timestamp,
                project_name=excluded.project_name,
                worktree_name=excluded.worktree_name,
                display_name=excluded.display_name,
                is_archived=excluded.is_archived,
                is_running=excluded.is_running,
                indexed_at=excluded.indexed_at
            """,
            (session_id, str(jsonl_path), jsonl_mtime, title, slug, status,
             message_count, last_ts_str, project_name, worktree_name,
             display_name, int(is_archived), int(is_running), now),
        )
        self._conn.commit()

    def needs_refresh(self, session_id: str, current_mtime: float) -> bool:
        """Check if a session needs re-parsing (mtime changed or not indexed)."""
        row = self._conn.execute(
            "SELECT jsonl_mtime FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return True  # Not indexed yet
        return abs(row["jsonl_mtime"] - current_mtime) > 0.001

    def get(self, session_id: str) -> Optional[dict]:
        """Get a session record by ID."""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        """List all indexed sessions."""
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY last_timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, session_id: str) -> None:
        """Remove a session from the index."""
        self._conn.execute(
            "DELETE FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def incremental_refresh(db_path: Path | None = None) -> int:
    """Run incremental refresh: scan ~/.claude/projects/, re-parse only changed files.

    Returns the number of sessions refreshed.
    """
    from ccsm.core.discovery import discover_projects
    from ccsm.core.parser import parse_session_info

    db = SessionIndexDB(db_path=db_path)
    projects = discover_projects()
    refreshed = 0

    for project in projects:
        for session in project.all_sessions:
            try:
                mtime = session.jsonl_path.stat().st_mtime
            except OSError:
                continue

            if not db.needs_refresh(session.session_id, mtime):
                continue

            # Parse this session's JSONL
            try:
                info = parse_session_info(session.jsonl_path)
                sid = info.session_id or session.session_id
                db.upsert(
                    session_id=sid,
                    jsonl_path=str(session.jsonl_path),
                    jsonl_mtime=mtime,
                    title=info.display_title,
                    slug=info.slug,
                    status=info.status.value if info.status else None,
                    message_count=info.message_count,
                    last_timestamp=info.last_timestamp,
                    project_name=project.name,
                    worktree_name=session.project_dir,
                    display_name=info.display_name,
                    is_archived=session.is_archived,
                )
                refreshed += 1
            except Exception as e:
                logger.debug("Skip indexing %s: %s", session.session_id, e)

    db.close()
    return refreshed
