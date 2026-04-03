# CCSM Workflow Swimlane & AI Clustering — Implementation Plan v2

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the flat git-log-style `session_graph.py` with a two-layer workflow visualization: a folded Workflow List in the detail panel (default) and a full-screen Swimlane timeline (`g` key). Add AI-powered workflow naming and orphan session clustering, running silently in the background.

**Architecture:** Build on the completed v1 infrastructure (`lineage.py`, `index.py`, `parser.py` extensions, `meta.py` lock_title). Add three new core modules (`workflow.py`, `cluster.py`, cache I/O in `meta.py`) and two new TUI widgets (`workflow_list.py`, `swimlane.py`). Replace the existing `session_graph.py` with swimlane rendering. AI clustering uses the same Haiku API pattern as `summarizer.py`.

**Tech Stack:** Python 3.10+, dataclasses, Textual, Rich markup, Anthropic SDK (claude-haiku-4.5 via local proxy)

**Depends on (completed v1):**
- `ccsm/models/session.py` — `LineageType`, `SessionLineage`, `SessionMeta.title_locked/last_message_at/lineage`
- `ccsm/core/lineage.py` — `parse_lineage_signals()`, `build_lineage_graph()`, `LineageSignals`
- `ccsm/core/index.py` — `SessionIndex`, `IndexEntry`
- `ccsm/core/parser.py` — `parse_session_timestamps()`
- `ccsm/core/meta.py` — `lock_title()`, lineage serialization
- `ccsm/core/discovery.py` — `detect_duplicates()`
- `ccsm/tui/screens/main.py` — lineage/index integration, `_lineage_signals`, `_session_index`, `action_toggle_graph()`

---

## File Structure

### New Files
| File | Responsibility |
|---|---|
| `ccsm/core/workflow.py` | Extract compact-chain workflows from lineage DAG (pure rules, no AI) |
| `ccsm/core/cluster.py` | AI-powered workflow naming + orphan clustering via Haiku |
| `ccsm/tui/widgets/workflow_list.py` | Folded workflow list widget (detail panel section) |
| `ccsm/tui/widgets/swimlane.py` | Full-screen dual-axis swimlane timeline |
| `tests/test_workflow.py` | Workflow chain extraction tests |
| `tests/test_cluster.py` | AI clustering tests (mocked API) |
| `tests/test_workflow_list.py` | Widget rendering tests |

### Modified Files
| File | Changes |
|---|---|
| `ccsm/models/session.py:322` | Add `Workflow` and `WorkflowCluster` dataclasses after `Project` |
| `ccsm/core/meta.py:436` | Add `save_workflows()` / `load_workflows()` cache I/O |
| `ccsm/tui/widgets/session_graph.py` | **Delete** — replaced by `swimlane.py` |
| `ccsm/tui/widgets/session_detail.py:115` | Add `_mount_workflows_section()` call in `_rebuild()` |
| `ccsm/tui/screens/main.py:694` | Replace `_show_graph()` with swimlane, add async AI clustering trigger |

---

### Task 1: Workflow and WorkflowCluster Data Models

**Files:**
- Modify: `ccsm/models/session.py:319` (add dataclasses after `Project`)
- Test: `tests/test_workflow.py` (model tests only in this task)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workflow.py
"""Tests for workflow data models and chain extraction."""
from datetime import datetime, timezone

from ccsm.models.session import Workflow, WorkflowCluster


def test_workflow_defaults():
    wf = Workflow(workflow_id="wf-1", sessions=["s1", "s2", "s3"])
    assert wf.workflow_id == "wf-1"
    assert len(wf.sessions) == 3
    assert wf.name is None
    assert wf.ai_name is None
    assert wf.fork_branches == []
    assert wf.is_active is False


def test_workflow_display_name_priority():
    """ai_name takes priority over auto-generated name."""
    wf = Workflow(
        workflow_id="wf-1",
        sessions=["s1"],
        name="s1-title → s2-title",
        ai_name="登录系统",
    )
    assert wf.display_name == "登录系统"


def test_workflow_display_name_fallback():
    """Falls back to name, then workflow_id."""
    wf1 = Workflow(workflow_id="wf-1", sessions=["s1"], name="auto-name")
    assert wf1.display_name == "auto-name"

    wf2 = Workflow(workflow_id="wf-2", sessions=["s1"])
    assert wf2.display_name == "wf-2"


def test_workflow_cluster_defaults():
    cluster = WorkflowCluster(
        worktree="memory",
        project="GUI",
        workflows=[],
        orphans=[],
    )
    assert cluster.generated_at is None
    assert cluster.model is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_workflow.py -v`
Expected: FAIL — `ImportError: cannot import name 'Workflow'`

- [ ] **Step 3: Add Workflow and WorkflowCluster dataclasses**

In `ccsm/models/session.py`, after the `Project` class (after line 319), add:

```python
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
    fork_branches: list[str] = field(default_factory=list)  # Session IDs that forked off
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
        return len(self.sessions) + len(self.fork_branches)

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_workflow.py -v`
Expected: PASS — all 4 tests

- [ ] **Step 5: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/models/session.py tests/test_workflow.py
git commit -m "feat(ccsm): add Workflow and WorkflowCluster data models"
```

---

### Task 2: Workflow Chain Extraction from Lineage DAG

**Files:**
- Create: `ccsm/core/workflow.py`
- Modify: `tests/test_workflow.py` (append chain extraction tests)

This module takes the lineage DAG from `build_lineage_graph()` and groups sessions into Workflow chains. Pure deterministic rules — no AI.

**Algorithm:**
1. Find all ROOT nodes (no parent) that are not DUPLICATE children
2. For each ROOT, walk its children: COMPACT/DUPLICATE children extend the chain; FORK children become `fork_branches`
3. Assign auto-generated names from session titles: `"title1 → title2 → title3"`
4. Sessions not reachable from any chain become `orphans`

- [ ] **Step 1: Write chain extraction tests**

Append to `tests/test_workflow.py`:

```python
from ccsm.core.workflow import extract_workflows
from ccsm.core.lineage import LineageSignals, build_lineage_graph
from ccsm.models.session import LineageType


def _make_signals(
    sid: str,
    first: str = "2026-04-01T10:00:00Z",
    last: str = "2026-04-01T10:30:00Z",
    cwd: str = "/project",
    branch: str = "main",
    is_fork: bool = False,
    display_name: str | None = None,
) -> LineageSignals:
    from datetime import datetime, timezone
    return LineageSignals(
        session_id=sid,
        first_message_at=datetime.fromisoformat(first.replace("Z", "+00:00")),
        last_message_at=datetime.fromisoformat(last.replace("Z", "+00:00")),
        cwd=cwd,
        git_branch=branch,
        is_fork=is_fork,
        fork_hint="display_name_branch_suffix" if is_fork else None,
    )


def test_single_session_becomes_one_workflow():
    signals = {"s1": _make_signals("s1")}
    graph = build_lineage_graph(signals)
    titles = {"s1": "fix-bug"}
    wf_cluster = extract_workflows(graph, signals, titles, "main", "GUI")
    assert len(wf_cluster.workflows) == 1
    assert wf_cluster.workflows[0].sessions == ["s1"]
    assert wf_cluster.orphans == []


def test_duplicate_chain_becomes_one_workflow():
    """Two sessions with same cwd+branch and time overlap → single workflow."""
    signals = {
        "s1": _make_signals("s1", first="2026-04-01T10:00:00Z", last="2026-04-01T10:30:00Z"),
        "s2": _make_signals("s2", first="2026-04-01T10:28:00Z", last="2026-04-01T11:00:00Z"),
    }
    graph = build_lineage_graph(signals)
    titles = {"s1": "fix-bug", "s2": "fix-bug-v2"}
    wf_cluster = extract_workflows(graph, signals, titles, "main", "GUI")
    assert len(wf_cluster.workflows) == 1
    wf = wf_cluster.workflows[0]
    assert "s1" in wf.sessions
    assert "s2" in wf.sessions
    assert wf.name == "fix-bug → fix-bug-v2"


def test_fork_becomes_branch():
    """Fork session goes into fork_branches, not main chain."""
    signals = {
        "s1": _make_signals("s1"),
        "s2": _make_signals("s2", is_fork=True),
    }
    graph = build_lineage_graph(signals)
    titles = {"s1": "main-work", "s2": "experiment"}
    wf_cluster = extract_workflows(graph, signals, titles, "main", "GUI")
    # s2 is a fork with no parent link in graph → becomes its own workflow
    assert len(wf_cluster.workflows) == 2


def test_independent_sessions_separate_workflows():
    """Sessions with different branches → separate workflows."""
    signals = {
        "s1": _make_signals("s1", branch="main"),
        "s2": _make_signals("s2", branch="feature", first="2026-04-01T12:00:00Z", last="2026-04-01T13:00:00Z"),
    }
    graph = build_lineage_graph(signals)
    titles = {"s1": "main-work", "s2": "feature-work"}
    wf_cluster = extract_workflows(graph, signals, titles, "main", "GUI")
    assert len(wf_cluster.workflows) == 2


def test_workflow_timestamps():
    """Workflow timestamps span from earliest to latest session."""
    signals = {
        "s1": _make_signals("s1", first="2026-04-01T10:00:00Z", last="2026-04-01T10:30:00Z"),
        "s2": _make_signals("s2", first="2026-04-01T10:28:00Z", last="2026-04-01T11:00:00Z"),
    }
    graph = build_lineage_graph(signals)
    titles = {"s1": "a", "s2": "b"}
    wf_cluster = extract_workflows(graph, signals, titles, "main", "GUI")
    wf = wf_cluster.workflows[0]
    assert wf.first_timestamp is not None
    assert wf.last_timestamp is not None
    assert wf.first_timestamp < wf.last_timestamp
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_workflow.py::test_single_session_becomes_one_workflow -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccsm.core.workflow'`

- [ ] **Step 3: Implement extract_workflows**

```python
# ccsm/core/workflow.py
"""Extract compact-chain workflows from the lineage DAG.

A workflow is a sequence of sessions connected by COMPACT or DUPLICATE
lineage edges — they represent the same logical unit of work continued
across multiple Claude Code sessions.

This module is pure deterministic rules — no AI. AI naming is handled
by cluster.py after workflows are extracted.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from ccsm.core.lineage import LineageSignals
from ccsm.models.session import (
    LineageType,
    SessionLineage,
    Workflow,
    WorkflowCluster,
)

log = logging.getLogger(__name__)


def extract_workflows(
    graph: dict[str, SessionLineage],
    signals: dict[str, LineageSignals],
    titles: dict[str, str],
    worktree: str,
    project: str,
) -> WorkflowCluster:
    """Group sessions into workflows based on lineage edges.

    Algorithm:
      1. Find all root nodes (parent_id is None)
      2. For each root, walk children:
         - DUPLICATE/COMPACT children → extend the chain (same workflow)
         - FORK children → record as fork_branches
      3. Sessions unreachable from any root → orphans
      4. Generate auto-names from chain titles: "title1 → title2"
    """
    claimed: set[str] = set()
    workflows: list[Workflow] = []

    # ── Step 1: Find roots ────────────────────────────────────────────
    roots = [
        sid for sid, node in graph.items()
        if node.parent_id is None
    ]
    # Sort roots by first_message_at for deterministic output
    roots.sort(
        key=lambda s: signals[s].first_message_at
        if s in signals and signals[s].first_message_at
        else datetime.max.replace(tzinfo=timezone.utc)
    )

    # ── Step 2: Walk each root to build workflow chains ───────────────
    for root_sid in roots:
        if root_sid in claimed:
            continue

        chain: list[str] = []
        forks: list[str] = []

        def _walk_chain(sid: str) -> None:
            if sid in claimed:
                return
            claimed.add(sid)
            chain.append(sid)

            node = graph.get(sid)
            if not node:
                return

            # Sort children by timestamp for deterministic order
            children = sorted(
                node.children,
                key=lambda c: signals[c].first_message_at
                if c in signals and signals[c].first_message_at
                else datetime.max.replace(tzinfo=timezone.utc),
            )

            for child_id in children:
                child_node = graph.get(child_id)
                if not child_node or child_id in claimed:
                    continue
                if child_node.lineage_type == LineageType.FORK:
                    forks.append(child_id)
                    claimed.add(child_id)
                else:
                    # DUPLICATE or COMPACT → same chain
                    _walk_chain(child_id)

        _walk_chain(root_sid)

        if not chain:
            continue

        # ── Build auto-name from chain titles ──────────────────────
        chain_titles = [titles.get(sid, sid[:8]) for sid in chain]
        auto_name = " → ".join(chain_titles) if chain_titles else None

        # ── Compute timestamps ──────────────────────────────────────
        all_sids = chain + forks
        first_ts = _earliest(all_sids, signals)
        last_ts = _latest(all_sids, signals)

        wf = Workflow(
            workflow_id=f"wf-{chain[0]}",
            sessions=chain,
            name=auto_name,
            fork_branches=forks,
            root_session_id=chain[0],
            first_timestamp=first_ts,
            last_timestamp=last_ts,
        )
        workflows.append(wf)

    # ── Step 3: Orphans ──────────────────────────────────────────────
    orphans = [sid for sid in graph if sid not in claimed]

    return WorkflowCluster(
        worktree=worktree,
        project=project,
        workflows=workflows,
        orphans=orphans,
    )


def _earliest(
    sids: list[str], signals: dict[str, LineageSignals]
) -> Optional[datetime]:
    timestamps = [
        signals[s].first_message_at
        for s in sids
        if s in signals and signals[s].first_message_at
    ]
    return min(timestamps) if timestamps else None


def _latest(
    sids: list[str], signals: dict[str, LineageSignals]
) -> Optional[datetime]:
    timestamps = [
        signals[s].last_message_at
        for s in sids
        if s in signals and signals[s].last_message_at
    ]
    return max(timestamps) if timestamps else None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_workflow.py -v`
Expected: PASS — all 9 tests

- [ ] **Step 5: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/workflow.py tests/test_workflow.py
git commit -m "feat(ccsm): extract compact-chain workflows from lineage DAG"
```

---

### Task 3: Workflow Cache I/O in Meta

**Files:**
- Modify: `ccsm/core/meta.py` (add `save_workflows()` / `load_workflows()`)
- Modify: `tests/test_meta.py` (append round-trip tests)

Workflow clusters are cached at `~/.ccsm/workflows/{project}_{worktree}.json` so the TUI loads instantly on subsequent visits.

- [ ] **Step 1: Write failing test**

Create or append to `tests/test_meta.py`:

```python
def test_workflow_cache_round_trip(tmp_path, monkeypatch):
    """WorkflowCluster survives save → load cycle."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from ccsm.core import meta as m
    importlib.reload(m)

    from datetime import datetime, timezone
    from ccsm.models.session import Workflow, WorkflowCluster

    cluster = WorkflowCluster(
        worktree="memory",
        project="GUI",
        workflows=[
            Workflow(
                workflow_id="wf-s1",
                sessions=["s1", "s2"],
                name="fix-bug → fix-v2",
                ai_name="登录修复",
                fork_branches=["s3"],
                root_session_id="s1",
                first_timestamp=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
                last_timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
                is_active=True,
            ),
        ],
        orphans=["s4"],
        generated_at=datetime(2026, 4, 2, 8, 0, tzinfo=timezone.utc),
        model="claude-haiku-4.5",
    )

    m.save_workflows(cluster)
    loaded = m.load_workflows("GUI", "memory")

    assert loaded is not None
    assert loaded.worktree == "memory"
    assert loaded.project == "GUI"
    assert len(loaded.workflows) == 1
    wf = loaded.workflows[0]
    assert wf.workflow_id == "wf-s1"
    assert wf.sessions == ["s1", "s2"]
    assert wf.ai_name == "登录修复"
    assert wf.fork_branches == ["s3"]
    assert wf.is_active is True
    assert loaded.orphans == ["s4"]
    assert loaded.model == "claude-haiku-4.5"


def test_workflow_cache_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    from ccsm.core import meta as m
    importlib.reload(m)

    result = m.load_workflows("GUI", "nonexistent")
    assert result is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_meta.py::test_workflow_cache_round_trip -v`
Expected: FAIL — `AttributeError: module 'ccsm.core.meta' has no attribute 'save_workflows'`

- [ ] **Step 3: Implement save_workflows / load_workflows**

At the end of `ccsm/core/meta.py` (after `lock_title()`), add:

```python
# ─── Workflow Cache I/O ─────────────────────────────────────────────────────


def _workflows_dir() -> Path:
    d = get_ccsm_dir() / "workflows"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _workflow_path(project: str, worktree: str) -> Path:
    safe_project = re.sub(r"[^a-zA-Z0-9_-]", "_", project)
    safe_wt = re.sub(r"[^a-zA-Z0-9_-]", "_", worktree)
    return _workflows_dir() / f"{safe_project}_{safe_wt}.json"


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
    if data is None:
        return None

    workflows = []
    for wd in data.get("workflows", []):
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_meta.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/meta.py tests/test_meta.py
git commit -m "feat(ccsm): add workflow cluster cache I/O to meta module"
```
