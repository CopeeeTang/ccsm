---
name: ccsm-setup
description: Initialize or rebuild the CCSM session index. Scans all Claude Code sessions, builds a SQLite search index, and optionally generates AI summaries. Use when first installing CCSM, after "初始化 CCSM", "rebuild index", "重建索引", "setup ccsm", or when CCSM tools return empty results suggesting the index needs building.
---

# CCSM Setup

First-time initialization or index rebuild for the Claude Code Session Manager.

## When This Is Needed

- First time using CCSM (no `~/.ccsm/index.db` exists)
- After a long period without using CCSM (index may be stale)
- When `ccsm-search` or `ccsm-overview` return empty/stale results
- User explicitly asks to rebuild

## Workflow

### Step 1: Build SQLite Index

Scan all Claude Code sessions and build the persistent index:

```bash
python3 -c "
import sys; sys.path.insert(0, '/home/v-tangxin/GUI/projects/ccsm')
from ccsm.core.index_db import incremental_refresh
count = incremental_refresh()
print(f'Indexed {count} sessions')
"
```

Report: "已扫描并索引 {count} 个会话到 ~/.ccsm/index.db"

### Step 2: Verify MCP Tools

Confirm the MCP server can access the indexed data:

```
mcp__ccsm__list_sessions()
```

Report: "MCP 工具已就绪，{len(results)} 个会话可查询"

### Step 3: Summary Statistics

Show the user what was found:

```
Setup complete:
  - 索引: {n} 个会话
  - Active: {active_count}
  - Done: {done_count}
  - 项目: {project_count} 个
  - MCP 工具: 8 个可用 (list/search/enter/resume/detail/summarize/update/batch)
  - Skills: /ccsm-search, /ccsm-resume, /ccsm-overview
```

### Step 4: Optional — Batch AI Summaries

Ask: "是否为未摘要的 active 会话生成 AI 摘要？（需要 API，约处理 {n} 个会话）"

If yes:

```
mcp__ccsm__batch_summarize(limit=10, status="active")
```

Report results: "{summarized} 个会话已生成摘要，{errors} 个失败"

## Notes

- Setup is idempotent — running multiple times only processes changed files (mtime-based incremental)
- The SQLite index is at `~/.ccsm/index.db` (~1.3MB for 3000 sessions)
- AI summaries use external API mode and are cached in `~/.ccsm/summaries/`
