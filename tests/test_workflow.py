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
