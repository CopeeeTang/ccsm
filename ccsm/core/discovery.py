"""Worktree & project discovery for CCSM.

Scans ~/.claude/projects/ to discover projects, worktrees, and session files.
Also loads runtime state (running sessions) and display names from history.

Directory encoding rules:
    ~/.claude/projects/ uses encoded path names where:
    - Single '-' can represent '/' (path separator) OR a literal hyphen in names
    - '--' (double hyphen) encodes '/.' (hidden directory with dot prefix)
    - '--claude-worktrees-' is the worktree separator

    Ambiguity in single '-' is resolved by recursive filesystem probing:
    at each directory level, try consuming 1..N hyphen-separated segments
    as a single entry name and check if it exists on disk.

    Examples:
        -home-v-tangxin-GUI                              → /home/v-tangxin/GUI (main)
        -home-v-tangxin-GUI--claude-worktrees-panel       → /home/v-tangxin/GUI, worktree: panel
        -home-v-tangxin-VLM-Router                       → /home/v-tangxin/VLM-Router
        -home-v-tangxin--claude-mem-observer-sessions     → /home/v-tangxin/.claude-mem/observer-sessions
"""

from __future__ import annotations

import json
import logging
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ccsm.models.session import Project, SessionInfo, Worktree

if TYPE_CHECKING:
    from ccsm.core.lineage import LineageSignals

logger = logging.getLogger(__name__)

# The separator Claude Code uses to encode worktree paths
_WORKTREE_SEP = "--claude-worktrees-"


def _default_claude_dir() -> Path:
    return Path.home() / ".claude"


# ─── Public API ──────────────────────────────────────────────────────────────


def decode_project_path(encoded: str) -> tuple[str, str | None]:
    """Decode an encoded project directory name.

    Returns:
        (project_name, worktree_name | None)

    Examples:
        >>> decode_project_path('-home-v-tangxin-GUI')
        ('GUI', None)
        >>> decode_project_path('-home-v-tangxin-GUI--claude-worktrees-panel')
        ('GUI', 'panel')
        >>> decode_project_path('-home-v-tangxin-VLM-Router')
        ('VLM-Router', None)
        >>> decode_project_path('-home-v-tangxin-ECVL-Router-977D')
        ('ECVL-Router-977D', None)
    """
    if _WORKTREE_SEP in encoded:
        base, worktree_name = encoded.split(_WORKTREE_SEP, 1)
        project_name, _ = _resolve_encoded_path(base)
        return project_name, worktree_name
    else:
        project_name, _ = _resolve_encoded_path(encoded)
        return project_name, None


def discover_projects(claude_dir: Path | None = None) -> list[Project]:
    """Scan ~/.claude/projects/ to discover all projects and worktrees.

    Steps:
        1. List all project directories
        2. Decode each directory name → (base_prefix, worktree_name?)
        3. Group by base_prefix into Projects
        4. Scan each directory for *.jsonl (active) and .archive/*.jsonl (archived)
        5. Create SessionInfo stubs (session_id, project_dir, jsonl_path, is_archived)

    Note:
        This function does NOT parse JSONL content — that's the parser's job.
        It only discovers files and groups them.
    """
    claude_dir = claude_dir or _default_claude_dir()
    projects_dir = claude_dir / "projects"

    if not projects_dir.is_dir():
        logger.warning("Projects directory not found: %s", projects_dir)
        return []

    # Phase 1: Scan directories and group by base prefix
    # groups: {base_encoded: [(encoded_dir_name, worktree_name | None), ...]}
    groups: dict[str, list[tuple[str, str | None]]] = defaultdict(list)

    for entry in sorted(projects_dir.iterdir()):
        if not entry.is_dir():
            continue

        encoded = entry.name

        if _WORKTREE_SEP in encoded:
            base_encoded, worktree_name = encoded.split(_WORKTREE_SEP, 1)
        else:
            base_encoded = encoded
            worktree_name = None

        groups[base_encoded].append((encoded, worktree_name))

    # Phase 2: Build Project objects
    projects: list[Project] = []

    for base_encoded, entries in sorted(groups.items()):
        project_name, base_path = _resolve_encoded_path(base_encoded)

        project = Project(name=project_name, base_path=base_path)

        for encoded_dir, worktree_name in entries:
            dir_path = projects_dir / encoded_dir
            sessions = _scan_sessions(dir_path, encoded_dir)

            if worktree_name is None:
                # Main branch
                wt = Worktree(
                    name="main",
                    encoded_path=encoded_dir,
                    sessions=sessions,
                )
                project.main_worktree = wt
            else:
                wt = Worktree(
                    name=worktree_name,
                    encoded_path=encoded_dir,
                    sessions=sessions,
                )
                project.worktrees.append(wt)

        projects.append(project)

    return projects


def _is_pid_alive(pid: int) -> bool:
    """Check whether a process with the given PID is still alive.

    Uses os.kill(pid, 0) which sends no signal but validates the PID.
    Returns False for invalid PIDs or if the process does not exist.
    """
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        # No such process
        return False
    except PermissionError:
        # Process exists but we don't have permission — still alive
        return True
    except OSError:
        return False


def load_running_sessions(claude_dir: Path | None = None) -> dict[str, dict]:
    """Read ~/.claude/sessions/ and return {session_id: info} for live running sessions.

    Each file in sessions/ is a single JSON object with fields:
        pid, sessionId, cwd, startedAt, [kind, entrypoint]

    PID liveness is verified via os.kill(pid, 0). Stale PID files
    (where the process no longer exists) are silently skipped.

    Returns:
        dict[str, dict] mapping session_id to:
            {"running": True, "kind": <str>, "pid": <int>}
    """
    claude_dir = claude_dir or _default_claude_dir()
    sessions_dir = claude_dir / "sessions"

    if not sessions_dir.is_dir():
        logger.debug("Sessions directory not found: %s", sessions_dir)
        return {}

    running: dict[str, dict] = {}

    for f in sessions_dir.iterdir():
        if f.suffix != ".json":
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            session_id = data.get("sessionId")
            if not session_id:
                continue

            pid = data.get("pid")
            if pid is not None:
                try:
                    pid = int(pid)
                except (ValueError, TypeError):
                    pid = None

            # Validate PID liveness; skip stale files
            if pid is not None and not _is_pid_alive(pid):
                logger.debug(
                    "Skipping stale session file %s: PID %d not alive", f.name, pid
                )
                continue

            kind = data.get("kind", "interactive")
            running[session_id] = {
                "running": True,
                "kind": kind,
                "pid": pid,
            }
        except (json.JSONDecodeError, OSError) as e:
            logger.debug("Failed to read session file %s: %s", f.name, e)

    return running


def load_display_names(claude_dir: Path | None = None) -> dict[str, str]:
    """Read ~/.claude/history.jsonl and return {session_id: display_name}.

    Takes the last history entry's 'display' field for each session.
    Entries with slash-command-only display values (e.g., "/model", "/resume")
    are still included — filtering is the caller's concern.
    """
    claude_dir = claude_dir or _default_claude_dir()
    history_file = claude_dir / "history.jsonl"

    if not history_file.is_file():
        logger.debug("History file not found: %s", history_file)
        return {}

    display_names: dict[str, str] = {}

    try:
        with open(history_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    session_id = record.get("sessionId")
                    display = record.get("display")
                    if session_id and display:
                        # Last-write-wins: later lines overwrite earlier ones
                        display_names[session_id] = display
                except json.JSONDecodeError:
                    continue
    except OSError as e:
        logger.warning("Failed to read history file: %s", e)

    return display_names


# ─── Internal helpers ────────────────────────────────────────────────────────


def _resolve_encoded_path(encoded_base: str) -> tuple[str, str]:
    """Resolve an encoded base directory name to (project_name, filesystem_path).

    Uses recursive filesystem probing to correctly handle:
    - Usernames with hyphens (e.g., v-tangxin)
    - Project names with hyphens (e.g., VLM-Router)
    - Hidden directories encoded as '--' (e.g., --claude-mem → /.claude-mem)
    """
    raw = encoded_base.lstrip("-")
    segments = raw.split("-")

    result = _probe_path(Path("/"), segments, 0)
    if result:
        _, full_path = result
        return Path(full_path).name, full_path

    # Fallback: last segment as name, naive decode as path
    return segments[-1], "/" + raw


def _probe_path(
    current: Path, segments: list[str], idx: int
) -> tuple[int, str] | None:
    """Recursively probe the filesystem to resolve hyphen-ambiguous paths.

    At each directory level, tries consuming 1..N segments (joined by hyphens)
    as a single filesystem entry name. Handles empty segments from '--' encoding
    by prepending a dot to the next segment (hidden directory convention).

    Returns:
        (depth, resolved_path) on success, None on failure.
    """
    if idx >= len(segments):
        return (0, str(current))

    best: tuple[int, str] | None = None

    for end in range(idx + 1, len(segments) + 1):
        raw_parts = segments[idx:end]

        candidate_name = _join_segments(raw_parts)
        if candidate_name is None:
            continue

        candidate_path = current / candidate_name

        if candidate_path.exists():
            if end >= len(segments):
                # Consumed all remaining segments
                return (end - idx, str(candidate_path))
            elif candidate_path.is_dir():
                sub = _probe_path(candidate_path, segments, end)
                if sub:
                    depth, full = sub
                    total = (end - idx) + depth
                    if best is None or total > best[0]:
                        best = (total, full)

    return best


def _join_segments(parts: list[str]) -> str | None:
    """Join hyphen-split segments back into a filesystem entry name.

    Handles the '--' encoding: an empty segment means the *next* segment
    gets a dot prefix (hidden directory convention).

    Examples:
        ['home']              → 'home'
        ['v', 'tangxin']      → 'v-tangxin'
        ['', 'claude', 'mem'] → '.claude-mem'
        ['VLM', 'Router']    → 'VLM-Router'
    """
    if not parts:
        return None

    result_parts: list[str] = []
    i = 0
    while i < len(parts):
        if parts[i] == "":
            # Empty segment from '--': prepend dot to next segment
            if i + 1 < len(parts):
                result_parts.append("." + parts[i + 1])
                i += 2
            else:
                # Trailing empty segment, skip
                i += 1
        else:
            result_parts.append(parts[i])
            i += 1

    if not result_parts:
        return None

    return "-".join(result_parts)


def _scan_sessions(dir_path: Path, encoded_dir: str) -> list[SessionInfo]:
    """Scan a project directory for session JSONL files.

    Looks for:
        - *.jsonl in the directory root (active sessions)
        - .archive/*.jsonl (archived sessions)
    """
    sessions: list[SessionInfo] = []

    # Active sessions: *.jsonl in root
    if dir_path.is_dir():
        for f in sorted(dir_path.glob("*.jsonl")):
            session_id = f.stem  # filename without .jsonl
            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    project_dir=encoded_dir,
                    jsonl_path=f,
                    is_archived=False,
                )
            )

    # Archived sessions: .archive/*.jsonl
    archive_dir = dir_path / ".archive"
    if archive_dir.is_dir():
        for f in sorted(archive_dir.glob("*.jsonl")):
            session_id = f.stem
            sessions.append(
                SessionInfo(
                    session_id=session_id,
                    project_dir=encoded_dir,
                    jsonl_path=f,
                    is_archived=True,
                )
            )

    return sessions


# ─── Duplicate Detection ────────────────────────────────────────────────────


def detect_duplicates(
    signals_map: dict[str, "LineageSignals"],
    max_gap_seconds: float = 300,
) -> list[list[str]]:
    """Find groups of sessions that are likely duplicates.

    Duplicates are sessions with same (cwd, git_branch) and overlapping or
    near-overlapping time ranges (gap < max_gap_seconds).

    Fixes pain point #7: two SSH terminals creating separate sessions
    for the same work on the same server.

    Args:
        signals_map: session_id → LineageSignals (from lineage.parse_lineage_signals)
        max_gap_seconds: Maximum gap between sessions to consider them duplicates.

    Returns:
        List of duplicate groups (each group is a list of session_ids, len ≥ 2).
    """
    from ccsm.core.lineage import LineageSignals  # noqa: F811 — runtime import

    # Group by (cwd, branch)
    by_key: dict[tuple[str, str], list[str]] = defaultdict(list)
    for sid, sig in signals_map.items():
        if sig.cwd and sig.git_branch:
            key = (sig.cwd, sig.git_branch)
            by_key[key].append(sid)

    groups: list[list[str]] = []
    for key, sids in by_key.items():
        if len(sids) < 2:
            continue
        # Sort by first_message_at
        sorted_sids = sorted(
            sids,
            key=lambda s: signals_map[s].first_message_at or datetime.min.replace(
                tzinfo=timezone.utc,
            ),
        )
        # Cluster: consecutive sessions with gap < threshold
        cluster: list[str] = [sorted_sids[0]]
        for i in range(1, len(sorted_sids)):
            curr = signals_map[sorted_sids[i]]
            prev = signals_map[sorted_sids[i - 1]]
            if curr.first_message_at and prev.last_message_at:
                gap = (curr.first_message_at - prev.last_message_at).total_seconds()
                if gap < max_gap_seconds:
                    cluster.append(sorted_sids[i])
                    continue
            # Gap too large — finalize cluster, start new
            if len(cluster) >= 2:
                groups.append(cluster)
            cluster = [sorted_sids[i]]
        if len(cluster) >= 2:
            groups.append(cluster)

    return groups
