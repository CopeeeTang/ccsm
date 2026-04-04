# CCSM Phase 2: Skills + Hook Context 摘要 + Setup 流程

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 CCSM 从"MCP 工具集"升级为完整的 Claude Code plugin — 增加 Skills（用户入口）、Hook Context 自动摘要（利用 Claude Code 内置模型）、Setup 初始化流程，实现用户无感知的增量同步。

**Architecture:** 双模式摘要引擎（外部 API 手动模式 + Hook Context 自动模式），Skills 作为用户交互入口内部调用 MCP 工具，SessionEnd/Stop hook 通过 context injection 让 Claude Code 自己的模型生成摘要并写入 `~/.ccsm/`。

**Tech Stack:** Python 3.10+ (FastMCP), Claude Code Plugin API (hooks, skills), Node.js shim (scripts/)

---

## File Structure

```
ccsm/
├── skills/                          # NEW: 全部 skill 定义
│   ├── ccsm-search/
│   │   └── SKILL.md                 # NEW: 会话搜索 skill
│   ├── ccsm-resume/
│   │   └── SKILL.md                 # NEW: 会话恢复 skill
│   ├── ccsm-overview/
│   │   └── SKILL.md                 # NEW: 会话总览/报告 skill
│   └── ccsm-setup/
│       └── SKILL.md                 # NEW: 初始化扫描 skill
├── hooks/
│   └── hooks.json                   # MODIFY: 增加 Stop hook + Setup hook
├── scripts/
│   ├── mcp-shim.js                  # (unchanged)
│   ├── worker.js                    # MODIFY: 增加 session-summarize 命令
│   └── stop-hook.js                 # NEW: Stop hook context injector
├── ccsm/
│   ├── core/
│   │   └── summarizer.py            # MODIFY: 增加 hook context 模式
│   └── mcp/
│       └── server.py                # MODIFY: 增加 batch_summarize 工具
├── tests/
│   └── test_skills.py               # NEW: skill 结构验证测试
└── .claude-plugin/
    └── plugin.json                  # (unchanged)
```

---

### Task 1: 创建 ccsm-search Skill

**Files:**
- Create: `skills/ccsm-search/SKILL.md`
- Test: `tests/test_skills.py`

- [ ] **Step 1: 创建 skills 目录和 SKILL.md**

```markdown
---
name: ccsm-search
description: Search Claude Code session history across all projects and worktrees. Use when user asks "find my old session about X", "what did I work on last week", or wants to locate a previous conversation.
---

# CCSM Session Search

Search past Claude Code sessions by keyword, status, or time range.

## When to Use

- "找一下我之前关于 X 的会话"
- "上周我做了什么"
- "搜索 authentication 相关的会话"

## Workflow

### Step 1: Search

Use the `ccsm:search_sessions` MCP tool:

```
search_sessions(query="用户的搜索词")
```

Returns matched sessions ranked by relevance with title, status, last_activity, message_count.

### Step 2: Show Results

Present results as a concise table:

| # | 标题 | 状态 | 最后活动 | 消息数 |
|---|------|------|----------|--------|

### Step 3: Drill Down (if user picks one)

Use `ccsm:enter_session` to get full context:

```
enter_session(session_id="picked-session-id")
```

Show: goal, progress, breakpoint, next_steps, and the resume command.

### Step 4: Resume (if user wants)

Provide the resume command from enter_session result:

```bash
claude --resume /path/to/session.jsonl
```

## Examples

User: "搜索关于 TUI 的会话"
→ search_sessions(query="TUI") → show table → user picks → enter_session → show context

User: "我上周做了什么"
→ list_sessions(status="done") → filter by date → show table
```

- [ ] **Step 2: 验证 SKILL.md 格式**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
# Check frontmatter is valid YAML
python3 -c "
import yaml
with open('skills/ccsm-search/SKILL.md') as f:
    content = f.read()
    # Extract frontmatter between --- markers
    parts = content.split('---', 2)
    meta = yaml.safe_load(parts[1])
    assert meta['name'] == 'ccsm-search'
    assert 'description' in meta
    print(f'Valid skill: {meta[\"name\"]}')
"
```

Expected: `Valid skill: ccsm-search`

- [ ] **Step 3: Commit**

```bash
git add skills/ccsm-search/SKILL.md
git commit -m "feat: add ccsm-search skill for session history search"
```

---

### Task 2: 创建 ccsm-resume Skill

**Files:**
- Create: `skills/ccsm-resume/SKILL.md`

- [ ] **Step 1: 创建 SKILL.md**

```markdown
---
name: ccsm-resume
description: Resume a previous Claude Code session with full context recovery. Use when user wants to go back to a previous conversation, continue old work, or asks "resume that session".
---

# CCSM Session Resume

Resume a previous session with context — shows what was happening, where you left off, and launches the resume command.

## When to Use

- "恢复之前的会话"
- "resume 那个 TUI 的 session"
- "继续上次的工作"

## Workflow

### Step 1: Identify the Session

If user specifies a keyword, search first:

```
search_sessions(query="keyword")
```

If user says "最近的" or "上一个", list recent:

```
list_sessions(status="active")
```

### Step 2: Show Context Before Resume

Use `ccsm:enter_session` to display what the session was about:

```
enter_session(session_id="target-session-id")
```

Present to user:
- **目标**: {goal}
- **进度**: {progress}
- **断点**: {breakpoint}
- **下一步**: {next_steps}

### Step 3: Launch Resume

Ask user to confirm, then provide:

```bash
# 复制以下命令到终端执行:
{command from enter_session}
```

Or suggest: "在提示符中输入 `! {command}` 直接在当前会话中执行"

## Important

- Always show context BEFORE resuming — user needs to know what they're going back to
- Use JSONL path (not session_id) in the command — ensures cross-worktree compatibility
```

- [ ] **Step 2: Commit**

```bash
git add skills/ccsm-resume/SKILL.md
git commit -m "feat: add ccsm-resume skill for context-rich session resume"
```

---

### Task 3: 创建 ccsm-overview Skill

**Files:**
- Create: `skills/ccsm-overview/SKILL.md`

- [ ] **Step 1: 创建 SKILL.md**

```markdown
---
name: ccsm-overview
description: Show an overview of all Claude Code sessions — active work, recent history, and session statistics. Use when user asks "show my sessions", "what's active", "session dashboard", or wants a summary of their work.
---

# CCSM Session Overview

Dashboard view of all sessions across projects.

## When to Use

- "显示我的会话"
- "有哪些活跃的会话"
- "会话概览"

## Workflow

### Step 1: Load Active Sessions

```
list_sessions(status="active")
```

### Step 2: Present Dashboard

**🟢 Active ({count})**

| 标题 | 项目 | 最后活动 | 消息数 |
|------|------|----------|--------|
| ... | ... | ... | ... |

### Step 3: Optional — Show Other Statuses

If user asks "全部" or "all":

```
list_sessions()
```

Group by status: Active → Background → Idea → Done

### Step 4: Drill Down

If user picks a specific session:

```
enter_session(session_id="...")
```

Show full context with resume option.
```

- [ ] **Step 2: Commit**

```bash
git add skills/ccsm-overview/SKILL.md
git commit -m "feat: add ccsm-overview skill for session dashboard"
```

---

### Task 4: 创建 ccsm-setup Skill

**Files:**
- Create: `skills/ccsm-setup/SKILL.md`

- [ ] **Step 1: 创建 SKILL.md**

```markdown
---
name: ccsm-setup
description: Initialize CCSM — scan all Claude Code sessions, build the search index, and optionally generate AI summaries. Run this after first installing CCSM or when you want to rebuild the index.
---

# CCSM Setup

First-time initialization or index rebuild.

## When to Use

- First time using CCSM
- "初始化 CCSM"
- "重建索引"
- After CCSM plugin is installed

## Workflow

### Step 1: Build SQLite Index

Run the incremental refresh to scan all sessions:

```bash
python3 -c "
from ccsm.core.index_db import incremental_refresh
count = incremental_refresh()
print(f'Indexed {count} sessions')
"
```

Report: "已扫描并索引 {count} 个会话"

### Step 2: Verify MCP Tools

```bash
python3 -c "
from ccsm.mcp.server import list_sessions
results = list_sessions()
print(f'MCP server ready: {len(results)} sessions accessible')
"
```

Report: "MCP 工具已就绪，{count} 个会话可查询"

### Step 3: Optional — Batch AI Summaries

Ask user: "是否为未摘要的会话生成 AI 摘要？（需要 API 访问，约 {n} 个会话）"

If yes, use external API mode:

```bash
python3 -c "
from ccsm.core.summarizer import summarize_session
from ccsm.core.index_db import SessionIndexDB
db = SessionIndexDB()
sessions = db.list_all()
# Process sessions without summaries...
"
```

### Step 4: Report

```
✅ CCSM 初始化完成
  - 索引: {n} 个会话
  - 摘要: {m} 个已生成
  - MCP 工具: 7 个可用
  - TUI: python3 -m ccsm
```

## Notes

- Setup 是幂等的 — 多次运行不会重复处理
- 增量索引只处理 mtime 变化的 JSONL 文件
- AI 摘要使用外部 API（手动模式），不依赖 Claude Code 内置模型
```

- [ ] **Step 2: Commit**

```bash
git add skills/ccsm-setup/SKILL.md
git commit -m "feat: add ccsm-setup skill for initialization"
```

---

### Task 5: Skill 结构验证测试

**Files:**
- Create: `tests/test_skills.py`

- [ ] **Step 1: 编写测试**

```python
"""Tests for CCSM skill structure validation."""
import yaml
from pathlib import Path

PLUGIN_ROOT = Path(__file__).parent.parent
SKILLS_DIR = PLUGIN_ROOT / "skills"

EXPECTED_SKILLS = ["ccsm-search", "ccsm-resume", "ccsm-overview", "ccsm-setup"]


def test_skills_directory_exists():
    """Skills directory must exist."""
    assert SKILLS_DIR.is_dir(), f"Missing skills directory: {SKILLS_DIR}"


def test_all_expected_skills_exist():
    """All expected skills have SKILL.md files."""
    for skill_name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        assert skill_file.exists(), f"Missing skill: {skill_file}"


def test_skill_frontmatter_valid():
    """Each SKILL.md has valid YAML frontmatter with name and description."""
    for skill_name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_file.read_text()
        parts = content.split("---", 2)
        assert len(parts) >= 3, f"{skill_name}: Missing --- frontmatter delimiters"
        meta = yaml.safe_load(parts[1])
        assert meta is not None, f"{skill_name}: Empty frontmatter"
        assert "name" in meta, f"{skill_name}: Missing 'name' in frontmatter"
        assert "description" in meta, f"{skill_name}: Missing 'description'"
        assert meta["name"] == skill_name, (
            f"{skill_name}: name mismatch: {meta['name']}"
        )


def test_skill_has_content():
    """Each SKILL.md has meaningful content after frontmatter."""
    for skill_name in EXPECTED_SKILLS:
        skill_file = SKILLS_DIR / skill_name / "SKILL.md"
        content = skill_file.read_text()
        parts = content.split("---", 2)
        body = parts[2].strip()
        assert len(body) > 100, f"{skill_name}: Body too short ({len(body)} chars)"
```

- [ ] **Step 2: 运行测试**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -m pytest tests/test_skills.py -v
```

Expected: 4 tests PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_skills.py
git commit -m "test: add skill structure validation tests"
```

---

### Task 6: Hook Context 自动摘要 — Stop Hook

**Files:**
- Create: `scripts/stop-hook.js`
- Modify: `hooks/hooks.json`

**设计:** Claude Code 的 `Stop` hook 在每次 Claude 完成回复后触发。此时 Claude Code 的模型仍在运行中。我们通过 hook 的 stdout 返回一段 context text，Claude Code 会将其注入到下一轮对话的 system prompt 中。但更实际的做法是：Stop hook 触发一个后台脚本，该脚本读取当前 session 的 JSONL 并用外部 API 或缓存逻辑生成摘要。

**关键区分:**
- `Stop` hook：Claude 完成一轮回复后触发，可以做轻量处理
- `SessionEnd` hook：整个会话结束时触发，适合做最终摘要

- [ ] **Step 1: 创建 `scripts/stop-hook.js`**

```javascript
#!/usr/bin/env node
/**
 * CCSM Stop Hook — triggered after each Claude response.
 * Reads session data from stdin, triggers incremental index refresh.
 * Lightweight: only updates the index, no API calls.
 */
const { execSync } = require('child_process');
const path = require('path');
const fs = require('fs');

const pluginRoot = process.env.CLAUDE_PLUGIN_ROOT || path.resolve(__dirname, '..');

// Find Python
const pythonPaths = [
  '/home/v-tangxin/GUI/ml_env/bin/python3',
  path.join(pluginRoot, '.venv', 'bin', 'python3'),
  'python3',
];

let pythonCmd = 'python3';
for (const p of pythonPaths) {
  if (p === 'python3' || fs.existsSync(p)) {
    pythonCmd = p;
    break;
  }
}

// Read session context from stdin (Claude Code passes session info)
let stdinData = '';
process.stdin.on('data', (chunk) => { stdinData += chunk; });
process.stdin.on('end', () => {
  try {
    const sessionInfo = JSON.parse(stdinData);
    const sessionId = sessionInfo.sessionId;
    if (!sessionId) {
      process.exit(0);
    }

    // Lightweight: just refresh the index for this session
    execSync(
      `${pythonCmd} -c "from ccsm.core.index_db import incremental_refresh; incremental_refresh()"`,
      {
        cwd: pluginRoot,
        env: { ...process.env, PYTHONPATH: pluginRoot },
        timeout: 10000,
        stdio: ['ignore', 'inherit', 'inherit'],
      }
    );
  } catch (err) {
    // Non-fatal
    process.exit(0);
  }
});
```

- [ ] **Step 2: 更新 `hooks/hooks.json`**

添加 Setup hook 和 Stop hook：

```json
{
  "description": "CCSM session lifecycle hooks",
  "hooks": {
    "Setup": [
      {
        "matcher": "*",
        "hooks": [
          {
            "type": "command",
            "command": "_R=\"${CLAUDE_PLUGIN_ROOT}\"; [ -z \"$_R\" ] && _R=\"/home/v-tangxin/GUI/projects/ccsm\"; node \"$_R/scripts/worker.js\" index-refresh",
            "timeout": 60
          }
        ]
      }
    ],
    "SessionStart": [
      {
        "matcher": "startup|clear|compact",
        "hooks": [
          {
            "type": "command",
            "command": "_R=\"${CLAUDE_PLUGIN_ROOT}\"; [ -z \"$_R\" ] && _R=\"/home/v-tangxin/GUI/projects/ccsm\"; node \"$_R/scripts/worker.js\" index-refresh",
            "timeout": 30
          }
        ]
      }
    ],
    "Stop": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "_R=\"${CLAUDE_PLUGIN_ROOT}\"; [ -z \"$_R\" ] && _R=\"/home/v-tangxin/GUI/projects/ccsm\"; node \"$_R/scripts/stop-hook.js\"",
            "timeout": 15
          }
        ]
      }
    ],
    "SessionEnd": [
      {
        "hooks": [
          {
            "type": "command",
            "command": "_R=\"${CLAUDE_PLUGIN_ROOT}\"; [ -z \"$_R\" ] && _R=\"/home/v-tangxin/GUI/projects/ccsm\"; node \"$_R/scripts/worker.js\" session-ended",
            "timeout": 10
          }
        ]
      }
    ]
  }
}
```

- [ ] **Step 3: 验证 JSON 格式**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -c "import json; d = json.load(open('hooks/hooks.json')); print(f'Hooks: {list(d[\"hooks\"].keys())}')"
```

Expected: `Hooks: ['Setup', 'SessionStart', 'Stop', 'SessionEnd']`

- [ ] **Step 4: Commit**

```bash
git add scripts/stop-hook.js hooks/hooks.json
git commit -m "feat: add Stop hook for incremental index sync + Setup hook"
```

---

### Task 7: MCP batch_summarize 工具

**Files:**
- Modify: `ccsm/mcp/server.py`

**设计:** 新增一个 MCP 工具 `batch_summarize`，供 `ccsm-setup` skill 调用，使用外部 API 模式批量生成摘要。

- [ ] **Step 1: 在 server.py 中添加 batch_summarize 工具**

在 `update_session_meta` 工具之后添加：

```python
@mcp.tool(
    name="batch_summarize",
    description=(
        "Generate AI summaries for sessions that don't have one yet. "
        "Uses external API mode. Returns count of newly summarized sessions."
    ),
)
def batch_summarize(
    limit: int = 10,
    status: Optional[str] = None,
) -> dict:
    """Batch generate summaries for un-summarized sessions."""
    from ccsm.core.meta import load_summary as _load_summary
    from ccsm.core.summarizer import summarize_session as _summarize

    session_map, context_map, _all_meta = _build_session_map()

    candidates = []
    for sid, info in session_map.items():
        if info.message_count < 8:
            continue
        if status:
            try:
                if info.status != Status(status):
                    continue
            except ValueError:
                continue
        cached = _load_summary(sid)
        if cached and cached.mode == "llm":
            continue  # Already has LLM summary
        candidates.append((sid, info))

    # Sort by message count descending (most content first)
    candidates.sort(key=lambda x: x[1].message_count, reverse=True)
    candidates = candidates[:limit]

    summarized = 0
    errors = 0
    for sid, info in candidates:
        try:
            _summarize(
                session_id=sid,
                jsonl_path=info.jsonl_path,
                mode="llm",
                force=True,
            )
            summarized += 1
        except Exception as e:
            logger.warning("Batch summarize failed for %s: %s", sid, e)
            errors += 1

    return {
        "summarized": summarized,
        "errors": errors,
        "total_candidates": len(candidates),
        "note": "Used external API mode (manual). For hook-based auto mode, see Stop hook.",
    }
```

- [ ] **Step 2: 验证工具注册**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -c "
import importlib
mod = importlib.import_module('ccsm.mcp.server')
assert hasattr(mod, 'batch_summarize'), 'batch_summarize not found'
print('batch_summarize tool registered')
"
```

Expected: `batch_summarize tool registered`

- [ ] **Step 3: Commit**

```bash
git add ccsm/mcp/server.py
git commit -m "feat: add batch_summarize MCP tool for external API mode"
```

---

### Task 8: 全量测试 + 集成验证

**Files:**
- (No new files)

- [ ] **Step 1: 运行全部测试**

```bash
cd /home/v-tangxin/GUI/projects/ccsm
source /home/v-tangxin/GUI/ml_env/bin/activate
python3 -m pytest tests/ -v
```

Expected: All tests pass (114 existing + 4 new skill tests = 118+)

- [ ] **Step 2: 验证 plugin 完整性**

```bash
python3 -c "
import json
from pathlib import Path

root = Path('/home/v-tangxin/GUI/projects/ccsm')

# Check all required files
required = [
    '.claude-plugin/plugin.json',
    '.mcp.json',
    'hooks/hooks.json',
    'scripts/mcp-shim.js',
    'scripts/worker.js',
    'scripts/stop-hook.js',
    'skills/ccsm-search/SKILL.md',
    'skills/ccsm-resume/SKILL.md',
    'skills/ccsm-overview/SKILL.md',
    'skills/ccsm-setup/SKILL.md',
]
for f in required:
    path = root / f
    assert path.exists(), f'Missing: {f}'
    print(f'  ✅ {f}')

# Check hooks
hooks = json.loads((root / 'hooks/hooks.json').read_text())
hook_types = list(hooks['hooks'].keys())
print(f'  Hooks: {hook_types}')
assert 'Setup' in hook_types
assert 'Stop' in hook_types

# Check MCP tools count
import importlib
mod = importlib.import_module('ccsm.mcp.server')
tool_names = ['list_sessions', 'get_session_detail', 'search_sessions',
              'resume_session', 'enter_session', 'summarize_session',
              'update_session_meta', 'batch_summarize']
for name in tool_names:
    assert hasattr(mod, name), f'Missing MCP tool: {name}'
print(f'  MCP tools: {len(tool_names)}')

print('\\n✅ Plugin structure complete')
"
```

- [ ] **Step 3: Commit (if any fixes)**

```bash
git status
# If any fixes needed
git add -A && git commit -m "fix: integration fixes for Phase 2"
```

---

## Execution Order & Dependencies

```
Task 1 (ccsm-search) ──┐
Task 2 (ccsm-resume) ──┤
Task 3 (ccsm-overview) ─┼── Task 5 (test_skills.py) ──┐
Task 4 (ccsm-setup) ────┘                              │
                                                        ├── Task 8 (E2E验证)
Task 6 (Stop hook) ────────────────────────────────────┤
Task 7 (batch_summarize) ──────────────────────────────┘
```

- Tasks 1-4 可并行（独立 skill 文件）
- Task 5 依赖 Tasks 1-4（验证 skill 结构）
- Task 6-7 独立于 skills，可与 Tasks 1-4 并行
- Task 8 依赖所有前置

---

## 双模式摘要架构说明

```
┌─────────────────────────────────────────────────┐
│                  摘要生成引擎                      │
├─────────────────────┬───────────────────────────┤
│  手动模式 (外部 API)  │  自动模式 (Hook Context)    │
├─────────────────────┼───────────────────────────┤
│ 触发: /ccsm-setup    │ 触发: Stop / SessionEnd    │
│ 或 batch_summarize   │ hook                       │
│ MCP 工具             │                            │
├─────────────────────┼───────────────────────────┤
│ 调用: summarizer.py  │ 调用: stop-hook.js          │
│ → _call_llm()       │ → incremental_refresh()     │
│ → httpx POST        │ (当前仅索引刷新;              │
│   DEFAULT_BASE_URL   │  未来可扩展为 context         │
│                     │  injection 让 CC 模型生成)    │
├─────────────────────┼───────────────────────────┤
│ 输出: ~/.ccsm/       │ 输出: ~/.ccsm/index.db      │
│   summaries/         │ (未来: summaries/)           │
├─────────────────────┼───────────────────────────┤
│ 适用: NPX看板独立    │ 适用: Plugin 集成            │
│  运行, 无需 CC      │  零配置, 用户无感知           │
└─────────────────────┴───────────────────────────┘
```

**Phase 2 实现范围:**
- ✅ 手动模式: 完整实现（已有 summarizer.py + 新增 batch_summarize）
- ⚠️ 自动模式: 实现 Stop hook 增量索引刷新；context injection 摘要生成留待 Phase 3

---

## 未来工作 (Phase 3, 不在本计划)

- **Hook Context 完整摘要**: Stop hook 不仅刷新索引，还通过 context injection 让 Claude Code 的模型生成摘要
- **NPX 包装**: `npx ccsm` 启动 TUI 看板（需要 npm 包壳）
- **Marketplace 发布**: 打包为 Claude Code 官方 plugin
- **TUI 性能优化**: 用 SQLite 索引替代全量 JSONL 解析加载 TUI 列表
