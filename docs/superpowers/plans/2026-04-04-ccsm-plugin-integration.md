# CCSM Plugin Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CCSM 打包为 Claude Code plugin，实现增量数据处理、默认标题显示优化，并支持 `claude --resume` 底层接口集成。

**Architecture:** 参照 claude-mem 的 plugin 模式（`.claude-plugin/plugin.json` + `.mcp.json` + `hooks/hooks.json`），用 Node.js shim 包装 Python MCP server。数据层增加 SQLite 持久化索引实现增量处理。TUI 标题逻辑改为原始标题优先、AI 标题仅作 fallback。

**Tech Stack:** Python 3.10+ (FastMCP, Textual, SQLite3), Node.js shim (stdio proxy), Claude Code Plugin API

---

## File Structure

```
ccsm/
├── .claude-plugin/
│   └── plugin.json              # NEW: Plugin metadata
├── .mcp.json                     # NEW: MCP server declaration
├── hooks/
│   └── hooks.json               # NEW: Lifecycle hooks
├── scripts/
│   ├── mcp-shim.js              # NEW: Node.js → Python stdio proxy
│   └── worker.js                # NEW: Background indexer trigger
├── ccsm/
│   ├── core/
│   │   ├── index_db.py          # NEW: SQLite persistent index
│   │   ├── discovery.py         # MODIFY: add mtime-based incremental scan
│   │   └── meta.py              # (unchanged)
│   ├── mcp/
│   │   └── server.py            # MODIFY: add enter_session tool, refine resume
│   ├── models/
│   │   └── session.py           # MODIFY: display_title priority tweak
│   └── tui/
│       └── screens/main.py      # MODIFY: title display & batch enrich logic
├── tests/
│   ├── test_index_db.py         # NEW: SQLite index tests
│   └── test_plugin_structure.py # NEW: Plugin packaging tests
└── pyproject.toml               # MODIFY: add scripts entry
```


---

### Task 1: Plugin 骨架打包

**Files:**
- Create: `.claude-plugin/plugin.json`
- Create: `.mcp.json`
- Create: `scripts/mcp-shim.js`

- [ ] **Step 1: 创建 `.claude-plugin/plugin.json`**

```json
{
  "name": "ccsm",
  "version": "0.1.0",
  "description": "Claude Code Session Manager — query, search, resume and manage Claude Code sessions across projects",
  "author": {
    "name": "tangxin"
  },
  "license": "MIT",
  "keywords": ["session", "manager", "resume", "history", "context"]
}
```

- [ ] **Step 2: 创建 `.mcp.json`**

MCP server 声明，使用 Node.js shim 包装 Python MCP server：

```json
{
  "mcpServers": {
    "ccsm": {
      "type": "stdio",
      "command": "node",
      "args": ["${CLAUDE_PLUGIN_ROOT}/scripts/mcp-shim.js"]
    }
  }
}
```

- [ ] **Step 3: 创建 `scripts/mcp-shim.js`**

Node.js stdio proxy — 启动 Python MCP server 子进程，双向转发 stdin/stdout：

```javascript
#!/usr/bin/env node
/**
 * CCSM MCP Shim — bridges Claude Code plugin system (expects Node.js)
 * to the Python FastMCP server via stdio pipe.
 *
 * Claude Code sends JSON-RPC over stdin → this shim → Python subprocess stdin
 * Python subprocess stdout → this shim → Claude Code reads from stdout
 */
const { spawn } = require('child_process');
const path = require('path');

// Resolve plugin root (CLAUDE_PLUGIN_ROOT or fallback)
const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');

// Find Python — prefer venv if available
const pythonPaths = [
  path.join(pluginRoot, 'venv', 'bin', 'python3'),
  path.join(pluginRoot, '.venv', 'bin', 'python3'),
  'python3',
];

let pythonCmd = 'python3';
const fs = require('fs');
for (const p of pythonPaths) {
  if (p === 'python3' || fs.existsSync(p)) {
    pythonCmd = p;
    break;
  }
}

// Spawn Python MCP server
const child = spawn(pythonCmd, ['-m', 'ccsm.mcp.server'], {
  cwd: pluginRoot,
  stdio: ['pipe', 'pipe', 'inherit'],  // inherit stderr for debugging
  env: { ...process.env, PYTHONPATH: pluginRoot },
});

// Bidirectional pipe: Claude Code stdin <-> Python stdin
process.stdin.pipe(child.stdin);
child.stdout.pipe(process.stdout);

// Handle process lifecycle
child.on('exit', (code) => process.exit(code || 0));
child.on('error', (err) => {
  process.stderr.write(`CCSM shim error: ${err.message}\n`);
  process.exit(1);
});
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
```

- [ ] **Step 4: 验证 shim 可以启动 Python MCP server**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
node scripts/mcp-shim.js &
SHIM_PID=$!
sleep 2
# Send a JSON-RPC initialize request to test
echo '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"capabilities":{}}}' | timeout 5 node scripts/mcp-shim.js
kill $SHIM_PID 2>/dev/null
```

Expected: JSON-RPC response with server capabilities

- [ ] **Step 5: Commit**

```bash
git add .claude-plugin/plugin.json .mcp.json scripts/mcp-shim.js
git commit -m "feat: add Claude Code plugin skeleton (plugin.json + mcp.json + shim)"
```


---

### Task 2: Hooks 集成 — 生命周期事件

**Files:**
- Create: `hooks/hooks.json`
- Create: `scripts/worker.js`

- [ ] **Step 1: 创建 `hooks/hooks.json`**

集成 Claude Code 生命周期事件。核心 hooks：
- `SessionStart`: 触发增量索引更新
- `SessionEnd`: 记录会话结束时间、触发标题/摘要更新

```json
{
  "description": "CCSM session lifecycle hooks",
  "hooks": {
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "_R=\"${CLAUDE_PLUGIN_ROOT}\"; [ -z \"$_R\" ] && _R=\"$(pwd)\"; node \"$_R/scripts/worker.js\" index-refresh",
            "timeout": 30
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "node -e \"let d='';process.stdin.on('data',c=>d+=c);process.stdin.on('end',()=>{try{const{sessionId:s}=JSON.parse(d);if(!s)process.exit(0);const r=require('http').request({hostname:'127.0.0.1',port:37778,path:'/api/session-ended',method:'POST',headers:{'Content-Type':'application/json'}},()=>process.exit(0));r.on('error',()=>process.exit(0));r.end(JSON.stringify({sessionId:s}));setTimeout(()=>process.exit(0),3000)}catch{process.exit(0)}})\"",
            "timeout": 5
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 2: 创建 `scripts/worker.js`**

轻量 worker 脚本，调用 Python 增量索引：

```javascript
#!/usr/bin/env node
/**
 * CCSM Worker — triggers Python-side incremental indexing.
 * Called by hooks on SessionStart to refresh the session index.
 */
const { execSync } = require('child_process');
const path = require('path');

const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');
const action = process.argv[2] || 'index-refresh';

try {
  if (action === 'index-refresh') {
    execSync(
      `python3 -c "from ccsm.core.index_db import incremental_refresh; incremental_refresh()"`,
      {
        cwd: pluginRoot,
        env: { ...process.env, PYTHONPATH: pluginRoot },
        timeout: 25000,
        stdio: ['ignore', 'inherit', 'inherit'],
      }
    );
  }
} catch (err) {
  // Non-fatal: index refresh is best-effort
  process.stderr.write(`CCSM worker (${action}): ${err.message}\n`);
}
```

- [ ] **Step 3: 验证 hooks 文件格式正确**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -c "import json; json.load(open('hooks/hooks.json')); print('hooks.json valid')"
```

Expected: `hooks.json valid`

- [ ] **Step 4: Commit**

```bash
git add hooks/hooks.json scripts/worker.js
git commit -m "feat: add lifecycle hooks for SessionStart/SessionEnd integration"
```


---

### Task 3: SQLite 持久化索引 — 增量处理核心

**Files:**
- Create: `ccsm/core/index_db.py`
- Create: `tests/test_index_db.py`

- [ ] **Step 1: 编写 test_index_db.py 测试骨架**

```python
"""Tests for SQLite persistent session index."""
import tempfile
from pathlib import Path
from datetime import datetime, timezone

import pytest

from ccsm.core.index_db import SessionIndexDB


@pytest.fixture
def db(tmp_path):
    """Create a temporary SQLite index."""
    return SessionIndexDB(db_path=tmp_path / "test_index.db")


def test_upsert_and_get(db):
    """Upsert a session record and retrieve it."""
    db.upsert(
        session_id="abc-123",
        jsonl_path="/fake/path.jsonl",
        jsonl_mtime=1234567890.0,
        title="Test Session",
        slug="test-session-slug",
        status="active",
        message_count=42,
        last_timestamp=datetime(2026, 4, 4, tzinfo=timezone.utc),
    )
    row = db.get("abc-123")
    assert row is not None
    assert row["title"] == "Test Session"
    assert row["message_count"] == 42


def test_needs_refresh_detects_mtime_change(db):
    """needs_refresh returns True when mtime differs."""
    db.upsert(
        session_id="abc-123",
        jsonl_path="/fake/path.jsonl",
        jsonl_mtime=1000.0,
        title="Old",
        slug="old",
        status="active",
        message_count=10,
    )
    assert db.needs_refresh("abc-123", current_mtime=1000.0) is False
    assert db.needs_refresh("abc-123", current_mtime=2000.0) is True
    assert db.needs_refresh("unknown-id", current_mtime=1000.0) is True


def test_list_all(db):
    """list_all returns all indexed sessions."""
    for i in range(5):
        db.upsert(
            session_id=f"session-{i}",
            jsonl_path=f"/fake/{i}.jsonl",
            jsonl_mtime=float(i),
            title=f"Session {i}",
            slug=f"slug-{i}",
            status="active",
            message_count=i * 10,
        )
    results = db.list_all()
    assert len(results) == 5


def test_delete(db):
    """delete removes a session from the index."""
    db.upsert(
        session_id="to-delete",
        jsonl_path="/fake/del.jsonl",
        jsonl_mtime=100.0,
        title="Delete Me",
        slug="del",
        status="noise",
        message_count=1,
    )
    assert db.get("to-delete") is not None
    db.delete("to-delete")
    assert db.get("to-delete") is None
```

- [ ] **Step 2: 运行测试验证失败**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -m pytest tests/test_index_db.py -v
```

Expected: FAIL — `ModuleNotFoundError: No module named 'ccsm.core.index_db'`

- [ ] **Step 3: 实现 `ccsm/core/index_db.py`**

```python
"""SQLite-backed persistent session index for incremental processing.

Stores session stubs with mtime tracking. On startup, only re-parses JSONL
files whose mtime has changed since last index.

Storage: ~/.ccsm/index.db
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path.home() / ".ccsm" / "index.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    jsonl_path TEXT NOT NULL,
    jsonl_mtime REAL NOT NULL,
    title TEXT,
    slug TEXT,
    status TEXT,
    message_count INTEGER DEFAULT 0,
    last_timestamp TEXT,
    project_name TEXT,
    worktree_name TEXT,
    display_name TEXT,
    is_archived INTEGER DEFAULT 0,
    is_running INTEGER DEFAULT 0,
    indexed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sessions_mtime ON sessions(jsonl_mtime);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
"""


class SessionIndexDB:
    """SQLite persistent index for session metadata."""

    def __init__(self, db_path: Path | None = None):
        self._db_path = db_path or _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)

    def upsert(
        self,
        session_id: str,
        jsonl_path: str,
        jsonl_mtime: float,
        title: str | None = None,
        slug: str | None = None,
        status: str | None = None,
        message_count: int = 0,
        last_timestamp: datetime | None = None,
        project_name: str | None = None,
        worktree_name: str | None = None,
        display_name: str | None = None,
        is_archived: bool = False,
        is_running: bool = False,
    ) -> None:
        """Insert or update a session record."""
        now = datetime.now(timezone.utc).isoformat()
        last_ts_str = last_timestamp.isoformat() if last_timestamp else None
        self._conn.execute(
            """INSERT INTO sessions
               (session_id, jsonl_path, jsonl_mtime, title, slug, status,
                message_count, last_timestamp, project_name, worktree_name,
                display_name, is_archived, is_running, indexed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(session_id) DO UPDATE SET
                jsonl_path=excluded.jsonl_path,
                jsonl_mtime=excluded.jsonl_mtime,
                title=excluded.title,
                slug=excluded.slug,
                status=excluded.status,
                message_count=excluded.message_count,
                last_timestamp=excluded.last_timestamp,
                project_name=excluded.project_name,
                worktree_name=excluded.worktree_name,
                display_name=excluded.display_name,
                is_archived=excluded.is_archived,
                is_running=excluded.is_running,
                indexed_at=excluded.indexed_at
            """,
            (session_id, str(jsonl_path), jsonl_mtime, title, slug, status,
             message_count, last_ts_str, project_name, worktree_name,
             display_name, int(is_archived), int(is_running), now),
        )
        self._conn.commit()

    def needs_refresh(self, session_id: str, current_mtime: float) -> bool:
        """Check if a session needs re-parsing (mtime changed or not indexed)."""
        row = self._conn.execute(
            "SELECT jsonl_mtime FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        if row is None:
            return True  # Not indexed yet
        return abs(row["jsonl_mtime"] - current_mtime) > 0.001

    def get(self, session_id: str) -> Optional[dict]:
        """Get a session record by ID."""
        row = self._conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
        return dict(row) if row else None

    def list_all(self) -> list[dict]:
        """List all indexed sessions."""
        rows = self._conn.execute(
            "SELECT * FROM sessions ORDER BY last_timestamp DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    def delete(self, session_id: str) -> None:
        """Remove a session from the index."""
        self._conn.execute(
            "DELETE FROM sessions WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()


def incremental_refresh() -> int:
    """Run incremental refresh: scan ~/.claude/projects/, re-parse only changed files.

    Returns the number of sessions refreshed.
    """
    from ccsm.core.discovery import discover_projects
    from ccsm.core.parser import parse_session_info

    db = SessionIndexDB()
    projects = discover_projects()
    refreshed = 0

    for project in projects:
        for session in project.all_sessions:
            try:
                mtime = session.jsonl_path.stat().st_mtime
            except OSError:
                continue

            if not db.needs_refresh(session.session_id, mtime):
                continue

            # Parse this session's JSONL
            try:
                info = parse_session_info(session.jsonl_path)
                db.upsert(
                    session_id=info.session_id or session.session_id,
                    jsonl_path=str(session.jsonl_path),
                    jsonl_mtime=mtime,
                    title=info.display_title,
                    slug=info.slug,
                    status=info.status.value if info.status else None,
                    message_count=info.message_count,
                    last_timestamp=info.last_timestamp,
                    project_name=project.name,
                    worktree_name=session.project_dir,
                    display_name=info.display_name,
                    is_archived=session.is_archived,
                )
                refreshed += 1
            except Exception as e:
                logger.debug("Skip indexing %s: %s", session.session_id, e)

    db.close()
    return refreshed
```

- [ ] **Step 4: 运行测试验证通过**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -m pytest tests/test_index_db.py -v
```

Expected: 4 tests PASS

- [ ] **Step 5: 运行全量测试确认无回归**

```bash
python3 -m pytest tests/ -v
```

Expected: All existing tests still pass

- [ ] **Step 6: Commit**

```bash
git add ccsm/core/index_db.py tests/test_index_db.py
git commit -m "feat: add SQLite persistent index for incremental session processing"
```


---

### Task 4: 标题显示逻辑优化 — 原始标题优先

**Files:**
- Modify: `ccsm/models/session.py:148-170` (`display_title` property)
- Modify: `ccsm/tui/screens/main.py:876-950` (`_batch_enrich_sessions`)

**设计意图:** 用户反馈当前 AI 标题覆盖了原始的 display_name，希望默认展示原始标题，AI 标题仅在确实无意义时作为 fallback。

- [ ] **Step 1: 修改 `display_title` property 优先级**

在 `ccsm/models/session.py` 中调整 `display_title` property，让 display_name（即使是 slug 格式）也优先展示：

```python
@property
def display_title(self) -> str:
    """Best available title for display.

    Priority: display_name > custom_title > ai_title_from_cc
    > slug > session_id prefix.

    保留原始标题不做过滤 —— 过滤逻辑移到 batch_enrich 中，
    仅在 UI 后台静默替换时使用。
    """
    # display_name from history.jsonl — always show if present
    if self.display_name:
        return self.display_name
    # custom_title set by user in JSONL
    if self.custom_title:
        return self.custom_title
    # AI-generated title from Claude Code JSONL
    if self.ai_title_from_cc:
        return self.ai_title_from_cc
    # Slug
    if self.slug:
        return self.slug
    return self.session_id[:8]
```

**关键变更:** 移除了 `display_name` 中对 slash command 的 `.startswith("/")` 过滤，以及 slug 中对 3-word 格式的跳过。这样用户会看到原始的 `/resume`, `fix-bug-login` 等标题。

- [ ] **Step 2: 调整 `_batch_enrich_sessions` 的 AI 标题触发阈值**

在 `ccsm/tui/screens/main.py` 中，减少批量 AI 标题生成的候选数量，并提高触发门槛：

找到 `_batch_enrich_sessions` 方法（约 line 876），将批量限制从 40 降到 10，并增加触发条件（必须有 display_name 为空或 session_id 前缀）：

```python
# 在 _batch_enrich_sessions 的 Phase 2 部分，替换候选筛选逻辑:
candidates = [
    s for s in sessions
    if s.message_count >= 12  # 提高门槛：从 8 到 12
    and (not s.display_name or s.display_name == s.session_id[:8])  # 仅当无标题时
    and s.status != Status.NOISE
]
candidates.sort(key=lambda s: status_rank.get(s.status, 99))
candidates = candidates[:10]  # 从 40 降到 10
```

- [ ] **Step 3: 运行全量测试确认无回归**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -m pytest tests/ -v
```

Expected: All tests pass

- [ ] **Step 4: Commit**

```bash
git add ccsm/models/session.py ccsm/tui/screens/main.py
git commit -m "fix: show original session titles by default, AI title only as fallback"
```


---

### Task 5: MCP Server 增强 — enter_session 工具

**Files:**
- Modify: `ccsm/mcp/server.py`

**设计意图:** 当前 `resume_session` 只返回一个命令字符串 `claude --resume {sid}`，用户还需要手动运行。新增 `enter_session` 工具，提供更丰富的上下文信息（断点、上次话题、关键里程碑），让 Claude 在 resume 前能理解会话历史。

- [ ] **Step 1: 在 server.py 中添加 `enter_session` 工具**

在 `resume_session` 工具之后，添加新工具：

```python
@mcp.tool(
    name="enter_session",
    description=(
        "Prepare context for entering/resuming a previous Claude Code session. "
        "Returns session summary, breakpoint, last milestones, and the resume command. "
        "Use this to understand what happened before resuming."
    ),
)
def enter_session(session_id: str) -> dict:
    """Provide rich context for resuming a session."""
    from ccsm.core.meta import load_summary

    session_map, context_map, all_meta = _build_session_map()

    if session_id not in session_map:
        return {"error": f"Session not found: {session_id}"}

    info = session_map[session_id]
    project_name, wt_name = context_map[session_id]
    meta = all_meta.get(session_id, SessionMeta(session_id=session_id))

    # Build context
    result = {
        "session_id": session_id,
        "title": meta.name or info.display_title,
        "command": f"claude --resume {session_id}",
        "is_running": info.is_running,
        "status": info.status.value,
        "cwd": info.cwd,
        "git_branch": info.git_branch,
        "message_count": info.message_count,
    }

    # Add summary context if available
    cached = load_summary(session_id)
    if cached:
        result["summary"] = {
            "description": cached.description,
            "milestones": [
                {"label": ms.label, "status": ms.status.value}
                for ms in (cached.milestones or [])[-5:]  # Last 5 milestones
            ],
        }
        if cached.breakpoint:
            result["breakpoint"] = {
                "milestone": cached.breakpoint.milestone_label,
                "detail": cached.breakpoint.detail,
                "last_topic": cached.breakpoint.last_topic,
            }
        if cached.digest:
            result["digest"] = {
                "goal": cached.digest.goal,
                "progress": cached.digest.progress,
                "next_steps": cached.digest.next_steps,
                "blocker": cached.digest.blocker,
            }

    # Add last assistant snippet for quick context
    last_msgs = get_last_assistant_messages(info.jsonl_path, count=1)
    if last_msgs:
        result["last_reply_snippet"] = last_msgs[0].content[:300]

    return result
```

- [ ] **Step 2: 验证新工具在 MCP server 中注册**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -c "
from ccsm.mcp.server import mcp
tools = [t.name for t in mcp._tools.values()]
assert 'enter_session' in tools, f'enter_session not found in {tools}'
print(f'Tools registered: {tools}')
print('enter_session tool registered successfully')
"
```

Expected: `enter_session tool registered successfully`

- [ ] **Step 3: Commit**

```bash
git add ccsm/mcp/server.py
git commit -m "feat: add enter_session MCP tool for context-rich session resume"
```


---

### Task 6: 增量数据处理集成到 MCP Server

**Files:**
- Modify: `ccsm/mcp/server.py` (`_build_session_map`)

**设计意图:** 当前 `_build_session_map` 每 30s TTL 后全量重新解析所有 JSONL。改为：先从 SQLite 索引加载缓存数据，仅重新解析 mtime 变化的文件。

- [ ] **Step 1: 修改 `_build_session_map` 使用 SQLite 索引**

在 `_build_session_map` 函数中，添加 SQLite 快速路径：

```python
def _build_session_map(
    force_refresh: bool = False,
) -> tuple[dict[str, SessionInfo], dict[str, tuple[str, str | None]], dict[str, SessionMeta]]:
    """Build lookup maps with TTL caching + SQLite incremental index.

    Fast path: if SQLite index exists and TTL not expired, load from DB.
    Incremental path: on TTL expiry, only re-parse JSONL files with changed mtime.
    """
    now = time.monotonic()
    if (
        not force_refresh
        and _cache["session_map"] is not None
        and (now - _cache["timestamp"]) < _CACHE_TTL_SECONDS
    ):
        return _cache["session_map"], _cache["context_map"], _cache["all_meta"]

    # Try incremental refresh via SQLite
    try:
        from ccsm.core.index_db import SessionIndexDB, incremental_refresh
        # Trigger incremental refresh (only re-parses changed files)
        refreshed_count = incremental_refresh()
        if refreshed_count > 0:
            logger.info("Incrementally refreshed %d sessions", refreshed_count)
    except Exception as e:
        logger.debug("SQLite incremental refresh unavailable: %s", e)

    # Continue with existing full discovery (but now much faster since
    # parse_session_info will hit OS cache for unchanged files)
    projects = discover_projects()
    running = load_running_sessions()
    display_names = load_display_names()
    all_meta = load_all_meta()

    session_map: dict[str, SessionInfo] = {}
    context_map: dict[str, tuple[str, str | None]] = {}

    for project in projects:
        if project.main_worktree:
            for session in project.main_worktree.sessions:
                info = parse_session_info(session.jsonl_path)
                info.project_dir = session.project_dir
                info.is_archived = session.is_archived
                info.is_running = running.get(info.session_id, False)
                if info.session_id in display_names:
                    info.display_name = display_names[info.session_id]
                session_map[info.session_id] = info
                context_map[info.session_id] = (project.name, None)

        for wt in project.worktrees:
            for session in wt.sessions:
                info = parse_session_info(session.jsonl_path)
                info.project_dir = session.project_dir
                info.is_archived = session.is_archived
                info.is_running = running.get(info.session_id, False)
                if info.session_id in display_names:
                    info.display_name = display_names[info.session_id]
                session_map[info.session_id] = info
                context_map[info.session_id] = (project.name, wt.name)

    classify_all(list(session_map.values()), all_meta)

    for sid, info in session_map.items():
        meta = all_meta.get(sid)
        if meta and meta.name:
            info.display_name = meta.name

    _cache["session_map"] = session_map
    _cache["context_map"] = context_map
    _cache["all_meta"] = all_meta
    _cache["timestamp"] = now

    return session_map, context_map, all_meta
```

注意：这是渐进式改进 —— SQLite 索引作为加速层，全量 discovery 仍作为 fallback。后续可以进一步优化为完全从 DB 加载。

- [ ] **Step 2: 验证 MCP server 仍正常工作**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -c "
from ccsm.mcp.server import _build_session_map
sm, cm, am = _build_session_map(force_refresh=True)
print(f'Sessions loaded: {len(sm)}')
print(f'First 3 titles: {[info.display_title for info in list(sm.values())[:3]]}')
"
```

Expected: Sessions loaded with count > 0

- [ ] **Step 3: 验证 SQLite 索引文件已创建**

```bash
ls -la ~/.ccsm/index.db
python3 -c "
from ccsm.core.index_db import SessionIndexDB
db = SessionIndexDB()
rows = db.list_all()
print(f'Indexed sessions: {len(rows)}')
db.close()
"
```

Expected: `~/.ccsm/index.db` exists, indexed sessions > 0

- [ ] **Step 4: Commit**

```bash
git add ccsm/mcp/server.py
git commit -m "feat: integrate SQLite incremental index into MCP server data pipeline"
```


---

### Task 7: Plugin 本地注册测试

**Files:**
- Create: `tests/test_plugin_structure.py`

- [ ] **Step 1: 编写 plugin 结构验证测试**

```python
"""Tests for Claude Code plugin packaging structure."""
import json
from pathlib import Path

import pytest

PLUGIN_ROOT = Path(__file__).parent.parent


def test_plugin_json_exists():
    """plugin.json must exist in .claude-plugin/."""
    path = PLUGIN_ROOT / ".claude-plugin" / "plugin.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "name" in data
    assert data["name"] == "ccsm"
    assert "version" in data


def test_mcp_json_exists():
    """.mcp.json must declare the ccsm MCP server."""
    path = PLUGIN_ROOT / ".mcp.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "mcpServers" in data
    assert "ccsm" in data["mcpServers"]
    server = data["mcpServers"]["ccsm"]
    assert server["type"] == "stdio"


def test_hooks_json_exists():
    """hooks.json must exist and be valid JSON."""
    path = PLUGIN_ROOT / "hooks" / "hooks.json"
    assert path.exists(), f"Missing {path}"
    data = json.loads(path.read_text())
    assert "hooks" in data
    assert "SessionStart" in data["hooks"] or "SessionEnd" in data["hooks"]


def test_mcp_shim_exists():
    """Node.js MCP shim script must exist."""
    path = PLUGIN_ROOT / "scripts" / "mcp-shim.js"
    assert path.exists(), f"Missing {path}"
    content = path.read_text()
    assert "spawn" in content  # Must spawn Python process
    assert "ccsm.mcp.server" in content  # Must reference Python module


def test_pyproject_toml_valid():
    """pyproject.toml must have ccsm entry point."""
    path = PLUGIN_ROOT / "pyproject.toml"
    assert path.exists()
    content = path.read_text()
    assert 'name = "ccsm"' in content
```

- [ ] **Step 2: 运行测试**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -m pytest tests/test_plugin_structure.py -v
```

Expected: All 5 tests PASS (assuming Tasks 1-2 completed)

- [ ] **Step 3: 手动注册 plugin 到本地 Claude Code**

在 `~/.claude/settings.local.json` 中添加 plugin 引用（如果文件不存在则创建）：

```bash
# 查看当前配置
cat ~/.claude/settings.local.json 2>/dev/null || echo '{}' 

# 确保 CCSM 可以作为 local plugin 被发现
# Option A: 直接在 MCP servers 中添加
python3 -c "
import json
from pathlib import Path

settings_path = Path.home() / '.claude' / 'settings.local.json'
if settings_path.exists():
    data = json.loads(settings_path.read_text())
else:
    data = {}

# Ensure mcpServers exists
if 'mcpServers' not in data:
    data['mcpServers'] = {}

# Add CCSM MCP server
data['mcpServers']['ccsm'] = {
    'type': 'stdio',
    'command': 'python3',
    'args': ['-m', 'ccsm.mcp.server'],
    'env': {
        'PYTHONPATH': '/home/v-tangxin/GUI/projects/ccsm'
    }
}

settings_path.write_text(json.dumps(data, indent=2))
print('CCSM MCP server registered in settings.local.json')
"
```

- [ ] **Step 4: 验证 Claude Code 可以发现 CCSM 工具**

启动新的 Claude Code 会话，运行：
```
/mcp
```
确认 ccsm server 出现在列表中。

- [ ] **Step 5: Commit**

```bash
git add tests/test_plugin_structure.py
git commit -m "test: add plugin structure validation tests"
```


---

### Task 8: 端到端集成验证

**Files:**
- (No new files — integration testing)

- [ ] **Step 1: 验证完整工具链**

```bash
cd /home/v-tangxin/GUI/projects/ccsm

# 1. 全量测试
python3 -m pytest tests/ -v

# 2. MCP server 工具列表
python3 -c "
from ccsm.mcp.server import mcp
tools = sorted([t.name for t in mcp._tools.values()])
print(f'MCP Tools ({len(tools)}):')
for t in tools:
    print(f'  - {t}')
assert len(tools) >= 7, f'Expected >= 7 tools, got {len(tools)}'
"

# 3. SQLite 增量索引
python3 -c "
from ccsm.core.index_db import incremental_refresh
count = incremental_refresh()
print(f'Incremental refresh: {count} sessions updated')
"

# 4. Plugin structure
python3 -m pytest tests/test_plugin_structure.py -v
```

Expected: All green

- [ ] **Step 2: 验证 TUI 启动无回归**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
timeout 5 python3 -m ccsm 2>&1 || true
# TUI 应该正常启动（timeout 5s 后自动退出是预期的）
```

- [ ] **Step 3: 验证标题显示变化**

```bash
python3 -c "
from ccsm.core.discovery import discover_projects, load_display_names
from ccsm.core.parser import parse_session_info

projects = discover_projects()
display_names = load_display_names()

# Show first 10 sessions with their title priority
count = 0
for p in projects:
    for s in p.all_sessions:
        if count >= 10:
            break
        info = parse_session_info(s.jsonl_path)
        if s.session_id in display_names:
            info.display_name = display_names[s.session_id]
        print(f'{info.session_id[:8]} | display_name={info.display_name!r:30s} | display_title={info.display_title!r}')
        count += 1
"
```

Expected: 看到原始 display_name 被保留展示

- [ ] **Step 4: 最终 commit（如有修复）**

```bash
git status
# 如有未提交的修复
git add -A
git commit -m "fix: integration fixes for plugin packaging"
```

---

## 执行顺序与依赖

```
Task 1 (Plugin骨架) ──┐
Task 2 (Hooks)     ──┤── Task 7 (注册测试)
Task 3 (SQLite索引) ──┤── Task 6 (MCP增量) ──┐
Task 4 (标题优化)   ──┤                      ├── Task 8 (E2E验证)
Task 5 (enter_session)┘                      │
                                              ┘
```

- Tasks 1-5 可以并行执行（无互相依赖）
- Task 6 依赖 Task 3（使用 SQLite 索引）
- Task 7 依赖 Tasks 1, 2（验证 plugin 结构）
- Task 8 依赖所有前置 Tasks

## 未来工作（不在本计划范围）

- **分发形态**: `pip install ccsm` + npm wrapper for Claude Code plugin marketplace
- **TUI 性能优化**: JSONL 解析缓存到 SQLite，TUI 从 DB 加载而非重新解析
- **Hook 深度集成**: PostToolUse 追踪代码变更、UserPromptSubmit 捕获意图
- **Resume 上下文注入**: 在 `claude --resume` 前自动注入断点摘要到 system prompt

