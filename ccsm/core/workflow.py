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
        forks: list[list[str]] = []

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
                    # Traverse the fork into its own sub-chain
                    fork_chain = _walk_fork_branch(child_id)
                    if fork_chain:
                        forks.append(fork_chain)
                else:
                    # DUPLICATE or COMPACT → same chain
                    _walk_chain(child_id)

        def _walk_fork_branch(sid: str) -> list[str]:
            """Walk a fork root and its COMPACT/DUPLICATE descendants."""
            branch: list[str] = []
            stack = [sid]
            while stack:
                current = stack.pop(0)
                if current in claimed:
                    continue
                claimed.add(current)
                branch.append(current)
                node = graph.get(current)
                if not node:
                    continue
                # Sort children for deterministic order
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
                        # Nested fork → its own branch
                        nested = _walk_fork_branch(child_id)
                        if nested:
                            forks.append(nested)
                    else:
                        stack.append(child_id)
            return branch

        _walk_chain(root_sid)

        if not chain:
            continue

        # ── Build auto-name from chain titles ──────────────────────
        chain_titles = [titles.get(sid, sid[:8]) for sid in chain]
        auto_name = " → ".join(chain_titles) if chain_titles else None

        # ── Compute timestamps ──────────────────────────────────────
        all_sids = list(chain)
        for branch in forks:
            all_sids.extend(branch)
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
