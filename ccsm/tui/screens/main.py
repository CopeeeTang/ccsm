"""Main screen: dual-panel layout with worktree tree and session list.

Detail is shown via a right-side Drawer overlay (ModalScreen).

This is the primary screen of the CCSM TUI. It:
1. Discovers projects and worktrees on mount
2. Parses session metadata asynchronously
3. Classifies sessions by status/priority
4. Opens detail Drawer on session selection
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Optional

from textual import work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Header, Input, Static

from ccsm.core.discovery import (
    discover_projects,
    load_display_names,
    load_running_sessions,
)
from ccsm.core.index import IndexEntry, SessionIndex
from ccsm.core.lineage import LineageSignals, parse_lineage_signals
from ccsm.core.meta import load_all_meta, load_meta, load_summary, lock_title, save_workflows
from ccsm.core.parser import (
    get_last_assistant_messages,
    parse_session_complete,
    parse_session_info,
    parse_session_messages,
)
from ccsm.core.summarizer import (
    extract_facts_sync,
    generate_ai_title_sync,
    generate_digest_sync,
    summarize_session,
)
from ccsm.core.status import classify_all
from ccsm.models.session import Project, SessionInfo, SessionMeta, Status, WorkflowCluster, Worktree
from ccsm.tui.screens.drawer import SessionDetailDrawer
from ccsm.tui.widgets.session_detail import SessionDetail
from ccsm.tui.widgets.session_list import SessionListPanel
from ccsm.tui.widgets.worktree_tree import WorktreeTree

logger = logging.getLogger(__name__)



def _is_meaningless_title(title: str) -> bool:
    """Check if a session title is meaningless and should be replaced by AI."""
    import re
    if not title:
        return True
    t = title.strip()
    # Slash commands
    if t.startswith("/"):
        return True
    # Too short
    if len(t) < 3:
        return True
    # UUID-like slugs (word-word-word)
    parts = t.split("-")
    if len(parts) == 3 and all(p.isalpha() for p in parts):
        return True
    # Session ID prefix (8-char hex like "06d166cd")
    if re.match(r'^[0-9a-f]{6,12}$', t):
        return True
    # UUID-style IDs (e.g., "0b963606-29b...")
    if re.match(r'^[0-9a-f]{8}-[0-9a-f]', t):
        return True
    # Contains XML/HTML tags (system injection pollution)
    if '<' in t and '>' in t:
        return True
    # Starts with command prefix markers
    if t.startswith("❯ /"):
        return True
    # Single common word titles that aren't meaningful
    if t.lower() in {"cli", "test", "debug", "hi", "hello", "help"}:
        return True
    return False

class MainScreen(Screen):
    """Primary dual-panel screen with Drawer overlay for detail."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "resume_session", "Resume"),
        ("s", "summarize_llm", "AI Summary"),
        ("h", "toggle_noise", "Toggle noise"),
        ("slash", "search", "Search"),
        ("1", "switch_tab_1", "Active"),
        ("2", "switch_tab_2", "Back"),
        ("3", "switch_tab_3", "Idea"),
        ("4", "switch_tab_4", "Done"),
        ("0", "switch_tab_all", "All"),
        ("D", "batch_archive", "Archive"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._projects: list[Project] = []
        self._all_sessions: list[SessionInfo] = []
        self._current_sessions: list[SessionInfo] = []
        self._selected_session: Optional[SessionInfo] = None
        self._all_meta: dict[str, SessionMeta] = {}
        self._last_thoughts: dict[str, str] = {}
        self._running: dict[str, dict] = {}
        self._display_names: dict[str, str] = {}
        self._panel_widgets: list = []  # for tab cycling
        self._auto_summary_timer = None  # Timer for silent auto-summary
        self._search_active: bool = False  # Whether search input is visible
        self._lineage_signals: dict[str, LineageSignals] = {}  # Pain point #1,5,7,8
        self._lineage_types: dict[str, str] = {}  # session_id → "fork"/"compact"/"duplicate"
        self._lineage_graph: dict = {}  # session_id → SessionLineage (parent/child DAG)
        self._session_index = SessionIndex()  # Pain point #3,4
        self._workflow_cluster: Optional[WorkflowCluster] = None  # v2: workflow data
        self._ai_cluster_timer = None  # Timer for background AI clustering
        self._drawer: Optional[SessionDetailDrawer] = None  # Active drawer

    def compose(self) -> ComposeResult:
        yield Static("⬡ CCSM — Claude Code Session Manager", id="header-bar")
        with Horizontal(id="main-container"):
            # Left panel: Worktree tree
            with Vertical(id="worktree-panel"):
                yield Static(" WORKTREES", classes="panel-title")
                yield WorktreeTree()
            # Right panel: Session list (dual mode: list / swimlane)
            with Vertical(id="session-panel"):
                yield Static(" SESSIONS", classes="panel-title")
                yield Input(
                    placeholder="🔍 Search sessions…",
                    id="search-input",
                    classes="search-input -hidden",
                )
                yield SessionListPanel()
        yield Static(
            " [#d97757]↑↓[/] Navigate  "
            "[#d97757]0-4[/] Filter  [#d97757]/[/] Search  "
            "[#d97757]Enter[/] Detail  [#d97757]r[/] Resume  [#d97757]s[/] AI  "
            "[#d97757]D[/] Archive  [#d97757]q[/] Quit",
            id="footer-bar",
        )

    def on_mount(self) -> None:
        """Start loading data after mount."""
        self._load_data()

    @work(thread=True)
    def _load_data(self) -> None:
        """Load project structure in background (fast — no JSONL parsing).

        Strategy: discover projects/worktrees immediately for the tree view.
        Session JSONL parsing is deferred until a worktree is selected.
        """
        try:
            # Step 1: Discover projects & worktrees (fast — filesystem only)
            projects = discover_projects()

            # Step 2: Load running sessions & display names (fast — small files)
            running = load_running_sessions()
            display_names = load_display_names()

            # Step 3: Load all metadata (fast — small JSON files)
            all_meta = load_all_meta()

            # Step 4: Enrich sessions with display_name and running state
            # (no JSONL parsing — just matching by session_id)
            for project in projects:
                for session in project.all_sessions:
                    if session.session_id in display_names:
                        session.display_name = display_names[session.session_id]
                    if session.session_id in running:
                        session.is_running = True

            # Post results to UI thread
            self.app.call_from_thread(
                self._on_data_loaded, projects, all_meta, running, display_names
            )
        except Exception as e:
            logger.exception("Failed to load data: %s", e)
            self.app.call_from_thread(self._on_load_error, str(e))

    def _on_data_loaded(
        self,
        projects: list[Project],
        all_meta: dict[str, SessionMeta],
        running: dict[str, dict],
        display_names: dict[str, str],
    ) -> None:
        """Called on UI thread when discovery completes (fast path)."""
        self._projects = projects
        self._all_meta = all_meta
        self._running = running
        self._display_names = display_names

        # Flatten all sessions (unparsed — just file stubs)
        self._all_sessions = []
        for p in projects:
            self._all_sessions.extend(p.all_sessions)

        # Update worktree tree
        tree = self.query_one(WorktreeTree)
        tree.load_projects(projects)

        # Auto-select: prefer named worktrees with moderate session counts
        best_wt = None
        best_project = None
        best_score = -1

        for project in sorted(projects, key=lambda p: p.name):
            if "observer" in project.name.lower():
                continue
            if project.total_count == 0:
                continue

            all_wts = (project.worktrees or []) + ([project.main_worktree] if project.main_worktree else [])
            for wt in all_wts:
                if not wt or wt.total_count == 0:
                    continue
                # Score: prefer worktrees with 1-100 sessions
                count = wt.total_count
                if count > 200:
                    score = 1  # too large, deprioritize
                elif count > 50:
                    score = 5
                else:
                    score = 10 + count  # moderate count is best
                if wt.name != "main":
                    score += 5  # prefer named worktrees
                if score > best_score:
                    best_score = score
                    best_wt = wt
                    best_project = project

        if best_wt and best_project:
            self._load_worktree_sessions(best_wt, best_project)

    def _on_load_error(self, error: str) -> None:
        """Called when data loading fails."""
        self.notify(f"Load error: {error}", severity="error")

    def _update_session_list(self) -> None:
        """Refresh the session list panel with current sessions."""
        panel = self.query_one(SessionListPanel)
        panel.load_sessions(
            self._current_sessions,
            all_meta=self._all_meta,
            last_thoughts=self._last_thoughts,
            lineage_types=self._lineage_types,
            lineage_graph=self._lineage_graph,
        )

    # ── Worktree selection ──────────────────────────────────────────────────

    def on_worktree_tree_worktree_selected(
        self, event: WorktreeTree.WorktreeSelected
    ) -> None:
        """Parse and display sessions for the selected worktree."""
        self._load_worktree_sessions(event.worktree, event.project)

    def on_worktree_tree_project_selected(
        self, event: WorktreeTree.ProjectSelected
    ) -> None:
        """Parse and display all sessions for a project."""
        project = event.project
        # Collect all sessions from all worktrees
        sessions = list(project.all_sessions)
        self._load_sessions_batch(sessions, f"{project.name}")

    @work(thread=True)
    def _load_worktree_sessions(self, wt: Worktree, project: Project) -> None:
        """Parse JSONL for a single worktree's sessions (lazy loading)."""
        sessions = list(wt.sessions)
        label = f"{project.name}/{wt.name}"
        self._parse_and_display(sessions, label)

    @work(thread=True)
    def _load_sessions_batch(self, sessions: list[SessionInfo], label: str) -> None:
        """Parse JSONL for a batch of sessions."""
        self._parse_and_display(sessions, label)

    def _parse_and_display(self, sessions: list[SessionInfo], label: str) -> None:
        """Parse session info, classify, extract thoughts — runs in thread."""
        try:
            parsed: list[SessionInfo] = []
            lineage_types: dict[str, str] = {}
            lineage_signals_local: dict[str, LineageSignals] = {}
            last_thoughts: dict[str, str] = {}

            # ── Single-pass: read each JSONL once ──
            for session in sessions:
                try:
                    info, sig, last_msgs = parse_session_complete(
                        session.jsonl_path,
                        display_name=(
                            self._display_names.get(session.session_id)
                            if hasattr(self, '_display_names')
                            else None
                        ),
                        last_msg_count=1,
                    )

                    # Sync fields from info to session (same as before)
                    if info.session_id and info.session_id != session.jsonl_path.stem:
                        session.session_id = info.session_id
                    session.slug = info.slug
                    session.cwd = info.cwd
                    session.git_branch = info.git_branch
                    session.first_timestamp = info.first_timestamp
                    session.last_timestamp = info.last_timestamp
                    session.message_count = info.message_count
                    session.user_message_count = info.user_message_count
                    session.first_user_content = info.first_user_content
                    session.total_user_chars = info.total_user_chars
                    session.all_slash_commands = info.all_slash_commands

                    # Propagate JSONL title metadata (audit fix P2-2)
                    session.custom_title = info.custom_title
                    session.ai_title_from_cc = info.ai_title_from_cc
                    session.forked_from_session = info.forked_from_session

                    # Propagate new high-value fields from parser
                    session.last_prompt = info.last_prompt
                    session.compact_summaries = info.compact_summaries
                    session.model_name = info.model_name
                    session.total_input_tokens = info.total_input_tokens
                    session.total_output_tokens = info.total_output_tokens
                    session.last_user_message = info.last_user_message

                    # Enrich with display_name and running state
                    if hasattr(self, '_display_names') and session.session_id in self._display_names:
                        session.display_name = self._display_names[session.session_id]
                    if hasattr(self, '_running') and session.session_id in self._running:
                        session.is_running = True

                    # Lineage signals (from same read)
                    lineage_signals_local[session.session_id] = sig
                    if sig.last_message_at:
                        session.last_timestamp = sig.last_message_at
                    if sig.is_fork:
                        lineage_types[session.session_id] = "fork"
                    elif sig.has_compact_boundary:
                        lineage_types[session.session_id] = "compact"

                    # Last thought (from same read)
                    if last_msgs:
                        last_thoughts[session.session_id] = last_msgs[-1].content[:200]

                    parsed.append(session)
                except Exception as e:
                    logger.debug("Skip session %s: %s", session.session_id, e)

            # ── Sync AI titles from meta to session display_name ──
            # If meta.name exists (previously AI-generated), prefer it over
            # meaningless display_name like /resume, /clear
            for s in parsed:
                meta = self._all_meta.get(s.session_id)
                if meta and meta.name and _is_meaningless_title(s.display_name or ''):
                    s.display_name = meta.name

            # Classify AFTER lineage scanning so corrected timestamps are used
            classify_all(parsed, self._all_meta, all_running=self._running)

            # ── Build search index (pain points #3, #4) ──
            index_entries = []
            for s in parsed:
                meta = self._all_meta.get(s.session_id)
                index_entries.append(IndexEntry(
                    session_id=s.session_id,
                    worktree=label,
                    project=s.project_dir,
                    title=meta.name if meta and meta.name else s.display_title,
                    intent=meta.ai_intent if meta else "",
                    git_branch=s.git_branch or "",
                    first_user_content=s.first_user_content or "",
                    last_message_at=s.last_timestamp,
                    status=s.status.value if s.status else "",
                    tags=meta.tags if meta else [],
                ))
            self._session_index.update_entries(index_entries)

            # ── Build workflow cluster from lineage (v2) ──
            from ccsm.core.lineage import build_lineage_graph
            from ccsm.core.workflow import extract_workflows
            graph = build_lineage_graph(lineage_signals_local)

            # Enrich lineage_types from graph (graph has more accurate types)
            # The signal-based detection above only catches compact/fork from
            # JSONL signals; the graph also detects DUPLICATE via time overlap.
            for sid, node in graph.items():
                if sid not in lineage_types:
                    lt_str = node.lineage_type.value  # "root"/"compact"/"fork"/"duplicate"
                    if lt_str != "root":
                        lineage_types[sid] = lt_str

            wf_titles = {}
            for s in parsed:
                meta_s = self._all_meta.get(s.session_id)
                wf_titles[s.session_id] = (
                    (meta_s.name if meta_s and meta_s.name else None)
                    or s.display_title
                )
            wf_cluster = extract_workflows(
                graph, lineage_signals_local, wf_titles, label, ""
            )
            # Mark active workflows
            active_sids = {s.session_id for s in parsed if s.status == Status.ACTIVE}
            for wf in wf_cluster.workflows:
                wf.is_active = any(sid in active_sids for sid in wf.sessions)

            # NOTE: last_thoughts already collected in single-pass loop above

            self.app.call_from_thread(
                self._on_sessions_parsed, parsed, last_thoughts, label,
                lineage_types, lineage_signals_local, wf_cluster, graph,
            )
        except Exception as e:
            logger.warning("Failed to parse sessions: %s", e)

    def _on_sessions_parsed(
        self,
        sessions: list[SessionInfo],
        last_thoughts: dict[str, str],
        label: str,
        lineage_types: dict[str, str] | None = None,
        lineage_signals: dict[str, LineageSignals] | None = None,
        workflow_cluster: Optional[WorkflowCluster] = None,
        lineage_graph: dict | None = None,
    ) -> None:
        """Update UI with parsed sessions."""
        self._current_sessions = sessions
        # Replace (not merge) to avoid stale data from previous worktree
        self._last_thoughts = dict(last_thoughts)
        self._lineage_types = dict(lineage_types) if lineage_types else {}
        self._lineage_signals = dict(lineage_signals) if lineage_signals else {}
        self._workflow_cluster = workflow_cluster
        self._lineage_graph = dict(lineage_graph) if lineage_graph else {}

        # Clear stale selected session if it's not in the new dataset
        if self._selected_session:
            new_sids = {s.session_id for s in sessions}
            if self._selected_session.session_id not in new_sids:
                self._selected_session = None
                if self._auto_summary_timer is not None:
                    self._auto_summary_timer.stop()
                    self._auto_summary_timer = None

        # Update panel title
        title = self.query_one("#session-panel .panel-title", Static)
        title.update(f" SESSIONS · {label}")

        self._update_session_list()

        # Schedule silent AI naming after 2s (non-blocking)
        if self._ai_cluster_timer:
            self._ai_cluster_timer.stop()
        if self._workflow_cluster and self._workflow_cluster.workflows:
            self._ai_cluster_timer = self.set_timer(
                2.0, self._try_ai_workflow_naming
            )

        # Schedule background batch AI title generation (non-blocking)
        self.set_timer(1.0, lambda: self._batch_enrich_sessions())

    # ── Session selection ───────────────────────────────────────────────────

    def on_session_list_panel_session_selected(
        self, event: SessionListPanel.SessionSelected
    ) -> None:
        """Open detail Drawer when a card is selected.

        Also schedules silent AI summary after 1.5s hover (if no cached LLM summary).
        """
        session = event.session
        self._selected_session = session

        # Cancel any pending auto-summary timer
        if self._auto_summary_timer is not None:
            self._auto_summary_timer.stop()
            self._auto_summary_timer = None

        # Open Drawer and start loading detail
        self._drawer = SessionDetailDrawer()
        self.app.push_screen(self._drawer)
        self._load_session_detail(session)

        # Schedule silent LLM summary after 1.5s if:
        # - session has enough messages (>8)
        # - session is ACTIVE or DONE (not NOISE)
        # - no cached LLM summary yet
        if (
            session.message_count > 8
            and session.status not in (Status.NOISE,)
        ):
            self._auto_summary_timer = self.set_timer(
                1.5,
                lambda: self._try_silent_summary(session),
            )

    @work(thread=True)
    def _load_session_detail(self, session: SessionInfo) -> None:
        """Load session detail data in background.

        Uses summarizer module for milestone extraction (extract mode by default).
        Cached summaries are reused automatically.
        Also triggers AI title generation if no display_name is set.
        Loads compact summary parsing and tool_use detail data for new layout.
        """
        try:
            meta = load_meta(session.session_id)

            # Load last assistant messages for reply display
            from ccsm.core.parse_cache import cached_parse_complete
            _, _, cached_msgs = cached_parse_complete(session.jsonl_path)
            # cached_msgs defaults to last 1; detail panel needs 3
            if len(cached_msgs) >= 3:
                last_msgs = cached_msgs[-3:]
            else:
                last_msgs = get_last_assistant_messages(session.jsonl_path, count=3)
            replies = [m.content for m in last_msgs if m.content]

            # Use summarizer: checks cache first, then extracts milestones
            summary = summarize_session(
                session_id=session.session_id,
                jsonl_path=session.jsonl_path,
                mode="extract",  # Default to free rule-based extraction
            )

            # Parse compact summary (zero cost — data already in SessionInfo)
            compact_parsed = None
            if session.compact_summaries:
                from ccsm.core.compact_parser import parse_compact_summary
                compact_parsed = parse_compact_summary(session.compact_summaries[-1])

            # Deep parse for tool_use operations (on-demand, slightly heavier)
            from ccsm.core.parser import parse_session_detail
            detail_data = parse_session_detail(session.jsonl_path)

            self.app.call_from_thread(
                self._on_detail_loaded, session, meta, summary, replies,
                detail_data, compact_parsed,
            )

            # AI title generation — lazy, only if no name cached
            if not session.display_name and not (meta and meta.name):
                self._generate_ai_title_for(session)

        except Exception as e:
            logger.warning("Failed to load detail for %s: %s", session.session_id, e)

    def _on_detail_loaded(
        self,
        session: SessionInfo,
        meta,
        summary,
        replies: list[str],
        detail_data=None,
        compact_parsed=None,
    ) -> None:
        """Update Drawer detail on UI thread."""
        # Only update if this session is still selected and drawer is open
        if self._selected_session and self._selected_session.session_id == session.session_id:
            if self._drawer is not None:
                self._drawer.show_session(
                    session, meta=meta, summary=summary, last_replies=replies,
                    detail_data=detail_data, compact_parsed=compact_parsed,
                )

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.app.exit()

    def action_resume_session(self) -> None:
        """Resume the selected session via claude --resume.

        Uses the JSONL file path instead of session_id to ensure cross-worktree
        resume works correctly. Claude Code resolves session_id by cwd, but
        the JSONL path works regardless of current working directory.
        """
        if self._selected_session is None:
            self.notify("No session selected", severity="warning")
            return

        session = self._selected_session
        # Use jsonl_path for reliable cross-worktree resume
        if session.jsonl_path and session.jsonl_path.exists():
            self.app.exit(result=str(session.jsonl_path))
        else:
            # Fallback to session_id (same-project resume)
            self.app.exit(result=session.session_id)

    def action_toggle_noise(self) -> None:
        """Toggle NOISE session visibility."""
        panel = self.query_one(SessionListPanel)
        panel.toggle_noise()

    def _try_silent_summary(self, session: SessionInfo) -> None:
        """Silently trigger LLM summary if user is still on the same session.

        Called after 1.5s hover delay. Only triggers if:
        - User hasn't switched to another session
        - No existing LLM summary cached
        """
        # Check if user is still on the same session
        if (
            self._selected_session is None
            or self._selected_session.session_id != session.session_id
        ):
            return

        # Check if LLM summary already cached
        from ccsm.core.meta import load_summary as _load_summary
        cached = _load_summary(session.session_id)
        if cached and cached.mode == "llm":
            return  # Already have LLM summary

        # Trigger silently (no notification)
        self._run_llm_summarize(session, silent=True)

    def action_summarize_llm(self) -> None:
        """Trigger LLM-powered summary for the selected session."""
        if self._selected_session is None:
            self.notify("No session selected", severity="warning")
            return
        self.notify("Generating AI summary…", severity="information")
        self._run_llm_summarize(self._selected_session, silent=False)

    @work(thread=True)
    def _run_llm_summarize(self, session: SessionInfo, silent: bool = False) -> None:
        """Call LLM summarizer in background thread.

        Pipeline: summary (milestones) → digest (5-dimension) → facts (atomic).
        All three are cached; UI is updated once after all complete.

        Args:
            silent: If True, don't show notifications (used for auto-summary).
        """
        try:
            summary = summarize_session(
                session_id=session.session_id,
                jsonl_path=session.jsonl_path,
                mode="llm",
                force=not silent,  # Silent mode uses cache if available
            )

            # Chain: generate digest after summary
            compact_text = (
                session.compact_summaries[-1]
                if session.compact_summaries
                else None
            )
            digest = generate_digest_sync(
                session_id=session.session_id,
                jsonl_path=session.jsonl_path,
                compact_summary_text=compact_text,
                milestones=summary.milestones,
                force=not silent,
            )
            if digest:
                summary.digest = digest

            # Chain: extract facts after digest
            facts = extract_facts_sync(
                session_id=session.session_id,
                compact_summary_text=compact_text,
                milestones=summary.milestones,
                digest=summary.digest,
                force=not silent,
            )
            if facts:
                summary.facts = facts

            # Save once with all three results
            if digest or facts:
                from ccsm.core.meta import save_summary
                save_summary(summary)

            # Only update UI if user is still on the same session
            if (
                self._selected_session
                and self._selected_session.session_id == session.session_id
            ):
                meta = load_meta(session.session_id)
                last_msgs = get_last_assistant_messages(session.jsonl_path, count=3)
                replies = [m.content for m in last_msgs if m.content]

                # Re-parse compact and detail data so LLM refresh doesn't lose them
                compact_parsed = None
                if session.compact_summaries:
                    from ccsm.core.compact_parser import parse_compact_summary
                    compact_parsed = parse_compact_summary(session.compact_summaries[-1])

                from ccsm.core.parser import parse_session_detail
                detail_data = parse_session_detail(session.jsonl_path)

                self.app.call_from_thread(
                    self._on_detail_loaded, session, meta, summary, replies,
                    detail_data, compact_parsed,
                )
                if not silent:
                    self.app.call_from_thread(
                        lambda msg=summary.mode: self.notify(f"AI summary generated ({msg})", severity="information")
                    )
        except Exception as e:
            logger.warning("LLM summarize failed: %s", e)
            if not silent:
                self.app.call_from_thread(
                    lambda e=e: self.notify(f"Summary failed: {e}", severity="error")
                )

    def action_search(self) -> None:
        """Toggle search input visibility."""
        search_input = self.query_one("#search-input", Input)
        if self._search_active:
            # Close search — restore full session list
            self._search_active = False
            search_input.add_class("-hidden")
            search_input.value = ""
            self._update_session_list()
        else:
            # Open search
            self._search_active = True
            search_input.remove_class("-hidden")
            search_input.focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        """Filter session list as user types in search input."""
        if event.input.id != "search-input":
            return
        query = event.value.strip()
        if not query:
            self._update_session_list()
            return

        # Use index for full-text search (pain point #4)
        results = self._session_index.search(query)
        matched_ids = {r.session_id for r in results}
        filtered = [s for s in self._current_sessions if s.session_id in matched_ids]

        panel = self.query_one(SessionListPanel)
        panel.load_sessions(
            filtered,
            all_meta=self._all_meta,
            last_thoughts=self._last_thoughts,
            lineage_types=self._lineage_types,
            lineage_graph=self._lineage_graph,
        )

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Select first result on Enter in search input."""
        if event.input.id != "search-input":
            return
        # Focus back to session list
        panel = self.query_one(SessionListPanel)
        panel.focus()

    def on_key(self, event) -> None:
        """Handle Escape to close search."""
        if event.key == "escape" and self._search_active:
            self.action_search()  # Toggle off
            event.prevent_default()
            event.stop()

    # ── AI title generation ────────────────────────────────────────────

    @work(thread=True)
    def _generate_ai_title_for(self, session: SessionInfo) -> None:
        """Generate AI title for a session in background.

        Called lazily when a session is selected and has no display_name.
        On success, updates the session card and detail panel.
        """
        try:
            messages = parse_session_messages(session.jsonl_path)
            if not messages or len(messages) < 4:
                return  # Too few messages for meaningful title

            result = generate_ai_title_sync(session.session_id, messages)
            if result is None:
                return

            title, intent = result

            # Update session object
            session.display_name = title

            # Lock title in CCSM sidecar (pain point #2: prevent revert)
            try:
                lock_title(session.session_id, title)
            except Exception:
                pass  # best-effort, don't fail AI title flow

            # Refresh card list and detail if still viewing this session
            if (
                self._selected_session
                and self._selected_session.session_id == session.session_id
            ):
                self.app.call_from_thread(self._update_session_list)
                # Re-load detail to reflect new title
                self._load_session_detail(session)

            logger.info("AI title applied: %s → %r", session.session_id[:8], title)
        except Exception as e:
            logger.debug("AI title generation skipped for %s: %s", session.session_id[:8], e)

    # ── Tab switching via number keys ──────────────────────────────────

    def _switch_tab(self, status: Status) -> None:
        """Switch session list to a specific status tab."""
        panel = self.query_one(SessionListPanel)
        panel.set_active_tab(status)

    def action_switch_tab_1(self) -> None:
        self._switch_tab(Status.ACTIVE)

    def action_switch_tab_2(self) -> None:
        self._switch_tab(Status.BACKGROUND)

    def action_switch_tab_3(self) -> None:
        self._switch_tab(Status.IDEA)

    def action_switch_tab_4(self) -> None:
        self._switch_tab(Status.DONE)

    def action_switch_tab_all(self) -> None:
        """Switch to ALL filter (show all statuses)."""
        panel = self.query_one(SessionListPanel)
        panel.set_filter_all()

    # ── Batch operations ───────────────────────────────────────────────

    def action_batch_archive(self) -> None:
        """Archive the currently selected session (mark as DONE via sidecar meta).

        Shift+D archives a single session — the one currently selected.
        """
        if self._selected_session is None:
            self.notify("No session selected", severity="warning")
            return

        session = self._selected_session
        if session.status == Status.DONE:
            self.notify("Already archived", severity="information")
            return

        self._archive_session(session)

    @work(thread=True)
    def _archive_session(self, session: SessionInfo) -> None:
        """Archive a session by setting status_override=DONE in sidecar meta."""
        from ccsm.core.meta import load_meta as _load_meta, save_meta as _save_meta

        try:
            meta = _load_meta(session.session_id)
            meta.status_override = Status.DONE
            _save_meta(meta)

            # Update in-memory state
            session.status = Status.DONE

            self.app.call_from_thread(self._update_session_list)
            self.app.call_from_thread(
                lambda t=session.display_title[:30]: self.notify(f"Archived: {t}", severity="information")
            )
        except Exception as e:
            logger.warning("Archive failed for %s: %s", session.session_id, e)
            self.app.call_from_thread(
                lambda e=e: self.notify(f"Archive failed: {e}", severity="error")
            )


    # ── Batch AI enrichment ──────────────────────────────────────────

    @work(thread=True)
    def _batch_enrich_sessions(self) -> None:
        """Background batch: generate AI titles + extract summaries for all sessions.

        Runs after initial load to populate AI content that was previously
        only generated on-demand (lazy loading). This ensures the session list
        shows meaningful titles instead of /resume, /clear etc.
        """
        from ccsm.core.meta import load_summary as _load_summary

        sessions = list(self._current_sessions)
        if not sessions:
            return

        # ── Phase 1: Batch extract summaries (zero cost, no API) ──
        extract_count = 0
        for s in sessions:
            if s.status == Status.NOISE:
                continue
            try:
                cached = _load_summary(s.session_id)
                if cached and cached.milestones:
                    continue  # Already has summary
                if s.message_count < 4:
                    continue  # Too short
                summarize_session(
                    session_id=s.session_id,
                    jsonl_path=s.jsonl_path,
                    mode="extract",
                )
                extract_count += 1
            except Exception:
                pass

        if extract_count > 0:
            logger.info("Batch extracted %d summaries", extract_count)

        # ── Phase 2: Batch AI title generation (API calls, throttled) ──
        # Only generate AI titles for sessions that truly have NO title at all.
        # Respect user's original display_name — don't replace meaningful titles.
        status_rank = {
            Status.ACTIVE: 0, Status.BACKGROUND: 1,
            Status.IDEA: 2, Status.DONE: 3, Status.NOISE: 99,
        }
        # M-1 fix: include sessions with meaningless titles (slash commands, random slugs)
        candidates = [
            s for s in sessions
            if s.message_count >= 12  # Higher threshold: need enough content
            and _is_meaningless_title(s.display_title)  # Covers /resume, random slugs, empty
            and s.status != Status.NOISE
        ]
        candidates.sort(key=lambda s: status_rank.get(s.status, 99))

        # Limit to 10 per batch (conservative — respect API budget)
        candidates = candidates[:10]

        if not candidates:
            return

        for i, session in enumerate(candidates):
            # Check if we are still viewing the same worktree
            if session not in self._current_sessions:
                break

            try:
                messages = parse_session_messages(session.jsonl_path)
                if not messages or len(messages) < 4:
                    continue

                result = generate_ai_title_sync(session.session_id, messages)
                if result is None:
                    continue

                ai_title, intent = result
                session.display_name = ai_title

                # Save to sidecar
                try:
                    lock_title(session.session_id, ai_title)
                except Exception:
                    pass

                # Refresh UI every 3 sessions (avoid too frequent updates)
                if (i + 1) % 3 == 0 or i == len(candidates) - 1:
                    self.app.call_from_thread(self._update_session_list)

                logger.info(
                    "Batch AI title [%d/%d]: %s → %r",
                    i + 1, len(candidates), session.session_id[:8], ai_title,
                )

                # Throttle: 0.5s between API calls
                time.sleep(0.5)

            except Exception as e:
                logger.debug("Batch AI title failed for %s: %s", session.session_id[:8], e)

        # Final refresh
        self.app.call_from_thread(self._update_session_list)

    def _try_ai_workflow_naming(self) -> None:
        """Trigger background AI workflow naming if not already cached."""
        if not self._workflow_cluster:
            return
        # Check if any workflow lacks an ai_name
        needs_naming = any(
            wf.ai_name is None for wf in self._workflow_cluster.workflows
        )
        if not needs_naming and not self._workflow_cluster.orphans:
            return
        self._run_ai_clustering()

    @work(thread=True)
    def _run_ai_clustering(self) -> None:
        """Background AI clustering — name workflows and assign orphans."""
        from ccsm.core.cluster import name_workflows_sync

        cluster = self._workflow_cluster
        if not cluster:
            return

        # Snapshot the worktree key to detect stale results
        snapshot_worktree = cluster.worktree

        # Build intents map from current sessions snapshot
        sessions_snapshot = list(self._current_sessions)
        intents: dict[str, str] = {}
        for s in sessions_snapshot:
            meta = self._all_meta.get(s.session_id)
            intents[s.session_id] = (
                (meta.ai_intent if meta else None)
                or s.first_user_content
                or ""
            )

        try:
            updated = name_workflows_sync(cluster, intents)

            # Discard if user switched worktrees while we were running
            if (self._workflow_cluster is None
                    or self._workflow_cluster.worktree != snapshot_worktree):
                logger.debug("AI naming result discarded — worktree changed")
                return

            self._workflow_cluster = updated
            save_workflows(updated)

            # Refresh detail panel if still showing workflows
            # NOTE: In Drawer architecture, SessionDetail lives inside
            # ModalScreen, not MainScreen. Skip direct query to avoid NoMatches.
            # Workflow display will be refreshed next time user opens a drawer.

            logger.info("AI workflow naming completed: %d workflows", len(updated.workflows))
        except Exception as e:
            logger.debug("AI workflow naming failed: %s", e)
