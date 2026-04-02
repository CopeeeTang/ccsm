"""Persistent session index with full-text fuzzy search."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class IndexEntry:
    """A single session index entry."""

    session_id: str
    worktree: str = ""
    project: str = ""
    title: str = ""
    intent: str = ""
    git_branch: str = ""
    first_user_content: str = ""
    last_message_at: Optional[datetime] = None
    status: str = ""
    tags: list[str] = field(default_factory=list)

    def search_text(self) -> str:
        """Concatenate searchable fields into a single lowercase string."""
        parts = [
            self.title,
            self.intent,
            self.git_branch,
            self.first_user_content,
            self.session_id[:8],
            " ".join(self.tags),
        ]
        return " ".join(parts).lower()


class SessionIndex:
    """In-memory session index with filtering and fuzzy search."""

    def __init__(self) -> None:
        self._entries: dict[str, IndexEntry] = {}

    # -- mutators -------------------------------------------------------------

    def update_entries(self, entries: list[IndexEntry]) -> None:
        """Add or update entries by *session_id*."""
        for entry in entries:
            self._entries[entry.session_id] = entry

    def remove(self, session_id: str) -> None:
        """Remove an entry.  Silently ignores missing ids."""
        self._entries.pop(session_id, None)

    # -- query ----------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        worktree: Optional[str] = None,
        project: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 0,
    ) -> list[IndexEntry]:
        """Search and filter entries.

        Filters (worktree, project, status) are applied first.
        If *query* is empty the filtered list is returned sorted by
        *last_message_at* descending.  Otherwise candidates are scored:

        * +10 if query is a substring of *title*
        * +5  if query is a substring of *intent*
        * +1  for each query term found in *search_text()*
        """
        candidates = list(self._entries.values())

        # -- filters ----------------------------------------------------------
        if worktree is not None:
            candidates = [e for e in candidates if e.worktree == worktree]
        if project is not None:
            candidates = [e for e in candidates if e.project == project]
        if status is not None:
            candidates = [e for e in candidates if e.status == status]

        # -- sort key helper (None-safe) --------------------------------------
        def _ts(entry: IndexEntry) -> datetime:
            return entry.last_message_at or datetime.min.replace(tzinfo=timezone.utc)

        if not query:
            candidates.sort(key=_ts, reverse=True)
        else:
            q = query.lower()
            terms = q.split()
            scored: list[tuple[int, datetime, IndexEntry]] = []
            for entry in candidates:
                score = 0
                if q in (entry.title or "").lower():
                    score += 10
                if q in (entry.intent or "").lower():
                    score += 5
                st = entry.search_text()
                for term in terms:
                    if term in st:
                        score += 1
                if score > 0:
                    scored.append((score, _ts(entry), entry))
            scored.sort(key=lambda t: (t[0], t[1]), reverse=True)
            candidates = [t[2] for t in scored]

        if limit > 0:
            candidates = candidates[:limit]
        return candidates

    # -- persistence ----------------------------------------------------------

    def save(self, path: Path) -> None:
        """Serialize the index to a JSON file."""
        records: list[dict] = []
        for entry in self._entries.values():
            d = asdict(entry)
            if d["last_message_at"] is not None:
                d["last_message_at"] = d["last_message_at"].isoformat()
            records.append(d)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(records, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "SessionIndex":
        """Deserialize an index from a JSON file.

        Gracefully handles missing files, corrupt JSON, and unknown fields.
        """
        idx = cls()
        if not path.exists():
            return idx
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return idx

        # Known fields for filtering unknown keys from older/newer formats
        known_fields = {f.name for f in IndexEntry.__dataclass_fields__.values()}
        entries: list[IndexEntry] = []
        for d in data:
            try:
                ts = d.get("last_message_at")
                if ts is not None:
                    d["last_message_at"] = datetime.fromisoformat(ts)
                # Filter out unknown fields to prevent TypeError
                filtered = {k: v for k, v in d.items() if k in known_fields}
                entries.append(IndexEntry(**filtered))
            except (TypeError, ValueError):
                continue  # Skip corrupt entries
        idx.update_entries(entries)
        return idx
