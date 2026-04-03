# CCSM Resume Painpoints Fix — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix 8 Claude Code resume pain points by adding lineage detection, stable title management, full-text search index, timestamp semantics fix, duplicate detection, and session DAG visualization to CCSM.

**Architecture:** Extend the existing CCSM pipeline with three new modules (`core/lineage.py`, `core/index.py`, `tui/widgets/session_graph.py`) and targeted modifications to `models/session.py`, `core/meta.py`, `core/parser.py`, `core/discovery.py`, and `tui/screens/main.py`. All new data persists in `~/.ccsm/` sidecar files — never modifies Claude Code's `~/.claude/` data.

**Tech Stack:** Python 3.10+, dataclasses, JSON, Textual TUI framework, Rich markup

**Pain Point → Task Map:**

| Pain Point | Root Cause | Tasks |
|---|---|---|
| #1 Fork sessions indistinguishable | No fork lineage stored | 1, 2, 3 |
| #2 Renamed title reverts | 64KB tail window + crash = lost title | 1, 4, 7 |
| #3 Worktree resume list unstable | cwd-dependent project dir resolution | 1, 5, 7 |
| #4 Search useless, count inconsistent | No index, head/tail scanning only | 5, 7 |
| #5 Compact spawns phantom session | compact_boundary + session ID change | 1, 2, 3 |
| #6 Resume changes timeline | Sort by file mtime, not message time | 1, 3, 4, 7 |
| #7 Multi-SSH creates duplicates | Each process = new session ID | 1, 2, 6 |
| #8 No tree/DAG visualization | No cross-session relationships | 1, 2, 8 |

---

## File Structure

### New Files
| File | Responsibility |
|---|---|
| `ccsm/core/lineage.py` | Detect fork/compact/duplicate relationships from JSONL signals |
| `ccsm/core/index.py` | Persistent session index with full-text fuzzy search |
| `ccsm/core/workflow.py` | Build compact-chain workflows from lineage DAG |
| `ccsm/core/cluster.py` | AI-powered workflow naming + orphan session clustering |
| `ccsm/tui/widgets/workflow_list.py` | Folded workflow list in detail panel (default view) |
| `ccsm/tui/widgets/swimlane.py` | Full-screen swimlane timeline (expanded view, `g` key) |
| `tests/test_lineage.py` | Lineage detection tests |
| `tests/test_index.py` | Index + search tests |
| `tests/test_parser_enhanced.py` | Enhanced parser tests |
| `tests/test_workflow.py` | Workflow chain extraction tests |
| `tests/test_cluster.py` | AI clustering tests (mocked API) |

### Modified Files
| File | Changes |
|---|---|
| `ccsm/models/session.py` | Add `LineageType`, `SessionLineage`, `Workflow`, `WorkflowCluster` dataclasses, extend `SessionMeta` |
| `ccsm/core/parser.py` | Add `parse_session_timestamps()` for fork/compact detection |
| `ccsm/core/meta.py` | Serialize new fields, add `lock_title()`, add workflow cache I/O |
| `ccsm/core/discovery.py` | Add `detect_duplicates()` |
| `ccsm/tui/screens/main.py` | Integrate index, lineage, timestamp sorting, workflow views, async AI clustering |
| `ccsm/tui/widgets/session_list.py` | Use index-backed search, show lineage badges |
| `ccsm/tui/widgets/session_detail.py` | Embed WorkflowList section, toggle to swimlane |

---

### Task 1: Data Model Extensions

**Files:**
- Modify: `ccsm/models/session.py` (add enums, dataclasses, extend `SessionMeta`)
- Test: `tests/test_models.py`

- [ ] **Step 1: Write test for LineageType and SessionLineage**

```python
# tests/test_models.py
"""Tests for extended session models."""
from ccsm.models.session import (
    LineageType, SessionLineage, SessionMeta,
)


def test_lineage_type_values():
    assert LineageType.FORK.value == "fork"
    assert LineageType.COMPACT.value == "compact"
    assert LineageType.DUPLICATE.value == "duplicate"
    assert LineageType.ROOT.value == "root"


def test_session_lineage_defaults():
    lin = SessionLineage(session_id="abc-123")
    assert lin.session_id == "abc-123"
    assert lin.lineage_type == LineageType.ROOT
    assert lin.parent_id is None
    assert lin.children == []
    assert lin.compact_predecessor is None
    assert lin.fork_source is None
    assert lin.fork_label is None
    assert lin.depth == 0


def test_session_lineage_fork():
    lin = SessionLineage(
        session_id="child-1",
        lineage_type=LineageType.FORK,
        parent_id="parent-1",
        fork_source="parent-1",
        fork_label="refactor-branch",
        depth=1,
    )
    assert lin.lineage_type == LineageType.FORK
    assert lin.parent_id == "parent-1"
    assert lin.fork_label == "refactor-branch"


def test_session_meta_new_fields():
    meta = SessionMeta(session_id="abc-123")
    assert meta.title_locked is False
    assert meta.last_message_at is None
    assert meta.last_accessed_at is None
    assert meta.lineage is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'LineageType'`

- [ ] **Step 3: Add LineageType enum and SessionLineage dataclass**

In `ccsm/models/session.py`, after the `Priority` enum (after line 49), add:

```python
class LineageType(str, Enum):
    """How this session relates to its parent."""
    ROOT = "root"           # No parent — standalone session
    FORK = "fork"           # Branched from another session
    COMPACT = "compact"     # Continuation after compaction
    DUPLICATE = "duplicate" # Same work from concurrent SSH
```

After `Breakpoint` (after line 229), add:

```python
@dataclass
class SessionLineage:
    """Cross-session relationship for DAG construction."""
    session_id: str
    lineage_type: LineageType = LineageType.ROOT
    parent_id: Optional[str] = None
    children: list[str] = field(default_factory=list)
    compact_predecessor: Optional[str] = None
    fork_source: Optional[str] = None
    fork_label: Optional[str] = None
    depth: int = 0  # Distance from root in the DAG
```

- [ ] **Step 4: Extend SessionMeta with new fields**

In `ccsm/models/session.py`, add fields to `SessionMeta` (after the `ai_intent` field, line 137):

```python
    title_locked: bool = False
    last_message_at: Optional[datetime] = None
    last_accessed_at: Optional[datetime] = None
    lineage: Optional["SessionLineage"] = None
```

Update the imports at the top to include the new types in `__all__` or ensure they are importable.

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_models.py -v`
Expected: PASS — all 4 tests pass

- [ ] **Step 6: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/models/session.py tests/test_models.py
git commit -m "feat(ccsm): add LineageType, SessionLineage, extend SessionMeta with title_locked and timestamps"
```

---

### Task 2: Lineage Signal Detection from JSONL

**Files:**
- Create: `ccsm/core/lineage.py`
- Test: `tests/test_lineage.py`

This module parses JSONL files to detect three relationship types:
- **Fork**: First user message content matches another session's compact summary, or session name ends with "(branch)"
- **Compact**: JSONL contains `compact_boundary` entries, link to predecessor by matching pre-boundary content
- **Duplicate**: Multiple sessions with same cwd + overlapping timestamp ranges + similar first messages

- [ ] **Step 1: Write tests for lineage signal parsing**

```python
# tests/test_lineage.py
"""Tests for lineage detection from JSONL signals."""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ccsm.core.lineage import parse_lineage_signals, LineageSignals


def _write_jsonl(lines: list[dict]) -> Path:
    """Write a list of dicts as JSONL to a temp file."""
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    )
    for line in lines:
        f.write(json.dumps(line) + "\n")
    f.close()
    return Path(f.name)


def test_detect_fork_from_branch_name():
    """Session with (branch) in display_name is a fork."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "sessionId": "child-1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
    ])
    signals = parse_lineage_signals(path, display_name="fix-bug (branch)")
    assert signals.is_fork is True
    assert signals.fork_hint == "display_name_branch_suffix"


def test_detect_compact_boundary():
    """Session with compact_boundary has compaction signal."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "start"},
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T11:00:00Z",
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "continue"},
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T11:05:00Z",
        },
    ])
    signals = parse_lineage_signals(path)
    assert signals.has_compact_boundary is True
    assert signals.compact_count == 1


def test_detect_no_signals():
    """Normal session has no lineage signals."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "write a sort function"},
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "Here is the sort..."},
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T10:00:05Z",
        },
    ])
    signals = parse_lineage_signals(path)
    assert signals.is_fork is False
    assert signals.has_compact_boundary is False
    assert signals.fork_hint is None


def test_extract_last_message_timestamp():
    """last_message_at is the timestamp of the last user/assistant message."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "hi"},
            "sessionId": "sess-1",
            "timestamp": "2026-04-01T10:05:00Z",
        },
        # system message after — should NOT be the last_message_at
        {
            "type": "custom-title",
            "customTitle": "test",
            "sessionId": "sess-1",
        },
    ])
    signals = parse_lineage_signals(path)
    assert signals.last_message_at == datetime(
        2026, 4, 1, 10, 5, 0, tzinfo=timezone.utc
    )


def test_detect_fork_from_compact_summary_first_message():
    """If first user message is a compact summary, it's a fork/continuation."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {
                "role": "user",
                "content": "Here is a summary of the conversation so far:",
            },
            "sessionId": "child-1",
            "timestamp": "2026-04-01T12:00:00Z",
            "isSidechain": False,
        },
    ])
    signals = parse_lineage_signals(path)
    assert signals.is_fork is True
    assert signals.fork_hint == "compact_summary_first_message"


def test_extract_session_cwd_and_branch():
    """Extracts cwd and git_branch for duplicate detection."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "hi"},
            "sessionId": "sess-1",
            "cwd": "/home/user/project",
            "gitBranch": "main",
            "timestamp": "2026-04-01T10:00:00Z",
        },
    ])
    signals = parse_lineage_signals(path)
    assert signals.cwd == "/home/user/project"
    assert signals.git_branch == "main"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_lineage.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccsm.core.lineage'`

- [ ] **Step 3: Implement lineage signal detection**

```python
# ccsm/core/lineage.py
"""Detect cross-session lineage signals from JSONL files.

Scans JSONL files for three relationship types:
- Fork: session branched from another (detected by name suffix or compact summary as first message)
- Compact: session contains compact_boundary markers (continuation within same file)
- Duplicate: will be resolved by cross-session comparison in build_lineage_graph()
"""
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ccsm.models.session import LineageType, SessionLineage

log = logging.getLogger(__name__)

_COMPACT_SUMMARY_PREFIXES = (
    "Here is a summary of the conversation",
    "Here's a summary of the conversation",
    "Summary of conversation so far",
    "Conversation summary:",
)


@dataclass
class LineageSignals:
    """Raw signals extracted from a single JSONL file."""
    session_id: Optional[str] = None
    is_fork: bool = False
    fork_hint: Optional[str] = None
    has_compact_boundary: bool = False
    compact_count: int = 0
    last_message_at: Optional[datetime] = None
    first_message_at: Optional[datetime] = None
    first_user_content: Optional[str] = None
    cwd: Optional[str] = None
    git_branch: Optional[str] = None


def parse_lineage_signals(
    jsonl_path: Path,
    display_name: Optional[str] = None,
) -> LineageSignals:
    """Extract lineage signals from a JSONL session file.

    This is a fast scan — reads all lines but only extracts lightweight
    signal fields, not full message content.
    """
    signals = LineageSignals()

    # Check display_name for fork hint
    if display_name and display_name.rstrip().endswith("(branch)"):
        signals.is_fork = True
        signals.fork_hint = "display_name_branch_suffix"

    try:
        text = jsonl_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log.debug("Cannot read %s", jsonl_path)
        return signals

    first_user_seen = False
    last_msg_ts: Optional[datetime] = None
    first_msg_ts: Optional[datetime] = None

    for raw_line in text.splitlines():
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        entry_type = data.get("type", "")

        # Extract session ID
        if not signals.session_id:
            sid = data.get("sessionId")
            if sid:
                signals.session_id = sid

        # Extract cwd and git_branch (take last seen)
        if "cwd" in data:
            signals.cwd = data["cwd"]
        if "gitBranch" in data:
            signals.git_branch = data["gitBranch"]

        # Detect compact_boundary
        if entry_type == "system":
            subtype = data.get("subtype", "")
            if subtype == "compact_boundary":
                signals.has_compact_boundary = True
                signals.compact_count += 1
            continue

        # Track message timestamps (user and assistant only)
        if entry_type in ("user", "assistant"):
            ts = _parse_ts(data.get("timestamp"))
            if ts:
                if first_msg_ts is None or ts < first_msg_ts:
                    first_msg_ts = ts
                if last_msg_ts is None or ts > last_msg_ts:
                    last_msg_ts = ts

        # Check first user message for compact summary (fork signal)
        if entry_type == "user" and not first_user_seen:
            first_user_seen = True
            msg = data.get("message", {})
            content = msg.get("content", "") if isinstance(msg, dict) else ""
            if isinstance(content, list):
                # Extract text blocks
                content = " ".join(
                    b.get("text", "") for b in content
                    if isinstance(b, dict) and b.get("type") == "text"
                )
            signals.first_user_content = content[:200] if content else None
            if content and not signals.is_fork:
                for prefix in _COMPACT_SUMMARY_PREFIXES:
                    if content.strip().startswith(prefix):
                        signals.is_fork = True
                        signals.fork_hint = "compact_summary_first_message"
                        break

    signals.last_message_at = last_msg_ts
    signals.first_message_at = first_msg_ts
    return signals


def build_lineage_graph(
    signals_map: dict[str, LineageSignals],
) -> dict[str, SessionLineage]:
    """Build lineage DAG from per-session signals.

    Phase 1: Mark forks and compact sessions based on individual signals.
    Phase 2: Detect duplicates by comparing (cwd, branch, time overlap).
    Phase 3: Assign parent-child and depth.

    Returns dict mapping session_id -> SessionLineage.
    """
    lineages: dict[str, SessionLineage] = {}

    # Phase 1: Create lineage nodes from individual signals
    for sid, sig in signals_map.items():
        lt = LineageType.ROOT
        if sig.is_fork:
            lt = LineageType.FORK
        lineages[sid] = SessionLineage(
            session_id=sid,
            lineage_type=lt,
        )

    # Phase 2: Detect duplicates — same cwd+branch, overlapping time, different session
    by_cwd_branch: dict[tuple, list[str]] = {}
    for sid, sig in signals_map.items():
        if sig.cwd and sig.git_branch:
            key = (sig.cwd, sig.git_branch)
            by_cwd_branch.setdefault(key, []).append(sid)

    for key, sids in by_cwd_branch.items():
        if len(sids) < 2:
            continue
        # Sort by first_message_at
        sorted_sids = sorted(
            sids,
            key=lambda s: signals_map[s].first_message_at or datetime.min.replace(
                tzinfo=timezone.utc
            ),
        )
        for i in range(1, len(sorted_sids)):
            curr = sorted_sids[i]
            prev = sorted_sids[i - 1]
            sig_curr = signals_map[curr]
            sig_prev = signals_map[prev]
            if sig_curr.first_message_at and sig_prev.last_message_at:
                gap = (
                    sig_curr.first_message_at - sig_prev.last_message_at
                ).total_seconds()
                # If sessions overlap or gap < 5 minutes, likely duplicate/continuation
                if gap < 300:
                    # Only mark as duplicate if not already a fork
                    if lineages[curr].lineage_type == LineageType.ROOT:
                        lineages[curr].lineage_type = LineageType.DUPLICATE
                        lineages[curr].parent_id = prev
                    lineages[prev].children.append(curr)

    # Phase 3: Assign depth
    def _assign_depth(sid: str, depth: int) -> None:
        lineages[sid].depth = depth
        for child in lineages[sid].children:
            if child in lineages:
                _assign_depth(child, depth + 1)

    for sid, lin in lineages.items():
        if lin.parent_id is None and lin.lineage_type == LineageType.ROOT:
            _assign_depth(sid, 0)

    return lineages


def _parse_ts(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO-8601 timestamp string."""
    if not ts_str:
        return None
    try:
        s = ts_str.replace("Z", "+00:00")
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_lineage.py -v`
Expected: PASS — all 6 tests pass

- [ ] **Step 5: Write tests for build_lineage_graph**

```python
# Append to tests/test_lineage.py

from ccsm.core.lineage import build_lineage_graph
from ccsm.models.session import LineageType


def test_build_graph_detects_duplicate():
    """Two sessions with same cwd+branch and overlapping time are duplicates."""
    signals = {
        "sess-a": LineageSignals(
            session_id="sess-a",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
        "sess-b": LineageSignals(
            session_id="sess-b",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 28, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
        ),
    }
    graph = build_lineage_graph(signals)
    assert graph["sess-b"].lineage_type == LineageType.DUPLICATE
    assert graph["sess-b"].parent_id == "sess-a"
    assert "sess-b" in graph["sess-a"].children


def test_build_graph_no_false_duplicate():
    """Two sessions with same cwd but 1-hour gap are independent."""
    signals = {
        "sess-a": LineageSignals(
            session_id="sess-a",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
        "sess-b": LineageSignals(
            session_id="sess-b",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 11, 30, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
        ),
    }
    graph = build_lineage_graph(signals)
    assert graph["sess-a"].lineage_type == LineageType.ROOT
    assert graph["sess-b"].lineage_type == LineageType.ROOT


def test_build_graph_fork_preserved():
    """Fork signal from parse_lineage_signals is preserved in graph."""
    signals = {
        "parent-1": LineageSignals(session_id="parent-1"),
        "child-1": LineageSignals(
            session_id="child-1",
            is_fork=True,
            fork_hint="display_name_branch_suffix",
        ),
    }
    graph = build_lineage_graph(signals)
    assert graph["child-1"].lineage_type == LineageType.FORK
    assert graph["parent-1"].lineage_type == LineageType.ROOT
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_lineage.py -v`
Expected: PASS — all 9 tests pass

- [ ] **Step 7: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/lineage.py tests/test_lineage.py
git commit -m "feat(ccsm): add lineage signal detection — fork, compact, duplicate"
```

---

### Task 3: Enhanced Parser — last_message_at and Compact Detection

**Files:**
- Modify: `ccsm/core/parser.py` (add `parse_timestamps()` function)
- Test: `tests/test_parser_enhanced.py`

This extracts `last_message_at` (last actual user/assistant timestamp, not file mtime) which fixes pain point #6.

- [ ] **Step 1: Write tests for timestamp extraction**

```python
# tests/test_parser_enhanced.py
"""Tests for enhanced parser: timestamp and compact detection."""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ccsm.core.parser import parse_session_timestamps


def _write_jsonl(lines: list[dict]) -> Path:
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False
    )
    for line in lines:
        f.write(json.dumps(line) + "\n")
    f.close()
    return Path(f.name)


def test_parse_timestamps_basic():
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "hi"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T10:05:00Z",
        },
    ])
    ts = parse_session_timestamps(path)
    assert ts.last_message_at == datetime(
        2026, 4, 1, 10, 5, 0, tzinfo=timezone.utc
    )
    assert ts.first_message_at == datetime(
        2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc
    )
    assert ts.compact_count == 0


def test_parse_timestamps_ignores_metadata_lines():
    """Metadata entries (custom-title, etc.) should not affect last_message_at."""
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "hello"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "custom-title",
            "customTitle": "test",
            "sessionId": "s1",
        },
        {
            "type": "last-prompt",
            "lastPrompt": "bye",
            "sessionId": "s1",
        },
    ])
    ts = parse_session_timestamps(path)
    assert ts.last_message_at == datetime(
        2026, 4, 1, 10, 0, 0, tzinfo=timezone.utc
    )


def test_parse_timestamps_with_compact():
    path = _write_jsonl([
        {
            "type": "user",
            "message": {"role": "user", "content": "start"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "system",
            "subtype": "compact_boundary",
            "sessionId": "s1",
            "timestamp": "2026-04-01T11:00:00Z",
        },
        {
            "type": "user",
            "message": {"role": "user", "content": "after compact"},
            "sessionId": "s1",
            "timestamp": "2026-04-01T11:05:00Z",
        },
    ])
    ts = parse_session_timestamps(path)
    assert ts.compact_count == 1
    assert ts.last_message_at == datetime(
        2026, 4, 1, 11, 5, 0, tzinfo=timezone.utc
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_parser_enhanced.py -v`
Expected: FAIL — `ImportError: cannot import name 'parse_session_timestamps'`

- [ ] **Step 3: Implement parse_session_timestamps**

At the end of `ccsm/core/parser.py`, add:

```python
@dataclass
class SessionTimestamps:
    """Lightweight timestamp extraction result."""
    first_message_at: Optional[datetime] = None
    last_message_at: Optional[datetime] = None
    compact_count: int = 0


def parse_session_timestamps(jsonl_path: Path) -> SessionTimestamps:
    """Fast extraction of message timestamps and compact boundaries.

    Reads only timestamp and type fields — skips message content parsing.
    Used to get last_message_at for correct timeline ordering (pain point #6).
    """
    result = SessionTimestamps()
    lines = _read_lines(jsonl_path)

    for raw_line in lines:
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            data = json.loads(raw_line)
        except json.JSONDecodeError:
            continue

        entry_type = data.get("type", "")

        # Compact boundary detection
        if entry_type == "system" and data.get("subtype") == "compact_boundary":
            result.compact_count += 1
            continue

        # Only user/assistant messages contribute to timestamp
        if entry_type not in ("user", "assistant"):
            continue

        ts = _parse_timestamp(data.get("timestamp"))
        if ts is None:
            continue

        if result.first_message_at is None or ts < result.first_message_at:
            result.first_message_at = ts
        if result.last_message_at is None or ts > result.last_message_at:
            result.last_message_at = ts

    return result
```

Also add the `dataclass` import to the existing imports at the top of `parser.py`:

```python
from dataclasses import dataclass
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_parser_enhanced.py -v`
Expected: PASS — all 3 tests pass

- [ ] **Step 5: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/parser.py tests/test_parser_enhanced.py
git commit -m "feat(ccsm): add parse_session_timestamps for correct timeline ordering"
```

---

### Task 4: Meta Serialization for New Fields

**Files:**
- Modify: `ccsm/core/meta.py` (extend `_meta_to_dict`, `_dict_to_meta`)
- Modify: `tests/test_meta.py` (add round-trip tests)

- [ ] **Step 1: Write round-trip tests for new SessionMeta fields**

Append to `tests/test_meta.py`:

```python
def test_meta_round_trip_new_fields(tmp_path, monkeypatch):
    """title_locked, last_message_at, last_accessed_at survive save/load."""
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
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
    from ccsm.core import meta as m
    importlib.reload(m)

    m.lock_title("test-2", "my-locked-title")
    loaded = m.load_meta("test-2")
    assert loaded.name == "my-locked-title"
    assert loaded.title_locked is True
```

Add `import importlib` to the top of `tests/test_meta.py` if not present.

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_meta.py::test_meta_round_trip_new_fields tests/test_meta.py::test_meta_lock_title -v`
Expected: FAIL — new fields not serialized, `lock_title` not found

- [ ] **Step 3: Update _meta_to_dict to serialize new fields**

In `ccsm/core/meta.py`, modify `_meta_to_dict()` (around line 156-168). Add after the existing fields:

```python
    d["title_locked"] = meta.title_locked
    d["last_message_at"] = _dt_to_iso(meta.last_message_at)
    d["last_accessed_at"] = _dt_to_iso(meta.last_accessed_at)
    if meta.lineage is not None:
        d["lineage"] = {
            "session_id": meta.lineage.session_id,
            "lineage_type": meta.lineage.lineage_type.value,
            "parent_id": meta.lineage.parent_id,
            "children": meta.lineage.children,
            "compact_predecessor": meta.lineage.compact_predecessor,
            "fork_source": meta.lineage.fork_source,
            "fork_label": meta.lineage.fork_label,
            "depth": meta.lineage.depth,
        }
    else:
        d["lineage"] = None
```

- [ ] **Step 4: Update _dict_to_meta to deserialize new fields**

In `ccsm/core/meta.py`, modify `_dict_to_meta()` (around line 171-183). Add after the existing fields:

```python
    meta.title_locked = d.get("title_locked", False)
    meta.last_message_at = _iso_to_dt(d.get("last_message_at"))
    meta.last_accessed_at = _iso_to_dt(d.get("last_accessed_at"))
    lineage_data = d.get("lineage")
    if lineage_data and isinstance(lineage_data, dict):
        meta.lineage = SessionLineage(
            session_id=lineage_data.get("session_id", meta.session_id),
            lineage_type=LineageType(lineage_data.get("lineage_type", "root")),
            parent_id=lineage_data.get("parent_id"),
            children=lineage_data.get("children", []),
            compact_predecessor=lineage_data.get("compact_predecessor"),
            fork_source=lineage_data.get("fork_source"),
            fork_label=lineage_data.get("fork_label"),
            depth=lineage_data.get("depth", 0),
        )
```

Add imports at the top of `meta.py`:

```python
from ccsm.models.session import (
    Breakpoint, LineageType, Milestone, MilestoneItem, MilestoneStatus,
    Priority, SessionLineage, SessionMeta, SessionSummary, Status,
)
```

- [ ] **Step 5: Add lock_title() function**

At the end of `ccsm/core/meta.py`, after `load_all_meta()`:

```python
def lock_title(session_id: str, title: str) -> SessionMeta:
    """Set a permanent title that won't be overwritten by AI or Claude Code.

    Fixes pain point #2: renamed titles reverting to last/first prompt.
    """
    meta = load_meta(session_id)
    meta.name = title
    meta.title_locked = True
    save_meta(meta)
    return meta
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_meta.py -v`
Expected: PASS — all tests pass (including existing ones)

- [ ] **Step 7: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/meta.py tests/test_meta.py
git commit -m "feat(ccsm): serialize lineage, title_locked, timestamps in sidecar metadata"
```

---

### Task 5: Persistent Session Index with Full-Text Search

**Files:**
- Create: `ccsm/core/index.py`
- Test: `tests/test_index.py`

Fixes pain points #3 (stable worktree-scoped views) and #4 (search + count consistency).

- [ ] **Step 1: Write tests for session index**

```python
# tests/test_index.py
"""Tests for persistent session index with full-text search."""
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ccsm.core.index import SessionIndex, IndexEntry


def _make_entries() -> list[IndexEntry]:
    return [
        IndexEntry(
            session_id="s1",
            worktree="main",
            project="GUI",
            title="fix-login-bug",
            intent="Fix the login validation bug",
            git_branch="main",
            first_user_content="The login form crashes when...",
            last_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            status="active",
            tags=["bug", "urgent"],
        ),
        IndexEntry(
            session_id="s2",
            worktree="panel",
            project="GUI",
            title="add-auth-feature",
            intent="Add OAuth2 authentication",
            git_branch="panel",
            first_user_content="We need OAuth2 integration",
            last_message_at=datetime(2026, 4, 1, 12, 0, tzinfo=timezone.utc),
            status="active",
            tags=["feature"],
        ),
        IndexEntry(
            session_id="s3",
            worktree="main",
            project="GUI",
            title="refactor-api",
            intent="Refactor API client",
            git_branch="main",
            first_user_content="The API client needs cleanup",
            last_message_at=datetime(2026, 3, 30, 8, 0, tzinfo=timezone.utc),
            status="done",
            tags=[],
        ),
    ]


def test_search_by_title():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("login")
    assert len(results) == 1
    assert results[0].session_id == "s1"


def test_search_by_intent():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("OAuth")
    assert len(results) == 1
    assert results[0].session_id == "s2"


def test_search_by_content():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("crashes")
    assert len(results) == 1
    assert results[0].session_id == "s1"


def test_search_fuzzy():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("auth")
    assert len(results) >= 1
    assert any(r.session_id == "s2" for r in results)


def test_filter_by_worktree():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("", worktree="panel")
    assert len(results) == 1
    assert results[0].session_id == "s2"


def test_filter_by_project():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("", project="GUI")
    assert len(results) == 3


def test_list_all_no_limit():
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    results = idx.search("")
    assert len(results) == 3


def test_save_and_load(tmp_path):
    idx = SessionIndex()
    idx.update_entries(_make_entries())
    idx.save(tmp_path / "test_index.json")

    idx2 = SessionIndex.load(tmp_path / "test_index.json")
    results = idx2.search("login")
    assert len(results) == 1
    assert results[0].session_id == "s1"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_index.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'ccsm.core.index'`

- [ ] **Step 3: Implement SessionIndex**

```python
# ccsm/core/index.py
"""Persistent session index with full-text fuzzy search.

Fixes pain points:
  #3 — Worktree-scoped stable views (filter by worktree/project)
  #4 — Real search across all fields, no arbitrary count limits
"""
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class IndexEntry:
    """Searchable index entry for one session."""
    session_id: str
    worktree: str = ""
    project: str = ""
    title: str = ""
    intent: str = ""
    git_branch: str = ""
    first_user_content: str = ""
    last_message_at: Optional[datetime] = None
    status: str = ""
    tags: list[str] = field(default_factory=list)

    def search_text(self) -> str:
        """Concatenated text for fuzzy matching."""
        parts = [
            self.title,
            self.intent,
            self.git_branch,
            self.first_user_content,
            self.session_id[:8],
            " ".join(self.tags),
        ]
        return " ".join(p for p in parts if p).lower()


class SessionIndex:
    """In-memory session index with persistence."""

    def __init__(self) -> None:
        self._entries: dict[str, IndexEntry] = {}

    def update_entries(self, entries: list[IndexEntry]) -> None:
        """Add or update entries in the index."""
        for e in entries:
            self._entries[e.session_id] = e

    def remove(self, session_id: str) -> None:
        """Remove an entry."""
        self._entries.pop(session_id, None)

    def search(
        self,
        query: str,
        *,
        worktree: Optional[str] = None,
        project: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 0,
    ) -> list[IndexEntry]:
        """Search entries with optional filters.

        Args:
            query: Search string (fuzzy-matched against all text fields).
                   Empty string returns all entries matching filters.
            worktree: Filter by worktree name.
            project: Filter by project name.
            status: Filter by status string.
            limit: Max results (0 = unlimited).

        Returns:
            List of matching IndexEntry, sorted by relevance then last_message_at.
        """
        candidates = list(self._entries.values())

        # Apply filters
        if worktree:
            candidates = [e for e in candidates if e.worktree == worktree]
        if project:
            candidates = [e for e in candidates if e.project == project]
        if status:
            candidates = [e for e in candidates if e.status == status]

        if not query:
            # No search query — return filtered list sorted by time
            candidates.sort(
                key=lambda e: e.last_message_at or datetime.min.replace(
                    tzinfo=timezone.utc
                ),
                reverse=True,
            )
            return candidates[:limit] if limit else candidates

        # Fuzzy search: score each candidate
        query_lower = query.lower()
        query_terms = query_lower.split()
        scored: list[tuple[float, IndexEntry]] = []

        for entry in candidates:
            text = entry.search_text()
            score = 0.0

            # Exact substring match in title — highest weight
            if query_lower in (entry.title or "").lower():
                score += 10.0
            # Exact substring in intent
            if query_lower in (entry.intent or "").lower():
                score += 5.0
            # Term-by-term matching
            for term in query_terms:
                if term in text:
                    score += 1.0

            if score > 0:
                scored.append((score, entry))

        # Sort by score descending, then by time descending
        scored.sort(
            key=lambda pair: (
                pair[0],
                pair[1].last_message_at or datetime.min.replace(
                    tzinfo=timezone.utc
                ),
            ),
            reverse=True,
        )

        results = [entry for _, entry in scored]
        return results[:limit] if limit else results

    def save(self, path: Path) -> None:
        """Save index to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        data = []
        for entry in self._entries.values():
            d = asdict(entry)
            if d.get("last_message_at"):
                d["last_message_at"] = d["last_message_at"].isoformat()
            else:
                d["last_message_at"] = None
            data.append(d)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, path: Path) -> "SessionIndex":
        """Load index from JSON file."""
        idx = cls()
        if not path.exists():
            return idx
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return idx
        for d in data:
            ts_str = d.get("last_message_at")
            ts = None
            if ts_str:
                try:
                    ts = datetime.fromisoformat(ts_str)
                except ValueError:
                    pass
            d["last_message_at"] = ts
            d["tags"] = d.get("tags", [])
            idx._entries[d["session_id"]] = IndexEntry(**d)
        return idx
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_index.py -v`
Expected: PASS — all 8 tests pass

- [ ] **Step 5: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/index.py tests/test_index.py
git commit -m "feat(ccsm): add persistent session index with full-text fuzzy search"
```

---

### Task 6: Duplicate Detection in Discovery

**Files:**
- Modify: `ccsm/core/discovery.py` (add `detect_duplicates()`)
- Test: `tests/test_discovery_enhanced.py`

Fixes pain point #7: two SSH terminals creating two sessions for the same work.

- [ ] **Step 1: Write test for duplicate detection**

```python
# tests/test_discovery_enhanced.py
"""Tests for enhanced discovery: duplicate detection."""
from datetime import datetime, timezone

from ccsm.core.lineage import LineageSignals
from ccsm.core.discovery import detect_duplicates


def test_detect_duplicates_overlapping():
    """Sessions with same cwd+branch and <5min gap are duplicates."""
    signals = {
        "s1": LineageSignals(
            session_id="s1",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
            first_user_content="帮我修复登录的bug",
        ),
        "s2": LineageSignals(
            session_id="s2",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 28, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 11, 0, tzinfo=timezone.utc),
            first_user_content="帮我修复登录的bug",
        ),
    }
    groups = detect_duplicates(signals)
    assert len(groups) == 1
    assert set(groups[0]) == {"s1", "s2"}


def test_no_false_duplicates():
    """Different branches or large time gap = independent."""
    signals = {
        "s1": LineageSignals(
            session_id="s1",
            cwd="/home/user/project",
            git_branch="main",
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
        "s2": LineageSignals(
            session_id="s2",
            cwd="/home/user/project",
            git_branch="feature",  # different branch
            first_message_at=datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc),
            last_message_at=datetime(2026, 4, 1, 10, 30, tzinfo=timezone.utc),
        ),
    }
    groups = detect_duplicates(signals)
    assert len(groups) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_discovery_enhanced.py -v`
Expected: FAIL — `ImportError: cannot import name 'detect_duplicates'`

- [ ] **Step 3: Implement detect_duplicates**

At the end of `ccsm/core/discovery.py`, add:

```python
from ccsm.core.lineage import LineageSignals


def detect_duplicates(
    signals_map: dict[str, LineageSignals],
    max_gap_seconds: float = 300,
) -> list[list[str]]:
    """Find groups of sessions that are likely duplicates.

    Duplicates are sessions with same (cwd, git_branch) and overlapping or
    near-overlapping time ranges (gap < max_gap_seconds).

    Fixes pain point #7: two SSH terminals creating separate sessions.

    Returns list of duplicate groups (each group is a list of session_ids).
    """
    # Group by (cwd, branch)
    by_key: dict[tuple[str, str], list[str]] = {}
    for sid, sig in signals_map.items():
        if sig.cwd and sig.git_branch:
            key = (sig.cwd, sig.git_branch)
            by_key.setdefault(key, []).append(sid)

    groups: list[list[str]] = []
    for key, sids in by_key.items():
        if len(sids) < 2:
            continue
        # Sort by first_message_at
        sorted_sids = sorted(
            sids,
            key=lambda s: signals_map[s].first_message_at or datetime.min.replace(
                tzinfo=timezone.utc,
            ),
        )
        # Cluster: consecutive sessions with gap < threshold
        cluster: list[str] = [sorted_sids[0]]
        for i in range(1, len(sorted_sids)):
            curr = signals_map[sorted_sids[i]]
            prev = signals_map[sorted_sids[i - 1]]
            if curr.first_message_at and prev.last_message_at:
                gap = (curr.first_message_at - prev.last_message_at).total_seconds()
                if gap < max_gap_seconds:
                    cluster.append(sorted_sids[i])
                    continue
            # Gap too large — finalize cluster, start new
            if len(cluster) >= 2:
                groups.append(cluster)
            cluster = [sorted_sids[i]]
        if len(cluster) >= 2:
            groups.append(cluster)

    return groups
```

Also add import at the top:

```python
from datetime import datetime, timezone
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_discovery_enhanced.py -v`
Expected: PASS — both tests pass

- [ ] **Step 5: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/core/discovery.py tests/test_discovery_enhanced.py
git commit -m "feat(ccsm): add duplicate session detection for multi-SSH scenarios"
```

---

### Task 7: MainScreen Pipeline Integration

**Files:**
- Modify: `ccsm/tui/screens/main.py` (integrate lineage, index, timestamps, stable titles)
- Modify: `ccsm/tui/widgets/session_list.py` (use index for search, show lineage badges)
- Modify: `ccsm/tui/widgets/session_card.py` (lineage badge rendering)

This task integrates all the new core modules into the TUI pipeline.

- [ ] **Step 1: Add lineage scanning to _load_data pipeline**

In `ccsm/tui/screens/main.py`, add imports at the top:

```python
from ccsm.core.lineage import parse_lineage_signals, build_lineage_graph, LineageSignals
from ccsm.core.index import SessionIndex, IndexEntry
from ccsm.core.meta import lock_title
```

Add new instance variables in `__init__()` (after line 73):

```python
        self._lineage_signals: dict[str, LineageSignals] = {}
        self._session_index = SessionIndex()
```

- [ ] **Step 2: Integrate lineage scanning into _parse_and_display**

In `_parse_and_display()` (around line 239-286), after the `parse_session_info()` loop but before `classify_all()`, add lineage scanning:

```python
        # Scan lineage signals (fast — only reads type/timestamp/metadata fields)
        for s in sessions:
            try:
                sig = parse_lineage_signals(
                    s.jsonl_path,
                    display_name=s.display_name,
                )
                self._lineage_signals[s.session_id] = sig
                # Fix pain point #6: use last_message_at instead of file mtime
                if sig.last_message_at and (
                    s.last_timestamp is None
                    or sig.last_message_at != s.last_timestamp
                ):
                    s.last_timestamp = sig.last_message_at
            except Exception:
                pass
```

- [ ] **Step 3: Fix sorting to use last_message_at instead of file mtime**

In `ccsm/tui/widgets/session_list.py`, the sorting in `_rebuild()` (around line 195-200) currently sorts by `last_timestamp`. This already uses the parser-extracted timestamp. After Step 2, `last_timestamp` will reflect `last_message_at` from lineage signals, which is the last actual message time, not file mtime. No code change needed here — the fix flows from Step 2.

- [ ] **Step 4: Build session index after parsing**

In `_parse_and_display()`, after the lineage scanning block, add index building:

```python
        # Build search index (pain point #4: real full-text search)
        index_entries = []
        for s in sessions:
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
```

- [ ] **Step 5: Replace search handler to use SessionIndex**

In `on_input_changed()` (around line 490-516), replace the inline fuzzy search with index-backed search:

```python
    def on_input_changed(self, event: Input.Changed) -> None:
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
        panel.load_sessions(filtered, self._all_meta, self._last_thoughts)
```

- [ ] **Step 6: Apply stable title on AI title generation**

In `_generate_ai_title_for()` (around line 535-567), after successfully generating a title, lock it in sidecar:

```python
            # Lock title in CCSM sidecar (pain point #2: prevent revert)
            lock_title(session.session_id, title)
```

- [ ] **Step 7: Add lineage badge to SessionCard**

In `ccsm/tui/widgets/session_card.py`, add a lineage badge after the status icon. Modify the `render()` method to accept and display lineage type.

First, add `lineage_type` to `__init__()`:

```python
    def __init__(
        self, session: SessionInfo, meta: SessionMeta,
        last_thought: str = "",
        lineage_type: Optional[str] = None,
    ) -> None:
        super().__init__()
        self.session = session
        self.meta = meta
        self._last_thought = last_thought
        self._lineage_type = lineage_type
```

In `render()`, after the status icon (around line 98-99), add lineage badge:

```python
        # Lineage badge
        lineage_badge = ""
        if self._lineage_type == "fork":
            lineage_badge = " [#60a5fa]fork[/]"
        elif self._lineage_type == "compact":
            lineage_badge = " [#a78bfa]compact[/]"
        elif self._lineage_type == "duplicate":
            lineage_badge = " [#f87171]dup[/]"
```

Include `lineage_badge` in the line 1 markup after the title.

- [ ] **Step 8: Pass lineage_type when creating SessionCards**

In `ccsm/tui/widgets/session_list.py` `_rebuild()` (around line 211-217), when creating `SessionCard`, pass lineage type:

```python
                lineage_type = None
                if hasattr(self, '_lineage_signals') and s.session_id in self._lineage_signals:
                    sig = self._lineage_signals[s.session_id]
                    if sig.is_fork:
                        lineage_type = "fork"
```

For cleaner data flow, add a `_lineage_types: dict[str, str]` parameter to `load_sessions()`:

```python
    def load_sessions(
        self,
        sessions: list[SessionInfo],
        all_meta: dict[str, SessionMeta],
        last_thoughts: dict[str, str],
        lineage_types: Optional[dict[str, str]] = None,
    ) -> None:
        self._sessions = sessions
        self._all_meta = all_meta
        self._last_thoughts = last_thoughts
        self._lineage_types = lineage_types or {}
        self._auto_select_tab()
        self._rebuild()
```

Then in `_rebuild()`, use `self._lineage_types.get(s.session_id)` when creating cards.

- [ ] **Step 9: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/tui/screens/main.py \
       projects/ccsm/ccsm/tui/widgets/session_list.py \
       projects/ccsm/ccsm/tui/widgets/session_card.py
git commit -m "feat(ccsm): integrate lineage, index, and timestamp fixes into TUI pipeline"
```

---

### Task 8: Session Graph Visualization Widget

**Files:**
- Create: `ccsm/tui/widgets/session_graph.py`
- Modify: `ccsm/tui/screens/main.py` (toggle graph view)
- Modify: `ccsm/tui/widgets/session_detail.py` (graph section)

Fixes pain point #8: tree/DAG visualization with time axis.

- [ ] **Step 1: Write the SessionGraph widget**

```python
# ccsm/tui/widgets/session_graph.py
"""Session DAG visualization widget.

Renders a tree/DAG of session relationships using Unicode box-drawing.
Nodes are sessions, edges represent fork/compact/duplicate relationships.
Time flows top-to-bottom, with the mainline (ROOT sessions) on the left.

Example output:
  ● fix-login-bug                    Apr 1 10:00
  │
  ├── ● add-auth (fork)              Apr 1 12:00
  │   └── ● auth-debug (compact)     Apr 1 14:00
  │
  ├── ● fix-login-v2 (dup)           Apr 1 10:28
  │
  └── ● refactor-api                 Apr 2 09:00
"""
from datetime import datetime
from typing import Optional

from rich.text import Text
from textual.widgets import Static

from ccsm.models.session import LineageType, SessionLineage


class SessionGraph(Static):
    """Renders a session lineage tree as Unicode art."""

    DEFAULT_CSS = """
    SessionGraph {
        padding: 1;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._nodes: list[_GraphNode] = []

    def set_data(
        self,
        lineages: dict[str, SessionLineage],
        titles: dict[str, str],
        timestamps: dict[str, Optional[datetime]],
        current_session_id: Optional[str] = None,
    ) -> None:
        """Build graph from lineage data.

        Args:
            lineages: session_id -> SessionLineage mapping
            titles: session_id -> display title
            timestamps: session_id -> last_message_at
            current_session_id: highlight this node
        """
        self._nodes = _build_tree(lineages, titles, timestamps, current_session_id)
        self.update(self._render())

    def _render(self) -> str:
        if not self._nodes:
            return "[#78716c]No lineage data[/]"

        lines: list[str] = []
        for node in self._nodes:
            indent = node.indent_str
            icon = _type_icon(node.lineage_type)
            highlight = "[bold #fb923c]" if node.is_current else ""
            end_hl = "[/]" if node.is_current else ""
            time_str = node.time_str or ""

            badge = ""
            if node.lineage_type == LineageType.FORK:
                badge = " [#60a5fa]fork[/]"
            elif node.lineage_type == LineageType.COMPACT:
                badge = " [#a78bfa]compact[/]"
            elif node.lineage_type == LineageType.DUPLICATE:
                badge = " [#f87171]dup[/]"

            title = node.title[:30] if node.title else node.session_id[:8]
            line = f"{indent}{icon} {highlight}{title}{end_hl}{badge}"
            # Right-align timestamp
            pad = max(1, 60 - len(Text.from_markup(line).plain))
            line += " " * pad + f"[#78716c]{time_str}[/]"
            lines.append(line)

            # Draw connector to next sibling/child
            if node.has_children:
                lines.append(f"{indent}│")

        return "\n".join(lines)


class _GraphNode:
    """Internal node for rendering."""
    __slots__ = (
        "session_id", "title", "lineage_type", "depth",
        "indent_str", "time_str", "is_current", "has_children",
    )

    def __init__(
        self,
        session_id: str,
        title: str,
        lineage_type: LineageType,
        depth: int,
        indent_str: str,
        time_str: Optional[str],
        is_current: bool,
        has_children: bool,
    ) -> None:
        self.session_id = session_id
        self.title = title
        self.lineage_type = lineage_type
        self.depth = depth
        self.indent_str = indent_str
        self.time_str = time_str
        self.is_current = is_current
        self.has_children = has_children


def _build_tree(
    lineages: dict[str, SessionLineage],
    titles: dict[str, str],
    timestamps: dict[str, Optional[datetime]],
    current_id: Optional[str],
) -> list[_GraphNode]:
    """Flatten DAG into ordered list of _GraphNode for rendering."""
    # Find roots (no parent)
    roots = [
        sid for sid, lin in lineages.items()
        if lin.parent_id is None
    ]
    # Sort roots by timestamp
    roots.sort(
        key=lambda s: timestamps.get(s) or datetime.min,
    )

    nodes: list[_GraphNode] = []

    def _walk(sid: str, depth: int, prefix: str, is_last: bool) -> None:
        lin = lineages.get(sid)
        if not lin:
            return
        children = [c for c in lin.children if c in lineages]
        children.sort(key=lambda c: timestamps.get(c) or datetime.min)

        # Build indent string
        if depth == 0:
            indent = ""
        elif is_last:
            indent = prefix + "└── "
        else:
            indent = prefix + "├── "

        # Time string
        ts = timestamps.get(sid)
        time_str = ts.strftime("%b %d %H:%M") if ts else None

        nodes.append(_GraphNode(
            session_id=sid,
            title=titles.get(sid, sid[:8]),
            lineage_type=lin.lineage_type,
            depth=depth,
            indent_str=indent,
            time_str=time_str,
            is_current=(sid == current_id),
            has_children=bool(children),
        ))

        # Recurse children
        child_prefix = prefix + ("    " if is_last else "│   ") if depth > 0 else ""
        for i, child in enumerate(children):
            _walk(child, depth + 1, child_prefix, i == len(children) - 1)

    for i, root in enumerate(roots):
        _walk(root, 0, "", i == len(roots) - 1)

    return nodes


def _type_icon(lt: LineageType) -> str:
    return {
        LineageType.ROOT: "●",
        LineageType.FORK: "◆",
        LineageType.COMPACT: "◇",
        LineageType.DUPLICATE: "◉",
    }.get(lt, "○")
```

- [ ] **Step 2: Add graph toggle keybinding to MainScreen**

In `ccsm/tui/screens/main.py`, add to BINDINGS:

```python
        Binding("g", "toggle_graph", "Graph View"),
```

Add new instance variable:

```python
        self._graph_visible: bool = False
```

Add action handler:

```python
    def action_toggle_graph(self) -> None:
        """Toggle session graph view in the detail panel."""
        self._graph_visible = not self._graph_visible
        if self._graph_visible and self._selected_session:
            self._show_graph()
        elif not self._graph_visible and self._selected_session:
            # Reload normal detail
            self._load_session_detail(self._selected_session)

    def _show_graph(self) -> None:
        """Build and display session graph from lineage data."""
        from ccsm.core.lineage import build_lineage_graph
        from ccsm.tui.widgets.session_graph import SessionGraph

        graph = build_lineage_graph(self._lineage_signals)
        titles = {}
        timestamps = {}
        for sid, sig in self._lineage_signals.items():
            # Find display title
            meta = self._all_meta.get(sid)
            session = next(
                (s for s in self._current_sessions if s.session_id == sid),
                None,
            )
            titles[sid] = (
                (meta.name if meta and meta.name else None)
                or (session.display_title if session else sid[:8])
            )
            timestamps[sid] = sig.last_message_at

        detail = self.query_one(SessionDetail)
        detail.remove_children()

        widget = SessionGraph()
        detail.mount(widget)
        widget.set_data(
            graph,
            titles,
            timestamps,
            current_session_id=(
                self._selected_session.session_id
                if self._selected_session else None
            ),
        )
```

- [ ] **Step 3: Update footer to show graph keybinding**

In the footer bar `compose()` section (around line 95-101), add `g Graph` to the keybinding help text.

- [ ] **Step 4: Commit**

```bash
cd /home/v-tangxin/GUI
git add projects/ccsm/ccsm/tui/widgets/session_graph.py \
       projects/ccsm/ccsm/tui/screens/main.py
git commit -m "feat(ccsm): add session DAG visualization with lineage tree view"
```

---

### Task 9: End-to-End Smoke Test

**Files:**
- Create: `tests/test_e2e_pipeline.py`

- [ ] **Step 1: Write an end-to-end pipeline test**

```python
# tests/test_e2e_pipeline.py
"""End-to-end smoke test for the enhanced CCSM pipeline.

Verifies the complete flow: JSONL → lineage signals → index → search → graph.
"""
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from ccsm.core.lineage import parse_lineage_signals, build_lineage_graph
from ccsm.core.index import SessionIndex, IndexEntry
from ccsm.core.parser import parse_session_timestamps
from ccsm.core.discovery import detect_duplicates
from ccsm.models.session import LineageType


def _write_jsonl(path: Path, lines: list[dict]) -> None:
    with open(path, "w") as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")


def test_full_pipeline():
    """Complete pipeline: parse → lineage → index → search."""
    tmp = Path(tempfile.mkdtemp())

    # Create two sessions: parent and fork
    parent_path = tmp / "parent-001.jsonl"
    _write_jsonl(parent_path, [
        {
            "type": "user",
            "message": {"role": "user", "content": "实现一个登录页面"},
            "sessionId": "parent-001",
            "cwd": "/home/user/project",
            "gitBranch": "main",
            "timestamp": "2026-04-01T10:00:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "好的，我来实现登录页面..."},
            "sessionId": "parent-001",
            "timestamp": "2026-04-01T10:05:00Z",
        },
    ])

    fork_path = tmp / "fork-002.jsonl"
    _write_jsonl(fork_path, [
        {
            "type": "user",
            "message": {"role": "user", "content": "调试一下CSS样式问题"},
            "sessionId": "fork-002",
            "cwd": "/home/user/project",
            "gitBranch": "main",
            "timestamp": "2026-04-01T10:03:00Z",
        },
        {
            "type": "assistant",
            "message": {"role": "assistant", "content": "让我检查CSS..."},
            "sessionId": "fork-002",
            "timestamp": "2026-04-01T10:08:00Z",
        },
    ])

    # Phase 1: Parse lineage signals
    sig_parent = parse_lineage_signals(parent_path)
    sig_fork = parse_lineage_signals(fork_path, display_name="login-page (branch)")

    assert sig_parent.is_fork is False
    assert sig_fork.is_fork is True

    # Phase 2: Parse timestamps
    ts_parent = parse_session_timestamps(parent_path)
    assert ts_parent.last_message_at == datetime(2026, 4, 1, 10, 5, tzinfo=timezone.utc)

    # Phase 3: Build lineage graph
    signals_map = {
        "parent-001": sig_parent,
        "fork-002": sig_fork,
    }
    graph = build_lineage_graph(signals_map)
    assert graph["fork-002"].lineage_type == LineageType.FORK

    # Phase 4: Detect duplicates (these overlap in time and share cwd+branch)
    dups = detect_duplicates(signals_map)
    assert len(dups) == 1  # They overlap in time

    # Phase 5: Build search index
    idx = SessionIndex()
    idx.update_entries([
        IndexEntry(
            session_id="parent-001",
            title="login-page",
            intent="实现登录页面",
            git_branch="main",
            first_user_content="实现一个登录页面",
            last_message_at=ts_parent.last_message_at,
            status="active",
        ),
        IndexEntry(
            session_id="fork-002",
            title="login-page (branch)",
            intent="调试CSS样式",
            git_branch="main",
            first_user_content="调试一下CSS样式问题",
            last_message_at=sig_fork.last_message_at,
            status="active",
        ),
    ])

    # Search by Chinese content
    results = idx.search("登录")
    assert len(results) >= 1
    assert any(r.session_id == "parent-001" for r in results)

    # Search by intent
    results = idx.search("CSS")
    assert len(results) == 1
    assert results[0].session_id == "fork-002"
```

- [ ] **Step 2: Run the test**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/test_e2e_pipeline.py -v`
Expected: PASS

- [ ] **Step 3: Run all tests**

Run: `cd /home/v-tangxin/GUI && PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
cd /home/v-tangxin/GUI
git add tests/test_e2e_pipeline.py
git commit -m "test(ccsm): add end-to-end pipeline smoke test covering all 8 pain points"
```

---

## Pain Point → Implementation Verification

| # | Pain Point | Fix Location | How to Verify |
|---|---|---|---|
| 1 | Fork name indistinguishable | `lineage.py:parse_lineage_signals()` detects forks; `session_card.py` shows `fork` badge | Fork sessions show distinct badge + unique AI title |
| 2 | Renamed title reverts | `meta.py:lock_title()` stores title in CCSM sidecar with `title_locked=True` | Title survives Claude Code crash/re-append |
| 3 | Worktree resume list unstable | `index.py:SessionIndex.search(worktree=...)` provides stable filtering | Search results consistent regardless of cwd changes |
| 4 | Search useless, count varies | `index.py:SessionIndex.search()` full-text search, no arbitrary limits | Search finds sessions by title, intent, content, tags |
| 5 | Compact spawns phantom | `lineage.py` detects compact boundaries, links as COMPACT type | Compact continuations shown as linked nodes, not separate |
| 6 | Resume changes timeline | `main.py:_parse_and_display()` overwrites `last_timestamp` with `last_message_at` | Sessions sort by last message time, not last access |
| 7 | Multi-SSH duplicates | `discovery.py:detect_duplicates()` + lineage DUPLICATE type | Duplicate sessions grouped and badged |
| 8 | No tree visualization | `session_graph.py:SessionGraph` renders DAG with box-drawing | Press `g` to toggle graph view in detail panel |
