"""Main screen: three-panel layout with worktree tree, session list, and detail.

This is the primary screen of the CCSM TUI. It:
1. Discovers projects and worktrees on mount
2. Parses session metadata asynchronously
3. Classifies sessions by status/priority
4. Handles panel navigation and session selection
"""

from __future__ import annotations

import logging
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
    parse_session_info,
    parse_session_messages,
)
from ccsm.core.summarizer import generate_ai_title_sync, summarize_session
from ccsm.core.status import classify_all
from ccsm.models.session import Project, SessionInfo, SessionMeta, Status, WorkflowCluster, Worktree
from ccsm.tui.widgets.session_detail import SessionDetail
from ccsm.tui.widgets.session_list import SessionListPanel
from ccsm.tui.widgets.worktree_tree import WorktreeTree

logger = logging.getLogger(__name__)


class MainScreen(Screen):
    """Primary three-panel screen."""

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "resume_session", "Resume"),
        ("s", "summarize_llm", "AI Summary"),
        ("h", "toggle_noise", "Toggle noise"),
        ("slash", "search", "Search"),
        ("tab", "focus_next_panel", "Next panel"),
        ("shift+tab", "focus_previous_panel", "Prev panel"),
        ("1", "switch_tab_1", "Active"),
        ("2", "switch_tab_2", "Back"),
        ("3", "switch_tab_3", "Idea"),
        ("4", "switch_tab_4", "Done"),
        ("D", "batch_archive", "Archive"),
        ("g", "toggle_graph", "Graph"),
    ]

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._projects: list[Project] = []
        self._all_sessions: list[SessionInfo] = []
        self._current_sessions: list[SessionInfo] = []
        self._selected_session: Optional[SessionInfo] = None
        self._all_meta: dict[str, SessionMeta] = {}
        self._last_thoughts: dict[str, str] = {}
        self._running: dict[str, bool] = {}
        self._display_names: dict[str, str] = {}
        self._panel_widgets: list = []  # for tab cycling
        self._auto_summary_timer = None  # Timer for silent auto-summary
        self._search_active: bool = False  # Whether search input is visible
        self._lineage_signals: dict[str, LineageSignals] = {}  # Pain point #1,5,7,8
        self._lineage_types: dict[str, str] = {}  # session_id → "fork"/"compact"/"duplicate"
        self._session_index = SessionIndex()  # Pain point #3,4
        self._graph_visible: bool = False  # Pain point #8: graph toggle
        self._workflow_cluster: Optional[WorkflowCluster] = None  # v2: workflow data
        self._ai_cluster_timer = None  # Timer for background AI clustering

    def compose(self) -> ComposeResult:
        yield Static("⬡ CCSM — Claude Code Session Manager", id="header-bar")
        with Horizontal(id="main-container"):
            # Left panel: Worktree tree
            with Vertical(id="worktree-panel"):
                yield Static(" WORKTREES", classes="panel-title")
                yield WorktreeTree()
            # Middle panel: Session list
            with Vertical(id="session-panel"):
                yield Static(" SESSIONS", classes="panel-title")
                yield Input(
                    placeholder="🔍 Search sessions…",
                    id="search-input",
                    classes="search-input -hidden",
                )
                yield SessionListPanel()
            # Right panel: Session detail
            with Vertical(id="detail-panel"):
                yield Static(" DETAIL", classes="panel-title")
                yield SessionDetail()
        yield Static(
            " [#fb923c]↑↓[/] Navigate  [#fb923c]Tab[/] Panel  "
            "[#fb923c]1-4[/] Status  [#fb923c]/[/] Search  "
            "[#fb923c]r[/] Resume  [#fb923c]s[/] AI  "
            "[#fb923c]g[/] Graph  [#fb923c]D[/] Archive  [#fb923c]q[/] Quit",
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
        running: dict[str, bool],
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
            for session in sessions:
                try:
                    info = parse_session_info(session.jsonl_path)
                    # Fix: sync session_id from JSONL content (file stem may differ)
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

                    # Enrich with display_name and running state
                    if hasattr(self, '_display_names') and session.session_id in self._display_names:
                        session.display_name = self._display_names[session.session_id]
                    if hasattr(self, '_running') and session.session_id in self._running:
                        session.is_running = True

                    parsed.append(session)
                except Exception as e:
                    logger.debug("Skip session %s: %s", session.session_id, e)

            # Classify
            classify_all(parsed, self._all_meta)

            # ── Lineage scanning (pain points #1, #5, #6, #7) ──
            lineage_types: dict[str, str] = {}
            lineage_signals_local: dict[str, LineageSignals] = {}
            for s in parsed:
                try:
                    sig = parse_lineage_signals(
                        s.jsonl_path,
                        display_name=s.display_name,
                    )
                    lineage_signals_local[s.session_id] = sig
                    # Fix pain point #6: use last_message_at from actual messages
                    if sig.last_message_at:
                        s.last_timestamp = sig.last_message_at
                    # Track lineage type for badge display
                    if sig.is_fork:
                        lineage_types[s.session_id] = "fork"
                    elif sig.has_compact_boundary:
                        lineage_types[s.session_id] = "compact"
                except Exception:
                    pass

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

            # Extract last thoughts for non-noise sessions (performance)
            last_thoughts: dict[str, str] = {}
            for session in parsed:
                if session.status == Status.NOISE:
                    continue
                try:
                    msgs = get_last_assistant_messages(session.jsonl_path, count=1)
                    if msgs:
                        last_thoughts[session.session_id] = msgs[-1].content[:200]
                except Exception:
                    pass

            self.app.call_from_thread(
                self._on_sessions_parsed, parsed, last_thoughts, label,
                lineage_types, lineage_signals_local, wf_cluster,
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
    ) -> None:
        """Update UI with parsed sessions."""
        self._current_sessions = sessions
        # Replace (not merge) to avoid stale data from previous worktree
        self._last_thoughts = dict(last_thoughts)
        self._lineage_types = dict(lineage_types) if lineage_types else {}
        self._lineage_signals = dict(lineage_signals) if lineage_signals else {}
        self._workflow_cluster = workflow_cluster

        # Update panel title
        title = self.query_one("#session-panel .panel-title", Static)
        title.update(f" SESSIONS · {label}")

        self._update_session_list()

        # Show workflow overview in detail panel (before any session is selected)
        if self._workflow_cluster and not self._selected_session:
            detail = self.query_one(SessionDetail)
            session_statuses = {s.session_id: s.status for s in self._current_sessions}
            detail.show_workflows(self._workflow_cluster, session_statuses)

        # Schedule silent AI naming after 2s (non-blocking)
        if self._ai_cluster_timer:
            self._ai_cluster_timer.stop()
        if self._workflow_cluster and self._workflow_cluster.workflows:
            self._ai_cluster_timer = self.set_timer(
                2.0, self._try_ai_workflow_naming
            )

    # ── Session selection ───────────────────────────────────────────────────

    def on_session_list_panel_session_selected(
        self, event: SessionListPanel.SessionSelected
    ) -> None:
        """Display session detail when a card is selected.

        Also schedules silent AI summary after 1.5s hover (if no cached LLM summary).
        """
        session = event.session
        self._selected_session = session

        # Cancel any pending auto-summary timer
        if self._auto_summary_timer is not None:
            self._auto_summary_timer.stop()
            self._auto_summary_timer = None

        # Load detail immediately (rule-based extract)
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
        """
        try:
            meta = load_meta(session.session_id)

            # Load last assistant messages for reply display
            last_msgs = get_last_assistant_messages(session.jsonl_path, count=3)
            replies = [m.content for m in last_msgs if m.content]

            # Use summarizer: checks cache first, then extracts milestones
            summary = summarize_session(
                session_id=session.session_id,
                jsonl_path=session.jsonl_path,
                mode="extract",  # Default to free rule-based extraction
            )

            self.app.call_from_thread(
                self._on_detail_loaded, session, meta, summary, replies
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
    ) -> None:
        """Update detail panel on UI thread."""
        # Only update if this session is still selected
        if self._selected_session and self._selected_session.session_id == session.session_id:
            detail = self.query_one(SessionDetail)
            detail.show_session(session, meta=meta, summary=summary, last_replies=replies)

    # ── Actions ─────────────────────────────────────────────────────────────

    def action_quit(self) -> None:
        self.app.exit()

    def action_resume_session(self) -> None:
        """Resume the selected session via claude --resume.

        Passes session_id out via app.exit(result=...) so the caller can
        launch claude AFTER Textual has fully restored the terminal.
        """
        if self._selected_session is None:
            self.notify("No session selected", severity="warning")
            return

        sid = self._selected_session.session_id
        self.app.exit(result=sid)

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

            # Only update UI if user is still on the same session
            if (
                self._selected_session
                and self._selected_session.session_id == session.session_id
            ):
                meta = load_meta(session.session_id)
                last_msgs = get_last_assistant_messages(session.jsonl_path, count=3)
                replies = [m.content for m in last_msgs if m.content]

                self.app.call_from_thread(
                    self._on_detail_loaded, session, meta, summary, replies
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

    def action_focus_next_panel(self) -> None:
        """Cycle focus to next panel."""
        self.screen.focus_next()

    def action_focus_previous_panel(self) -> None:
        """Cycle focus to previous panel."""
        self.screen.focus_previous()

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

    # ── Graph view ────────────────────────────────────────────────────

    def action_toggle_graph(self) -> None:
        """Toggle swimlane timeline view in the detail panel (pain point #8)."""
        self._graph_visible = not self._graph_visible
        if self._graph_visible:
            self._show_graph()
        elif self._selected_session:
            self._load_session_detail(self._selected_session)
        elif self._workflow_cluster:
            detail = self.query_one(SessionDetail)
            session_statuses = {s.session_id: s.status for s in self._current_sessions}
            detail.show_workflows(self._workflow_cluster, session_statuses)

    def _show_graph(self) -> None:
        """Build and display swimlane timeline from workflow data."""
        from ccsm.tui.widgets.swimlane import Swimlane

        if not self._workflow_cluster:
            self.notify("No workflow data — select a worktree first", severity="warning")
            self._graph_visible = False
            return

        detail = self.query_one(SessionDetail)
        detail.remove_children()

        session_statuses = {s.session_id: s.status for s in self._current_sessions}

        widget = Swimlane()
        detail.mount(widget)
        widget.set_data(
            self._workflow_cluster,
            statuses=session_statuses,
            current_session_id=(
                self._selected_session.session_id
                if self._selected_session else None
            ),
        )

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

        # Build intents map
        intents: dict[str, str] = {}
        for s in self._current_sessions:
            meta = self._all_meta.get(s.session_id)
            intents[s.session_id] = (
                (meta.ai_intent if meta else None)
                or s.first_user_content
                or ""
            )

        try:
            updated = name_workflows_sync(cluster, intents)
            self._workflow_cluster = updated
            save_workflows(updated)

            # Refresh detail panel if still showing workflows
            if not self._selected_session and not self._graph_visible:
                session_statuses = {
                    s.session_id: s.status for s in self._current_sessions
                }
                self.app.call_from_thread(
                    lambda: self.query_one(SessionDetail).show_workflows(
                        updated, session_statuses
                    )
                )

            logger.info("AI workflow naming completed: %d workflows", len(updated.workflows))
        except Exception as e:
            logger.debug("AI workflow naming failed: %s", e)
