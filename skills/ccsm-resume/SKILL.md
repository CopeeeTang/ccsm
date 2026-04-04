---
name: ccsm-resume
description: Resume a previous Claude Code session with full context preview before entering. Unlike the built-in /resume which shows a simple picker, this skill first displays what the session was about (goal, progress, breakpoint, next steps) so you know exactly what you're going back to. Use whenever user says "resume session", "继续上次", "回到之前的会话", "恢复工作", "pick up where I left off", "re-enter that conversation", or wants to return to any previous Claude Code session with context awareness.
---

# CCSM Session Resume

Resume a previous session with context preview — shows what was happening, where you left off, and what to do next, BEFORE launching the resume command.

**Key difference from built-in `/resume`**: CCSM shows goal/progress/breakpoint/next-steps context first, so you know what you're going back to.

## Workflow

### Step 1: Identify the Session

If user specifies a keyword or topic:

```
mcp__ccsm__search_sessions(query="keyword")
```

If user says "最近的", "上一个", or "last session":

```
mcp__ccsm__list_sessions(status="active")
```

Present a short list for user to pick from.

### Step 2: Show Context Before Resume

Fetch full context for the selected session:

```
mcp__ccsm__enter_session(session_id="target-session-id")
```

Present the recovery summary:

**Session: {title}**
- **目标**: {goal or digest.goal}
- **进度**: {progress or digest.progress}
- **断点**: {breakpoint.detail}
- **下一步**: {next_steps}
- **状态**: {status} | **消息数**: {message_count}

If there is a `last_reply_snippet`, show it under "最后回复" as additional context.

### Step 3: Confirm and Launch

Ask: "确认恢复这个会话？"

If confirmed, provide:

```bash
# 在提示符中输入以下命令:
! {command from enter_session}
```

The `!` prefix runs the command directly in the current terminal session, launching Claude Code with the previous conversation restored.

## Important

- Always show context BEFORE resuming — the user needs to know what they're going back to. This is CCSM's core value-add over bare `/resume`.
- The resume command uses JSONL file path (not session_id) to ensure cross-worktree compatibility. This is already handled by `enter_session`.

## Examples

**Example 1:**
User: "帮我恢复上次做 CCSM 插件的那个会话"
-> `mcp__ccsm__search_sessions(query="CCSM 插件")` -> show matches -> user picks -> `mcp__ccsm__enter_session(...)` -> show context -> user confirms -> provide `! claude --resume /path/to/session.jsonl`

**Example 2:**
User: "继续上次的工作"
-> `mcp__ccsm__list_sessions(status="active")` -> show most recent -> `mcp__ccsm__enter_session(...)` -> show context -> confirm -> resume
