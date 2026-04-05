"""File-level caching utilities for JSONL parse results."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ccsm.core.lineage import LineageSignals
    from ccsm.models.session import JSONLMessage, SessionInfo


def cache_key_for(path: Path) -> tuple[str, float, int]:
    """Compute a cache key based on file path, mtime, and size.

    Returns a tuple (abs_path, mtime_ns, size) that changes when the
    file is modified.  For missing files returns a sentinel.
    """
    try:
        st = path.stat()
        return (str(path.resolve()), st.st_mtime_ns, st.st_size)
    except (OSError, IOError):
        return ("__missing__", 0, 0)


@lru_cache(maxsize=256)
def _cached_parse(key: tuple) -> tuple:
    """Internal LRU-cached parse. Key must be a hashable tuple from cache_key_for."""
    # key[0] is the absolute path string
    from ccsm.core.parser import parse_session_complete
    return parse_session_complete(Path(key[0]))


def cached_parse_complete(
    path: Path,
    display_name: str | None = None,
) -> tuple["SessionInfo", "LineageSignals", list["JSONLMessage"]]:
    """Parse JSONL with file-level LRU cache.

    Cache is keyed by (abs_path, mtime_ns, size).
    If the file has been modified since the last read, cache misses
    and re-parses.
    """
    key = cache_key_for(path)
    if key[0] == "__missing__":
        from ccsm.core.parser import parse_session_complete
        return parse_session_complete(path, display_name=display_name)
    return _cached_parse(key)


def invalidate_cache() -> None:
    """Clear the parse cache. Call after bulk operations that modify JSONL files."""
    _cached_parse.cache_clear()
