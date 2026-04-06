"""Tests for background task state parsing from JSONL."""

import json
import tempfile
from pathlib import Path


def _write_jsonl(lines: list[dict]) -> Path:
    """Helper: write dicts as JSONL to a temp file."""
    f = tempfile.NamedTemporaryFile(suffix=".jsonl", delete=False, mode="w")
    for line in lines:
        f.write(json.dumps(line) + "\n")
    f.close()
    return Path(f.name)


def test_background_task_info_creation():
    """BackgroundTaskInfo should hold task metadata."""
    from ccsm.models.session import BackgroundTaskInfo

    task = BackgroundTaskInfo(
        task_id="1",
        subject="Fix auth bug",
        status="completed",
        tool_name="TaskCreate",
    )
    assert task.task_id == "1"
    assert task.subject == "Fix auth bug"
    assert task.status == "completed"
    assert task.description is None  # Optional field defaults to None


def test_background_task_info_from_agent():
    """BackgroundTaskInfo should support Agent-spawned tasks."""
    from ccsm.models.session import BackgroundTaskInfo

    task = BackgroundTaskInfo(
        task_id="agent-a1b",
        subject="Research auth module",
        status="running",
        tool_name="Agent",
        description="Investigate the auth bug in login flow",
    )
    assert task.tool_name == "Agent"
    assert task.description is not None


def test_parse_session_detail_extracts_task_create():
    """parse_session_detail should extract TaskCreate tool_use blocks."""
    from ccsm.core.parser import parse_session_detail

    path = _write_jsonl([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "TaskCreate", "input": {
                "subject": "Fix auth module",
                "description": "Investigate and fix the auth bug",
            }},
        ]}},
        {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": json.dumps({
                "task": {"id": "1", "subject": "Fix auth module"},
            })},
        ]}},
    ])

    result = parse_session_detail(path)
    assert len(result.background_tasks) >= 1
    task = result.background_tasks[0]
    assert task.subject == "Fix auth module"
    assert task.tool_name == "TaskCreate"
    path.unlink()


def test_parse_session_detail_extracts_agent_tool():
    """parse_session_detail should extract Agent tool_use as background tasks."""
    from ccsm.core.parser import parse_session_detail

    path = _write_jsonl([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Agent", "input": {
                "description": "Research auth patterns",
                "prompt": "Look into how auth is handled...",
            }},
        ]}},
    ])

    result = parse_session_detail(path)
    assert len(result.background_tasks) >= 1
    task = result.background_tasks[0]
    assert "Research auth" in task.subject
    assert task.tool_name == "Agent"
    path.unlink()


def test_parse_session_detail_no_duplicate_agents():
    """Agents should appear in both agents_spawned and background_tasks."""
    from ccsm.core.parser import parse_session_detail

    path = _write_jsonl([
        {"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Agent", "input": {
                "description": "Check tests",
            }},
        ]}},
    ])

    result = parse_session_detail(path)
    assert len(result.agents_spawned) >= 1  # Existing behavior preserved
    assert len(result.background_tasks) >= 1  # New behavior added
    path.unlink()
