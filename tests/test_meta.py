#!/usr/bin/env python3
"""Integration test for ccsm.core.meta — CRUD + edge cases."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

# ─── Patch HOME so tests don't touch the real ~/.ccsm/ ────────────────────────
_tmpdir = tempfile.mkdtemp(prefix="ccsm_test_")
_fake_home = Path(_tmpdir)

# We need to patch Path.home() BEFORE importing meta
with mock.patch.object(Path, "home", return_value=_fake_home):
    from ccsm.core.meta import (
        get_ccsm_dir,
        load_all_meta,
        load_meta,
        load_summary,
        save_meta,
        save_summary,
        update_meta,
    )
    from ccsm.models.session import Priority, SessionMeta, SessionSummary, Status

    passed = 0
    failed = 0

    def check(label: str, condition: bool, detail: str = ""):
        global passed, failed
        if condition:
            passed += 1
            print(f"  ✅ {label}")
        else:
            failed += 1
            print(f"  ❌ {label}  — {detail}")

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 1. get_ccsm_dir() ═══")
    ccsm = get_ccsm_dir()
    check("returns ~/.ccsm path", ccsm == _fake_home / ".ccsm")
    check("meta/ exists", (ccsm / "meta").is_dir())
    check("summaries/ exists", (ccsm / "summaries").is_dir())

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 2. load_meta — default for missing ═══")
    meta = load_meta("nonexistent-id")
    check("returns SessionMeta", isinstance(meta, SessionMeta))
    check("session_id matches", meta.session_id == "nonexistent-id")
    check("name is None", meta.name is None)
    check("tags is empty list", meta.tags == [])

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 3. save_meta + load_meta round-trip ═══")
    m1 = SessionMeta(
        session_id="test-001",
        name="我的测试会话",
        status_override=Status.ACTIVE,
        priority_override=Priority.FOCUS,
        tags=["design", "plugin"],
        pinned_messages=["msg-aaa"],
        notes="这是备注",
    )
    save_meta(m1)
    check("created_at auto-set", m1.created_at is not None)
    check("updated_at auto-set", m1.updated_at is not None)

    m1_loaded = load_meta("test-001")
    check("name round-trip", m1_loaded.name == "我的测试会话")
    check("status_override round-trip", m1_loaded.status_override == Status.ACTIVE)
    check("priority_override round-trip", m1_loaded.priority_override == Priority.FOCUS)
    check("tags round-trip", m1_loaded.tags == ["design", "plugin"])
    check("pinned_messages round-trip", m1_loaded.pinned_messages == ["msg-aaa"])
    check("notes round-trip", m1_loaded.notes == "这是备注")
    check(
        "created_at round-trip",
        m1_loaded.created_at is not None
        and abs((m1_loaded.created_at - m1.created_at).total_seconds()) < 1,
    )

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 4. save_meta updates updated_at ═══")
    old_updated = m1_loaded.updated_at
    time.sleep(0.05)
    save_meta(m1_loaded)
    m1_reloaded = load_meta("test-001")
    check(
        "updated_at bumped",
        m1_reloaded.updated_at is not None
        and old_updated is not None
        and m1_reloaded.updated_at >= old_updated,
    )

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 5. update_meta — direct fields ═══")
    m2 = update_meta(
        "test-002", name="Quick Session", priority_override="watch", notes="hi"
    )
    check("name set", m2.name == "Quick Session")
    check("priority_override set from string", m2.priority_override == Priority.WATCH)
    check("notes set", m2.notes == "hi")

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 6. update_meta — add/remove tags ═══")
    update_meta("test-002", add_tags=["a", "b", "c"])
    m2 = load_meta("test-002")
    check("tags after add", m2.tags == ["a", "b", "c"])

    update_meta("test-002", add_tags=["b", "d"])  # b already exists
    m2 = load_meta("test-002")
    check("tags deduplicated", m2.tags == ["a", "b", "c", "d"])

    update_meta("test-002", remove_tags=["a", "c"])
    m2 = load_meta("test-002")
    check("tags after remove", m2.tags == ["b", "d"])

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 7. update_meta — add/remove pinned ═══")
    update_meta("test-002", add_pinned=["p1", "p2"])
    m2 = load_meta("test-002")
    check("pinned after add", m2.pinned_messages == ["p1", "p2"])

    update_meta("test-002", add_pinned=["p2", "p3"])
    m2 = load_meta("test-002")
    check("pinned deduplicated", m2.pinned_messages == ["p1", "p2", "p3"])

    update_meta("test-002", remove_pinned=["p1"])
    m2 = load_meta("test-002")
    check("pinned after remove", m2.pinned_messages == ["p2", "p3"])

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 8. update_meta — full tags/pinned replace ═══")
    update_meta("test-002", tags=["x", "y"], pinned_messages=["z"])
    m2 = load_meta("test-002")
    check("tags full replace", m2.tags == ["x", "y"])
    check("pinned full replace", m2.pinned_messages == ["z"])

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 9. update_meta — status_override ═══")
    update_meta("test-002", status_override="background")
    m2 = load_meta("test-002")
    check("status_override from string", m2.status_override == Status.BACKGROUND)

    update_meta("test-002", status_override=None)
    m2 = load_meta("test-002")
    check("status_override cleared", m2.status_override is None)

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 10. SessionSummary round-trip ═══")
    s1 = SessionSummary(
        session_id="test-001",
        mode="llm",
        description="A great session",
        decision_trail=["chose React", "switched to Vue"],
        key_insights=["Vue is faster for prototyping"],
        tasks_completed=["setup project"],
        tasks_pending=["add tests"],
        code_changes=["src/app.vue created"],
        last_context="Was about to add routing",
        generated_at=datetime.now(timezone.utc),
        model="gpt-4o",
    )
    save_summary(s1)
    s1_loaded = load_summary("test-001")
    check("summary loaded", s1_loaded is not None)
    check("summary description", s1_loaded.description == "A great session")
    check("summary mode", s1_loaded.mode == "llm")
    check("summary model", s1_loaded.model == "gpt-4o")
    check(
        "decision_trail",
        s1_loaded.decision_trail == ["chose React", "switched to Vue"],
    )
    check("tasks_pending", s1_loaded.tasks_pending == ["add tests"])

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 11. load_summary — missing ═══")
    check("returns None for missing", load_summary("no-such-id") is None)

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 12. load_all_meta ═══")
    all_meta = load_all_meta()
    check("contains test-001", "test-001" in all_meta)
    check("contains test-002", "test-002" in all_meta)
    check("count == 2", len(all_meta) == 2)

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 13. Corrupt JSON recovery ═══")
    corrupt_path = ccsm / "meta" / "corrupt-id.meta.json"
    corrupt_path.write_text("{invalid json!!!", encoding="utf-8")
    m_corrupt = load_meta("corrupt-id")
    check("corrupt returns default", m_corrupt.session_id == "corrupt-id")
    check("corrupt name is None", m_corrupt.name is None)

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 14. Null/None fields ═══")
    m_null = SessionMeta(session_id="null-test")
    save_meta(m_null)
    m_null_loaded = load_meta("null-test")
    check("name None preserved", m_null_loaded.name is None)
    check("status_override None preserved", m_null_loaded.status_override is None)
    check("priority_override None preserved", m_null_loaded.priority_override is None)
    check("notes None preserved", m_null_loaded.notes is None)

    # ══════════════════════════════════════════════════════════════════════
    print("\n═══ 15. JSON file format verification ═══")
    raw = json.loads(
        (_fake_home / ".ccsm" / "meta" / "test-001.meta.json").read_text()
    )
    check("JSON has session_id", raw["session_id"] == "test-001")
    check("JSON status is string", isinstance(raw.get("status_override"), str))
    check("JSON priority is string", isinstance(raw.get("priority"), str))
    check("JSON created_at is ISO string", isinstance(raw.get("created_at"), str))

    # Verify JSON key is "priority" (not "priority_override") to match spec
    check("JSON key is 'priority'", "priority" in raw and "priority_override" not in raw)

    # ══════════════════════════════════════════════════════════════════════
    # Cleanup
    shutil.rmtree(_tmpdir, ignore_errors=True)

    # ══════════════════════════════════════════════════════════════════════
    print(f"\n{'═' * 50}")
    print(f"  Result: {passed} passed, {failed} failed")
    print(f"{'═' * 50}")
    if __name__ == "__main__":
        sys.exit(1 if failed else 0)


def test_meta_round_trip_new_fields(tmp_path, monkeypatch):
    """title_locked, last_message_at, last_accessed_at, lineage survive save/load."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import importlib
    from ccsm.core import meta as m
    importlib.reload(m)

    from datetime import datetime, timezone
    from ccsm.models.session import SessionMeta, LineageType, SessionLineage

    now = datetime(2026, 4, 2, 12, 0, 0, tzinfo=timezone.utc)
    lineage = SessionLineage(
        session_id="test-1",
        lineage_type=LineageType.FORK,
        parent_id="parent-1",
        fork_label="refactor",
        depth=1,
    )
    meta = SessionMeta(
        session_id="test-1",
        name="my-session",
        title_locked=True,
        last_message_at=now,
        last_accessed_at=now,
        lineage=lineage,
    )
    m.save_meta(meta)
    loaded = m.load_meta("test-1")

    assert loaded.title_locked is True
    assert loaded.last_message_at == now
    assert loaded.last_accessed_at == now
    assert loaded.lineage is not None
    assert loaded.lineage.lineage_type == LineageType.FORK
    assert loaded.lineage.parent_id == "parent-1"
    assert loaded.lineage.fork_label == "refactor"
    assert loaded.lineage.depth == 1


def test_meta_lock_title(tmp_path, monkeypatch):
    """lock_title() sets name and title_locked."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import importlib
    from ccsm.core import meta as m
    importlib.reload(m)

    m.lock_title("test-2", "my-locked-title")
    loaded = m.load_meta("test-2")
    assert loaded.name == "my-locked-title"
    assert loaded.title_locked is True


def test_workflow_cache_round_trip(tmp_path, monkeypatch):
    """WorkflowCluster survives save → load cycle."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    import importlib
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
    import importlib
    from ccsm.core import meta as m
    importlib.reload(m)

    result = m.load_workflows("GUI", "nonexistent")
    assert result is None
