"""AI-powered workflow naming and orphan session clustering.

Uses Haiku via local proxy (same pattern as summarizer.py) to:
1. Generate semantic names for workflow chains
2. Assign orphan sessions to existing workflows or new groups

This module only handles the AI interaction. Deterministic workflow
extraction is in workflow.py.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

from ccsm.models.session import Workflow, WorkflowCluster

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://127.0.0.1:4142"
DEFAULT_API_KEY = "sk-dummy"
DEFAULT_MODEL = "claude-haiku-4.5"

_SYSTEM_PROMPT = """\
You are a project analyst. Given a list of workflows (session chains) and orphan sessions, do two things:
1. Give each workflow a short semantic name (≤8 Chinese chars or ≤20 English chars)
2. Assign orphan sessions to an existing workflow if they belong, or leave unassigned

Output ONLY valid JSON:
{
  "workflows": [
    {"workflow_id": "wf-xxx", "ai_name": "semantic name"}
  ],
  "orphan_assignments": [
    {"session_id": "xxx", "assign_to": "wf-xxx"}
  ]
}

Rules:
- Names should be action-oriented: "登录修复", "API重构", "数据迁移"
- Only assign an orphan if it clearly belongs to a workflow's topic
- Use the same language as the session content
- Output ONLY JSON, no markdown, no explanation"""


def _build_naming_prompt(
    cluster: WorkflowCluster,
    intents: dict[str, str],
) -> str:
    """Build the user prompt for workflow naming."""
    lines: list[str] = []
    lines.append(f"Project: {cluster.project}, Worktree: {cluster.worktree}")
    lines.append("")

    for wf in cluster.workflows:
        lines.append(f"Workflow {wf.workflow_id}:")
        lines.append(f"  Chain: {wf.name or '(unnamed)'}")
        lines.append(f"  Sessions ({len(wf.sessions)}):")
        for sid in wf.sessions[:5]:
            intent = intents.get(sid, "")
            lines.append(f"    - {sid[:8]}: {intent[:100]}")
        if wf.fork_branches:
            # fork_branches is list[list[str]], flatten for display
            fork_ids = [s[:8] for branch in wf.fork_branches for s in branch]
            lines.append(f"  Forks: {', '.join(fork_ids)}")
        lines.append("")

    if cluster.orphans:
        lines.append("Orphan sessions (not in any workflow):")
        for sid in cluster.orphans[:10]:
            intent = intents.get(sid, "")
            lines.append(f"  - {sid[:8]}: {intent[:100]}")

    return "\n".join(lines)


def _parse_naming_response(
    data: dict,
) -> tuple[dict[str, str], dict[str, str]]:
    """Parse AI response into naming dict and orphan assignments.

    Returns:
        (workflow_names, orphan_assignments) where:
        - workflow_names: {workflow_id: ai_name}
        - orphan_assignments: {session_id: workflow_id}
    """
    names: dict[str, str] = {}
    assignments: dict[str, str] = {}

    for entry in data.get("workflows", []):
        wid = entry.get("workflow_id")
        name = entry.get("ai_name")
        if wid and name:
            names[wid] = name

    for entry in data.get("orphan_assignments", []):
        sid = entry.get("session_id")
        target = entry.get("assign_to")
        if sid and target:
            assignments[sid] = target

    return names, assignments


async def name_workflows(
    cluster: WorkflowCluster,
    intents: dict[str, str],
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> WorkflowCluster:
    """Call AI to name workflows and assign orphans.

    Modifies the cluster in-place (sets ai_name on workflows,
    moves orphans into workflows). Returns the same cluster.

    If the API call fails, returns the cluster unchanged (best-effort).
    """
    if not cluster.workflows and not cluster.orphans:
        return cluster

    prompt = _build_naming_prompt(cluster, intents)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": _SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    "max_tokens": 1024,
                    "temperature": 0.2,
                },
            )
            resp.raise_for_status()
            raw_text = resp.json()["choices"][0]["message"]["content"].strip()

            # Strip markdown fences
            if raw_text.startswith("```"):
                raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
            if raw_text.endswith("```"):
                raw_text = raw_text[:-3].strip()

            data = json.loads(raw_text)

    except Exception as e:
        logger.warning("AI workflow naming failed: %s", e)
        return cluster

    names, assignments = _parse_naming_response(data)

    # Apply names
    wf_by_id = {wf.workflow_id: wf for wf in cluster.workflows}
    for wid, name in names.items():
        if wid in wf_by_id:
            wf_by_id[wid].ai_name = name

    # Apply orphan assignments (append but note: timestamps may become stale)
    remaining_orphans: list[str] = []
    reassigned_wfs: set[str] = set()
    for sid in cluster.orphans:
        target_wid = assignments.get(sid)
        if target_wid and target_wid in wf_by_id:
            wf_by_id[target_wid].sessions.append(sid)
            reassigned_wfs.add(target_wid)
        else:
            remaining_orphans.append(sid)
    cluster.orphans = remaining_orphans

    # Recompute session_count is automatic (property). Mark reassigned
    # workflows as needing timestamp refresh on next extract_workflows() call.
    # For now, clear stale timestamps so they don't mislead the TUI.
    for wid in reassigned_wfs:
        wf = wf_by_id[wid]
        wf.first_timestamp = None
        wf.last_timestamp = None

    # Update metadata
    from datetime import datetime, timezone
    cluster.generated_at = datetime.now(timezone.utc)
    cluster.model = model

    return cluster


def name_workflows_sync(
    cluster: WorkflowCluster,
    intents: dict[str, str],
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> WorkflowCluster:
    """Synchronous wrapper for name_workflows."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                name_workflows(cluster, intents, base_url, api_key, model),
            )
            return future.result(timeout=25)
    except RuntimeError:
        return asyncio.run(
            name_workflows(cluster, intents, base_url, api_key, model)
        )
