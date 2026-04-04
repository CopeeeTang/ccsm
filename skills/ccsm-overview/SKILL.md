---
name: ccsm-overview
description: Show a dashboard overview of all Claude Code sessions — active work, recent history, session counts, and status breakdown across projects. Use whenever the user asks "show my sessions", "what's active", "会话列表", "session dashboard", "有哪些活跃任务", "我在做什么", "list all sessions", "会话概览", "project status", or wants to see a summary of their Claude Code work across all projects and worktrees.
---

# CCSM Session Overview

Dashboard view of all Claude Code sessions across projects, grouped by status.

## Workflow

### Step 1: Load Sessions

Start with active sessions (most relevant):

```
mcp__ccsm__list_sessions(status="active")
```

### Step 2: Present Dashboard

Format as a clean dashboard:

**Active ({count})**

| 标题 | 项目/Worktree | 最后活动 | 消息数 |
|------|--------------|----------|--------|

- Show relative time ("2h ago", "昨天") instead of raw timestamps
- Truncate titles to 40 chars
- Group by project if more than 8 sessions

### Step 3: Expand (if user wants more)

If user asks "全部", "all statuses", or "包括已完成的":

```
mcp__ccsm__list_sessions()
```

Group by status with section headers:

**Active ({n})** — needs active engagement
| ... |

**Background ({n})** — long-running tasks
| ... |

**Done ({n})** — completed (show recent 5 only)
| ... |

### Step 4: Drill Down

When user picks a specific session:

```
mcp__ccsm__enter_session(session_id="...")
```

Show full context and offer resume option.

## Presentation Guidelines

- Sort by last_activity descending within each status group
- Active sessions always shown first
- For large session counts (>20), show top 10 per status with "...and {n} more"
- If a session is currently running, mark it with a indicator

## Error Handling

- **No sessions found**: "没有发现任何 Claude Code 会话。可能需要先运行 `/ccsm-setup` 建立索引。"
- **MCP 未连接**: "CCSM MCP server 未连接。检查 `/mcp` 确认 ccsm 状态。"

## Examples

**Example 1:**
User: "我现在有哪些活跃的会话"
-> `mcp__ccsm__list_sessions(status="active")` -> show active sessions table

**Example 2:**
User: "看看所有会话的概览"
-> `mcp__ccsm__list_sessions()` -> group by status -> show full dashboard

**Example 3:**
User: "GUI 项目有哪些会话"
-> `mcp__ccsm__search_sessions(query="GUI")` -> show filtered results
