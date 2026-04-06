"""Tests for background task state parsing from JSONL."""


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
