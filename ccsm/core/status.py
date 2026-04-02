"""Status inference engine for CCSM.

Determines session Status and Priority based on metadata signals
extracted from SessionInfo (message counts, timestamps, project_dir, etc.).

Inference priority order: NOISE > BACKGROUND > ACTIVE > IDEA > DONE
Each rule is checked in this order; the first match wins.

No JSONL content parsing is needed — only the lightweight fields
already populated by parse_session_info().
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Optional

from ccsm.models.session import (
    Priority,
    SessionInfo,
    SessionMeta,
    Status,
    STATUS_TO_PRIORITY,
)

logger = logging.getLogger(__name__)

# ─── Configurable thresholds ────────────────────────────────────────────────
# These module-level constants can later be loaded from config.toml.

ACTIVE_THRESHOLD_HOURS = 24
DONE_THRESHOLD_HOURS = 48
BACKGROUND_MIN_DURATION_HOURS = 2
NOISE_MIN_MESSAGES = 3
IDEA_MAX_DURATION_MINUTES = 30

# Content-based NOISE thresholds
NOISE_MAX_TOTAL_USER_CHARS = 50  # Sessions with all user text < 50 chars are noise

# ─── Detection patterns ────────────────────────────────────────────────────

# project_dir substring that identifies claude-mem observer sessions (noise)
_OBSERVER_DIR_PATTERN = "-claude-mem-observer-sessions"

# Keywords in slug / display_name that suggest a background task
_BACKGROUND_KEYWORDS = re.compile(
    r"(cron|loop|experiment|eval|benchmark|monitor|sweep|batch|pipeline|scheduled|recurring)",
    re.IGNORECASE,
)

# Keywords in slug / display_name that suggest an exploratory/idea session
_IDEA_KEYWORDS = re.compile(
    r"(source-first|brainstorm|explore|idea|draft|sketch|poc|prototype|research|investigate)",
    re.IGNORECASE,
)


# ─── Helpers ────────────────────────────────────────────────────────────────


def _hours_since(ts: Optional[datetime]) -> Optional[float]:
    """Return hours elapsed since *ts*, or None if ts is unavailable."""
    if ts is None:
        return None
    now = datetime.now(timezone.utc)
    # Make ts offset-aware if needed
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    delta = (now - ts).total_seconds()
    return delta / 3600.0


def _duration_hours(session: SessionInfo) -> Optional[float]:
    """Session duration in hours, or None if timestamps are missing."""
    secs = session.duration_seconds
    if secs is None:
        return None
    return secs / 3600.0


def _duration_minutes(session: SessionInfo) -> Optional[float]:
    """Session duration in minutes, or None if timestamps are missing."""
    secs = session.duration_seconds
    if secs is None:
        return None
    return secs / 60.0


def _text_hints(session: SessionInfo) -> str:
    """Concatenate slug + display_name for keyword matching."""
    parts: list[str] = []
    if session.slug:
        parts.append(session.slug)
    if session.display_name:
        parts.append(session.display_name)
    return " ".join(parts)


# ─── Core inference ─────────────────────────────────────────────────────────


def _is_noise(session: SessionInfo) -> bool:
    """Check NOISE conditions (highest inference priority).

    NOISE if ANY of:
    1. project_dir matches the claude-mem observer directory pattern
    2. Session has < NOISE_MIN_MESSAGES user messages AND low total message count
    3. All user messages are slash commands (/model, /resume, /compact, etc.)
    4. Total user text content < NOISE_MAX_TOTAL_USER_CHARS (hi, OK, test, etc.)
    """
    # Rule 1: observer directory — always noise regardless of content
    if _OBSERVER_DIR_PATTERN in session.project_dir:
        return True

    # Rule 2: too few user messages AND low total messages (no substance)
    if session.user_message_count < NOISE_MIN_MESSAGES and session.message_count < NOISE_MIN_MESSAGES * 2:
        return True

    # Rule 3: all user messages are slash commands (testing/configuration)
    if session.all_slash_commands and session.user_message_count > 0:
        return True

    # Rule 4: extremely short total user content (hi, OK, test, etc.)
    # BUT only if total message count is also low — a single precise instruction
    # that triggers a long Claude response is NOT noise.
    if (
        session.total_user_chars > 0
        and session.total_user_chars < NOISE_MAX_TOTAL_USER_CHARS
        and session.message_count < NOISE_MIN_MESSAGES * 2
    ):
        return True

    return False


def _is_background(session: SessionInfo) -> bool:
    """Check BACKGROUND conditions (second inference priority).

    BACKGROUND if ANY of:
    1. Running AND duration > BACKGROUND_MIN_DURATION_HOURS
    2. slug/display_name contains background keywords (cron, loop, experiment, etc.)
    3. High tool-call density: many messages but few user messages (autonomous work)
       Heuristic: message_count > 20 AND user_message_count < message_count * 0.2
    """
    dur = _duration_hours(session)
    hints = _text_hints(session)

    # Rule 1: running long session
    if session.is_running and dur is not None and dur >= BACKGROUND_MIN_DURATION_HOURS:
        return True

    # Rule 2: keyword match in slug/display_name
    if _BACKGROUND_KEYWORDS.search(hints):
        return True

    # Rule 3: tool-call density heuristic (lots of assistant messages, few user)
    if (
        session.message_count > 20
        and session.user_message_count > 0
        and session.user_message_count < session.message_count * 0.2
    ):
        return True

    return False


def _is_active(session: SessionInfo) -> bool:
    """Check ACTIVE conditions (third inference priority).

    ACTIVE if ALL of:
    1. Last activity within ACTIVE_THRESHOLD_HOURS
    2. Has substantive user messages (>= NOISE_MIN_MESSAGES)
    """
    hours_ago = _hours_since(session.last_timestamp)

    # Currently running is a strong active signal
    if session.is_running:
        return True

    # Recent activity + substantive content
    if hours_ago is not None and hours_ago <= ACTIVE_THRESHOLD_HOURS:
        if session.user_message_count >= NOISE_MIN_MESSAGES:
            return True

    return False


def _is_idea(session: SessionInfo) -> bool:
    """Check IDEA conditions (fourth inference priority).

    IDEA if ANY of:
    1. slug/display_name contains exploratory keywords (source-first, brainstorm, etc.)
    2. Short session (< IDEA_MAX_DURATION_MINUTES) with discussion character
       (some user messages but not deeply engaged)
    """
    hints = _text_hints(session)
    dur_min = _duration_minutes(session)

    # Rule 1: exploratory keywords
    if _IDEA_KEYWORDS.search(hints):
        return True

    # Rule 2: short, lightweight session
    if dur_min is not None and dur_min < IDEA_MAX_DURATION_MINUTES:
        # Has some substance but not a lot — discussion character
        if NOISE_MIN_MESSAGES <= session.user_message_count <= 10:
            return True

    return False


def infer_status(session: SessionInfo) -> Status:
    """Infer session status from SessionInfo metadata fields.

    Applies rules in priority order: NOISE > BACKGROUND > ACTIVE > IDEA > DONE.
    The first matching rule wins.

    Uses only lightweight fields already present in SessionInfo:
    - message_count, user_message_count
    - first_timestamp, last_timestamp (for duration and recency)
    - is_running
    - project_dir (observer directory detection)
    - slug, display_name (keyword hints)
    """
    # Priority order: NOISE → BACKGROUND → ACTIVE → IDEA → DONE
    if _is_noise(session):
        return Status.NOISE

    if _is_background(session):
        return Status.BACKGROUND

    if _is_active(session):
        return Status.ACTIVE

    if _is_idea(session):
        return Status.IDEA

    # Default: DONE (no recent activity, doesn't match other patterns)
    return Status.DONE


def infer_priority(status: Status, meta: Optional[SessionMeta] = None) -> Priority:
    """Map Status to default Priority, with optional meta override.

    If meta has a priority_override set, it takes precedence over
    the default STATUS_TO_PRIORITY mapping.
    """
    if meta is not None and meta.priority_override is not None:
        return meta.priority_override

    return STATUS_TO_PRIORITY.get(status, Priority.PARK)


def classify_session(
    session: SessionInfo, meta: Optional[SessionMeta] = None
) -> tuple[Status, Priority]:
    """Full classification pipeline for a single session.

    Steps:
    1. If meta has status_override, use it; otherwise infer from signals.
    2. Derive priority from status (with meta.priority_override if set).
    3. Update the session object in-place.

    Returns:
        (status, priority) tuple.
    """
    # Step 1: Determine status
    if meta is not None and meta.status_override is not None:
        status = meta.status_override
    else:
        status = infer_status(session)

    # Step 2: Determine priority
    priority = infer_priority(status, meta)

    # Step 3: Update session in-place
    session.status = status
    session.priority = priority

    return status, priority


def classify_all(
    sessions: list[SessionInfo],
    all_meta: Optional[dict[str, SessionMeta]] = None,
) -> None:
    """Classify all sessions in-place.

    Looks up per-session metadata from the all_meta dict (keyed by session_id).
    Sessions without metadata are classified using inferred rules only.
    """
    all_meta = all_meta or {}

    for session in sessions:
        meta = all_meta.get(session.session_id)
        classify_session(session, meta)
