"""Tests for AI workflow clustering (mocked API)."""
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from ccsm.core.cluster import (
    _build_naming_prompt,
    _parse_naming_response,
    name_workflows,
)
from ccsm.models.session import Workflow, WorkflowCluster


def _wf(wid: str, sessions: list[str], name: str) -> Workflow:
    return Workflow(
        workflow_id=wid,
        sessions=sessions,
        name=name,
        first_timestamp=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
        last_timestamp=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
    )


def test_build_naming_prompt():
    cluster = WorkflowCluster(
        worktree="memory",
        project="GUI",
        workflows=[
            _wf("wf-1", ["s1", "s2"], "fix-bug → fix-v2"),
            _wf("wf-2", ["s3"], "brainstorm-api"),
        ],
        orphans=["s4"],
    )
    intents = {
        "s1": "修复登录页面的CSS问题",
        "s2": "继续修复登录验证逻辑",
        "s3": "讨论API重构方案",
        "s4": "快速查看日志格式",
    }
    prompt = _build_naming_prompt(cluster, intents)
    assert "fix-bug → fix-v2" in prompt
    assert "修复登录页面" in prompt
    assert "brainstorm-api" in prompt
    assert "orphan" in prompt.lower() or "s4" in prompt


def test_parse_naming_response():
    raw = {
        "workflows": [
            {"workflow_id": "wf-1", "ai_name": "登录修复"},
            {"workflow_id": "wf-2", "ai_name": "API重构"},
        ],
        "orphan_assignments": [
            {"session_id": "s4", "assign_to": "wf-1"},
        ],
    }
    names, assignments = _parse_naming_response(raw)
    assert names["wf-1"] == "登录修复"
    assert names["wf-2"] == "API重构"
    assert assignments["s4"] == "wf-1"


def test_parse_naming_response_empty():
    names, assignments = _parse_naming_response({})
    assert names == {}
    assert assignments == {}


def test_parse_naming_response_partial():
    raw = {
        "workflows": [
            {"workflow_id": "wf-1", "ai_name": "修复"},
        ],
    }
    names, assignments = _parse_naming_response(raw)
    assert names == {"wf-1": "修复"}
    assert assignments == {}
