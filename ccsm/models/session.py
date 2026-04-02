"""Data models for CCSM.

Defines the core dataclasses used throughout the application:
- Session: A single Claude Code conversation session
- Worktree: A git worktree containing sessions
- Project: A top-level project containing worktrees
- SessionMeta: User-defined metadata (sidecar)
- SessionSummary: LLM-generated or extracted summary
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Literal, Optional


# ─── Enums ────────────────────────────────────────────────────────────────────


class Status(str, Enum):
    """Session status, automatically inferred from JSONL content.

    Inference priority: NOISE > BACKGROUND > ACTIVE > IDEA > DONE
    """

    ACTIVE = "active"  # User actively engaged, recent activity
    BACKGROUND = "background"  # Long-running autonomous task (eval, experiment, loop)
    IDEA = "idea"  # Short exploratory session (source-first, brainstorm)
    DONE = "done"  # No recent activity, completed
    NOISE = "noise"  # Plugin pollution, duplicate SSH, empty sessions


class Priority(str, Enum):
    """Attention priority, default-mapped from Status but user-overridable.

    Controls TUI visibility:
    - FOCUS: always shown, highlighted
    - WATCH: shown, secondary highlight
    - PARK: collapsed by default
    - HIDE: hidden by default (toggle with 'h')
    """

    FOCUS = "focus"  # Needs active engagement
    WATCH = "watch"  # Check occasionally
    PARK = "park"  # Parked / shelved
    HIDE = "hide"  # Hidden from view


# Default mapping: Status → Priority
STATUS_TO_PRIORITY: dict[Status, Priority] = {
    Status.ACTIVE: Priority.FOCUS,
    Status.BACKGROUND: Priority.WATCH,
    Status.IDEA: Priority.PARK,
    Status.DONE: Priority.PARK,
    Status.NOISE: Priority.HIDE,
}


class LineageType(str, Enum):
    """How this session relates to its parent in the session DAG.

    Detected from JSONL signals (fork name suffix, compact_boundary,
    overlapping timestamps + same cwd/branch).
    """

    ROOT = "root"           # No parent — standalone session
    FORK = "fork"           # Branched from another session (Claude Code /branch)
    COMPACT = "compact"     # Continuation after compaction (same JSONL file)
    DUPLICATE = "duplicate" # Same work from concurrent SSH terminals


# ─── Core Data Models ─────────────────────────────────────────────────────────


@dataclass
class JSONLMessage:
    """A single message extracted from a JSONL session file."""

    uuid: str
    parent_uuid: Optional[str]
    role: Literal["user", "assistant"]  # Message role
    content: str  # Flattened text content
    timestamp: datetime
    is_sidechain: bool = False


@dataclass
class SessionInfo:
    """Lightweight session metadata extracted from JSONL without full parsing.

    This is the primary data structure used for listing and filtering.
    Full message content is loaded lazily only when needed (e.g., for detail view).
    """

    session_id: str
    project_dir: str  # Encoded project directory name
    jsonl_path: Path  # Absolute path to the JSONL file
    is_archived: bool = False

    # Extracted from JSONL (first/last message scan)
    slug: Optional[str] = None  # Auto-generated 3-word identifier
    cwd: Optional[str] = None  # Working directory
    git_branch: Optional[str] = None
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    message_count: int = 0  # Total user + assistant messages
    user_message_count: int = 0  # User messages only

    # Content signals (extracted during parse, used for NOISE detection & card display)
    first_user_content: Optional[str] = None  # First user message text (truncated)
    total_user_chars: int = 0  # Sum of all user message character lengths
    all_slash_commands: bool = False  # True if every user message starts with '/'

    # Extracted from JSONL metadata entries
    custom_title: Optional[str] = None       # From JSONL `custom-title` type entry
    ai_title_from_cc: Optional[str] = None   # From JSONL `ai-title` type entry
    forked_from_session: Optional[str] = None  # From forkedFrom.sessionId field

    # Extracted from history.jsonl
    display_name: Optional[str] = None  # Name shown in /resume

    # Runtime state (from ~/.claude/sessions/)
    is_running: bool = False  # Currently has an active process

    # Inferred
    status: Status = Status.DONE
    priority: Priority = Priority.PARK

    @property
    def duration_seconds(self) -> Optional[float]:
        """Session duration in seconds, if timestamps are available."""
        if self.first_timestamp and self.last_timestamp:
            return (self.last_timestamp - self.first_timestamp).total_seconds()
        return None

    @property
    def display_title(self) -> str:
        """Best available title for display.

        Priority: display_name (from history.jsonl) > custom_title (user-set in JSONL)
        > ai_title_from_cc (AI-generated title in JSONL) > slug > session_id prefix.
        """
        return (
            self.display_name
            or self.custom_title
            or self.ai_title_from_cc
            or self.slug
            or self.session_id[:8]
        )


@dataclass
class SessionMeta:
    """User-defined metadata stored in sidecar .meta.json files.

    This data is independent of Claude Code's JSONL and survives
    session renames, archival, and other Claude operations.
    """

    session_id: str
    name: Optional[str] = None  # User-assigned name (overrides display_name)
    status_override: Optional[Status] = None  # Manual status override
    priority_override: Optional[Priority] = None  # Manual priority override
    tags: list[str] = field(default_factory=list)
    pinned_messages: list[str] = field(default_factory=list)  # UUIDs of pinned responses
    notes: Optional[str] = None  # Free-form user notes
    ai_intent: Optional[str] = None  # AI-generated one-line intent summary
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    # ── New fields for pain point fixes ──
    title_locked: bool = False              # True = title won't be overwritten by AI
    last_message_at: Optional[datetime] = None   # Last actual message timestamp (not file mtime)
    last_accessed_at: Optional[datetime] = None  # Last time session was opened/resumed
    lineage: Optional["SessionLineage"] = None   # Cross-session relationship


@dataclass
class SessionSummary:
    """Generated summary for a session, cached in sidecar .summary.json."""

    session_id: str
    mode: Literal["extract", "llm"]  # Summarization mode

    # Summary content
    description: Optional[str] = None  # High-level semantic summary
    decision_trail: list[str] = field(default_factory=list)  # Key decisions made
    key_insights: list[str] = field(default_factory=list)  # Important discoveries
    tasks_completed: list[str] = field(default_factory=list)  # Completed tasks
    tasks_pending: list[str] = field(default_factory=list)  # Pending tasks
    code_changes: list[str] = field(default_factory=list)  # File-level diff summary
    last_context: Optional[str] = None  # What was happening when session ended

    # Milestone timeline — ordered list of key phase-transition nodes
    milestones: list["Milestone"] = field(default_factory=list)
    breakpoint: Optional["Breakpoint"] = None  # Where the session was interrupted

    generated_at: Optional[datetime] = None
    model: Optional[str] = None  # LLM model used (if mode == "llm")


class MilestoneStatus(str, Enum):
    """Status of a milestone node in the session timeline."""

    DONE = "done"          # ✓ Completed
    IN_PROGRESS = "wip"    # ▶ Currently working on
    PENDING = "pending"    # ○ Not started yet


@dataclass
class Milestone:
    """A key phase-transition node in a session's timeline.

    Milestones capture the *structural turning points* of a conversation,
    not every message. They answer: "What phases did this session go through?"

    Milestone extraction method (what qualifies as a milestone):
    ─────────────────────────────────────────────────────────────
    1. PLAN PRODUCED — user & Claude agreed on a concrete plan
       Signal: assistant message contains plan/todo list, user confirms
    2. PHASE TRANSITION — work shifted from discussion to execution,
       from one subtask to another, or from impl to review
       Signal: topic change, tool usage pattern shift
    3. ARTIFACT PRODUCED — a tangible output was created
       Signal: file writes, test results, commit, PR
    4. REVIEW/FEEDBACK — external input changed direction
       Signal: codex review, user feedback, demo session
    5. BLOCKED/INTERRUPTED — work stopped at a specific point
       Signal: last message in session, error, context switch
    """

    label: str                              # Short title (e.g., "架构讨论")
    detail: Optional[str] = None            # One-line detail (e.g., "确认微服务拆分方案")
    status: MilestoneStatus = MilestoneStatus.PENDING
    sub_items: list["MilestoneItem"] = field(default_factory=list)  # Sub-topics discussed
    # For expandable index: reference to message range
    start_msg_idx: Optional[int] = None     # First message index in this phase
    end_msg_idx: Optional[int] = None       # Last message index in this phase


@dataclass
class MilestoneItem:
    """A sub-item within a milestone (e.g., "A. 消息队列选型 ✓")."""

    label: str
    status: MilestoneStatus = MilestoneStatus.PENDING


@dataclass
class Breakpoint:
    """Where the user interrupted/left the session.

    This is the single most valuable piece of information for context restoration.
    It directly answers: "Where was I when I left?"
    """

    milestone_label: str                    # Which milestone was active
    detail: str                             # What specifically was being discussed
    sub_item_label: Optional[str] = None    # Which sub-item, if applicable
    last_topic: Optional[str] = None        # The specific topic being discussed


@dataclass
class SessionLineage:
    """Cross-session relationship for DAG construction.

    Captures how sessions relate to each other: forks, compactions,
    and duplicates. Used to build the session graph visualization.
    """

    session_id: str
    lineage_type: LineageType = LineageType.ROOT
    parent_id: Optional[str] = None         # Parent session in the DAG
    children: list[str] = field(default_factory=list)  # Child session IDs
    compact_predecessor: Optional[str] = None  # Session before compaction
    fork_source: Optional[str] = None       # Original session that was forked
    fork_label: Optional[str] = None        # Descriptive label for this fork
    depth: int = 0                          # Distance from root in the DAG


# ─── Grouping Models ──────────────────────────────────────────────────────────


@dataclass
class Worktree:
    """A git worktree containing sessions.

    Worktrees are discovered from the encoded project directory names
    by identifying the '--claude-worktrees-' pattern.
    """

    name: str  # Worktree name (e.g., "panel", "memory")
    encoded_path: str  # Full encoded directory name
    sessions: list[SessionInfo] = field(default_factory=list)

    @property
    def active_count(self) -> int:
        return sum(1 for s in self.sessions if s.status == Status.ACTIVE)

    @property
    def total_count(self) -> int:
        return len(self.sessions)

    @property
    def has_active(self) -> bool:
        return self.active_count > 0


@dataclass
class Project:
    """A top-level project containing worktrees.

    Projects are discovered from ~/.claude/projects/ by grouping
    directories that share a common path prefix.
    """

    name: str  # Display name (e.g., "GUI", "VLM-Router")
    base_path: str  # Original filesystem path (decoded)
    main_worktree: Optional[Worktree] = None  # The main branch (non-worktree sessions)
    worktrees: list[Worktree] = field(default_factory=list)

    @property
    def all_sessions(self) -> list[SessionInfo]:
        """All sessions across main and worktrees."""
        sessions = []
        if self.main_worktree:
            sessions.extend(self.main_worktree.sessions)
        for wt in self.worktrees:
            sessions.extend(wt.sessions)
        return sessions

    @property
    def total_count(self) -> int:
        return len(self.all_sessions)


@dataclass
class Workflow:
    """A chain of related sessions forming a logical unit of work.

    The primary chain is a compact-continuation sequence (A → compact → B → compact → C).
    Fork branches hang off the chain as side-tracks.

    Rendering in TUI:
      ━● 登录系统                   3 sessions
        fix-login → c1 → c2
                     └─ auth (fork)
        Apr 1 10:00 — Apr 1 16:00        6h
    """

    workflow_id: str  # UUID or deterministic hash of root session_id
    sessions: list[str]  # Ordered session IDs in the compact chain
    name: Optional[str] = None  # Auto-generated: "title1 → title2 → ..."
    ai_name: Optional[str] = None  # AI-generated semantic name
    fork_branches: list[list[str]] = field(default_factory=list)  # Fork branch chains (each is an ordered list of session IDs)
    root_session_id: Optional[str] = None  # First session in chain
    first_timestamp: Optional[datetime] = None
    last_timestamp: Optional[datetime] = None
    is_active: bool = False  # Any session in chain is currently ACTIVE

    @property
    def display_name(self) -> str:
        """Best available workflow name."""
        return self.ai_name or self.name or self.workflow_id

    @property
    def session_count(self) -> int:
        return len(self.sessions) + sum(len(b) for b in self.fork_branches)

    @property
    def duration_seconds(self) -> Optional[float]:
        if self.first_timestamp and self.last_timestamp:
            return (self.last_timestamp - self.first_timestamp).total_seconds()
        return None


@dataclass
class WorkflowCluster:
    """Collection of workflows for a worktree, with AI-enriched metadata.

    Cached at ~/.ccsm/workflows/{worktree_key}.json
    """

    worktree: str
    project: str
    workflows: list[Workflow] = field(default_factory=list)
    orphans: list[str] = field(default_factory=list)  # Session IDs not in any workflow
    generated_at: Optional[datetime] = None
    model: Optional[str] = None  # AI model used for naming/clustering
