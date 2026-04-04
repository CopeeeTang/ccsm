---
name: ccsm-search
description: Search and locate past Claude Code sessions across all projects and worktrees. Use this skill whenever the user mentions finding old sessions, searching conversation history, locating previous work, asking "what did I work on", "find my session about X", "之前做过什么", "找回会话", "搜索历史", "哪个会话在做X", or any reference to past Claude Code conversations they want to find. Also use when user asks about work from a specific time period ("last week", "yesterday", "上周").
---

# CCSM Session Search

Search past Claude Code sessions by keyword, status, or time range. Uses the CCSM MCP tools to query a persistent index of all sessions.

## Workflow

### Step 1: Search

Call the CCSM search tool with the user's query:

```
mcp__ccsm__search_sessions(query="用户的搜索词")
```

If the user asks about a time period rather than a keyword, list by status instead:

```
mcp__ccsm__list_sessions(status="active")
```

### Step 2: Present Results

Show results as a concise table, sorted by relevance:

| # | 标题 | 状态 | 项目 | 最后活动 | 消息数 |
|---|------|------|------|----------|--------|

- Show relative time ("2h ago", "昨天") instead of ISO timestamps
- Truncate titles longer than 40 chars with ellipsis
- If no results, suggest broader search terms

### Step 3: Drill Down

When the user picks a session, fetch full context:

```
mcp__ccsm__enter_session(session_id="picked-session-id")
```

Present the recovery context:
- **目标**: {goal}
- **进度**: {progress}
- **断点**: {breakpoint}
- **下一步**: {next_steps}
- **Resume 命令**: {command}

### Step 4: Resume (optional)

If user wants to resume, provide the command:

```bash
# 在提示符中输入:
! claude --resume /path/to/session.jsonl
```

The `!` prefix runs the command in the current terminal session.

## Error Handling

- **Empty search results**: "没有找到匹配的会话。试试更宽泛的关键词，或用 `/ccsm-overview` 查看所有会话。"
- **MCP 连接失败**: "CCSM MCP server 未连接。确认 `/mcp` 中 ccsm 状态正常。"

## Examples

**Example 1:**
User: "找一下我之前关于 TUI 布局的会话"
-> `mcp__ccsm__search_sessions(query="TUI 布局")` -> show table with matched sessions

**Example 2:**
User: "上周我在 GUI 项目做了什么"
-> `mcp__ccsm__search_sessions(query="GUI")` -> filter results by last_activity within last week

**Example 3:**
User: "搜索 authentication 相关的会话"
-> `mcp__ccsm__search_sessions(query="authentication")` -> show matches -> user picks one -> `mcp__ccsm__enter_session(session_id=...)` -> show context
