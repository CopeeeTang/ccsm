"""LLM-powered session summarizer for generating milestone timelines.

Uses a local Anthropic-compatible API to analyze conversation history
and produce structured milestones + breakpoint data for the Detail panel.

Two modes:
  - extract: Rule-based extraction from milestones.py (zero cost, instant)
  - llm: API call to Claude for higher-quality semantic understanding

API configuration:
  - base_url: http://127.0.0.1:14142 (local proxy, configurable)
  - api_key: sk-dummy (placeholder for local proxy)
  - model: claude-sonnet-4.6 (configurable)

Usage:
  from ccsm.core.summarizer import summarize_session
  summary = summarize_session(session_id, jsonl_path, mode="llm")
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from ccsm.core.i18n import get_prompts
from ccsm.core.meta import load_summary, save_summary
from ccsm.core.milestones import extract_breakpoint, extract_milestones
from ccsm.core.parser import parse_session_messages
from ccsm.models.session import (
    Breakpoint,
    JSONLMessage,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
    SessionDigest,
    SessionFact,
    SessionSummary,
)

logger = logging.getLogger(__name__)

# ─── API Configuration ──────────────────────────────────────────────────────
# These can later be loaded from ~/.ccsm/config.toml

DEFAULT_BASE_URL = "http://127.0.0.1:4142"
DEFAULT_API_KEY = "sk-dummy"
DEFAULT_MODEL = "claude-haiku-4.5"  # Cheapest & fastest, sufficient for summarization
DEFAULT_MAX_TOKENS = 2048

# ─── Prompt Template ────────────────────────────────────────────────────────

_SYSTEM_PROMPT = """\
You are a session analyst. Given a conversation between a user and Claude Code, \
extract a structured timeline of key milestones and identify where the user left off.

Rules:
- Only extract STRUCTURAL turning points, NOT every message
- A milestone is a phase transition: discussion→plan, plan→execution, execution→review, etc.
- Each milestone gets a short label (≤20 chars) and one-line detail (≤50 chars)
- The last active milestone should have sub_items listing specific topics discussed
- The breakpoint identifies exactly where the user stopped
- 默认使用中文输出所有文本内容，专有名词（如 API、Redis、Kubernetes）保留英文

Output ONLY valid JSON matching this schema — no markdown, no explanation:
{
  "description": "One-sentence summary of the entire session",
  "milestones": [
    {
      "label": "short phase name",
      "detail": "one-line description of what happened",
      "status": "done|wip|pending",
      "sub_items": [
        {"label": "sub-topic A", "status": "done|wip|pending"}
      ]
    }
  ],
  "breakpoint": {
    "milestone_label": "which milestone was active",
    "detail": "what specifically was being discussed",
    "last_topic": "the exact topic at interruption point"
  },
  "key_insights": ["insight 1", "insight 2"]
}"""

_USER_PROMPT_TEMPLATE = """\
Analyze this conversation and extract milestones. The conversation has {msg_count} messages \
over {duration}. Here are the messages (user messages marked [U], assistant marked [A]):

{conversation}

Remember: output ONLY valid JSON, no markdown fences, no explanation."""


# ─── Message formatting ────────────────────────────────────────────────────


def _format_messages_for_prompt(
    messages: list[JSONLMessage],
    max_chars: int = 12000,
) -> str:
    """Format conversation messages for the LLM prompt.

    Compresses messages to fit within token budget:
    - User messages: keep first 200 chars (these drive milestones)
    - Assistant messages: keep first 100 chars (context only)
    - Skip tool_use heavy messages (just show "[tool usage]")
    """
    lines: list[str] = []
    total_chars = 0

    for i, msg in enumerate(messages):
        prefix = "[U]" if msg.role == "user" else "[A]"
        text = msg.content.strip()

        if not text:
            continue

        # Compress
        if msg.role == "user":
            if len(text) > 200:
                text = text[:197] + "…"
        else:
            # Assistant messages: just first meaningful line
            first_line = ""
            for line in text.split("\n"):
                line = line.strip()
                if line and len(line) > 5 and not line.startswith(("```", "─", "═", "###")):
                    first_line = line
                    break
            text = first_line[:100] if first_line else "(tool usage / code output)"

        line = f"{prefix} {text}"
        if total_chars + len(line) > max_chars:
            lines.append(f"... ({len(messages) - i} more messages)")
            break

        lines.append(line)
        total_chars += len(line) + 1

    return "\n".join(lines)


def _format_duration(messages: list[JSONLMessage]) -> str:
    """Format session duration from first to last message."""
    if not messages:
        return "unknown"
    first = messages[0].timestamp
    last = messages[-1].timestamp
    delta = (last - first).total_seconds()
    if delta < 60:
        return f"{int(delta)}s"
    if delta < 3600:
        return f"{int(delta / 60)}m"
    hours = int(delta / 3600)
    mins = int((delta % 3600) / 60)
    return f"{hours}h {mins}m"


# ─── LLM call ──────────────────────────────────────────────────────────────


def _call_llm(
    messages: list[JSONLMessage],
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
    max_tokens: int = DEFAULT_MAX_TOKENS,
) -> Optional[dict]:
    """Call the Anthropic-compatible API to generate a structured summary.

    Returns parsed JSON dict on success, None on failure.
    """
    try:
        import anthropic
    except ImportError:
        logger.warning("anthropic SDK not installed. Run: pip install anthropic")
        return None

    conversation = _format_messages_for_prompt(messages)
    duration = _format_duration(messages)

    prompts = get_prompts()
    user_prompt = prompts.milestones_user.format(
        msg_count=len(messages),
        duration=duration,
        conversation=conversation,
    )

    try:
        client = anthropic.Anthropic(
            base_url=base_url,
            api_key=api_key,
        )
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=prompts.milestones_system,
            messages=[{"role": "user", "content": user_prompt}],
        )

        raw_text = response.content[0].text.strip()

        # Strip markdown fences if the model wrapped its output
        if raw_text.startswith("```"):
            raw_text = raw_text.split("\n", 1)[1] if "\n" in raw_text else raw_text[3:]
        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

        return json.loads(raw_text)

    except anthropic.APIConnectionError:
        logger.warning("LLM API not reachable at %s", base_url)
        return None
    except (json.JSONDecodeError, IndexError, KeyError) as e:
        logger.warning("Failed to parse LLM response: %s", e)
        return None
    except Exception as e:
        logger.warning("LLM summarization failed: %s", e)
        return None


# ─── Response parsing ───────────────────────────────────────────────────────


_STATUS_MAP = {
    "done": MilestoneStatus.DONE,
    "wip": MilestoneStatus.IN_PROGRESS,
    "pending": MilestoneStatus.PENDING,
    "in_progress": MilestoneStatus.IN_PROGRESS,
}


def _parse_llm_response(data: dict) -> tuple[
    Optional[str],                    # description
    list[Milestone],                  # milestones
    Optional[Breakpoint],             # breakpoint
    list[str],                        # key_insights
]:
    """Parse LLM JSON response into typed data models."""
    description = data.get("description")
    key_insights = data.get("key_insights", [])

    # Parse milestones
    milestones: list[Milestone] = []
    for ms_data in data.get("milestones", []):
        sub_items = []
        for si in ms_data.get("sub_items", []):
            sub_items.append(MilestoneItem(
                label=si.get("label", ""),
                status=_STATUS_MAP.get(si.get("status", "pending"), MilestoneStatus.PENDING),
            ))

        milestones.append(Milestone(
            label=ms_data.get("label", ""),
            detail=ms_data.get("detail"),
            status=_STATUS_MAP.get(ms_data.get("status", "pending"), MilestoneStatus.PENDING),
            sub_items=sub_items,
        ))

    # Parse breakpoint
    breakpoint = None
    bp_data = data.get("breakpoint")
    if bp_data:
        breakpoint = Breakpoint(
            milestone_label=bp_data.get("milestone_label", ""),
            detail=bp_data.get("detail", ""),
            sub_item_label=bp_data.get("sub_item_label"),
            last_topic=bp_data.get("last_topic"),
        )

    return description, milestones, breakpoint, key_insights


# ─── Public API ──────────────────────────────────────────────────────────────


def summarize_session(
    session_id: str,
    jsonl_path: Path,
    mode: str = "extract",
    force: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> SessionSummary:
    """Generate or load a session summary.

    Args:
        session_id: Session UUID
        jsonl_path: Path to the .jsonl file
        mode: "extract" (rule-based, free) or "llm" (API call)
        force: If True, regenerate even if cached
        base_url: Anthropic API base URL
        api_key: API key
        model: Model name

    Returns:
        SessionSummary with milestones and breakpoint populated
    """
    # Check cache first — only reuse if cached mode matches requested mode
    # (prevents extract cache from blocking LLM upgrade)
    if not force:
        cached = load_summary(session_id)
        if cached and cached.milestones and cached.mode == mode:
            return cached

    # Parse all messages
    messages = parse_session_messages(jsonl_path)
    if not messages:
        return SessionSummary(
            session_id=session_id,
            mode="extract",
            description="Empty session",
        )

    if mode == "llm":
        return _summarize_llm(session_id, messages, base_url, api_key, model)
    else:
        return _summarize_extract(session_id, messages)


def _summarize_extract(
    session_id: str,
    messages: list[JSONLMessage],
) -> SessionSummary:
    """Rule-based extraction — zero cost, instant."""
    milestones = extract_milestones(messages)
    breakpoint = extract_breakpoint(messages, milestones)

    summary = SessionSummary(
        session_id=session_id,
        mode="extract",
        milestones=milestones,
        breakpoint=breakpoint,
        generated_at=datetime.now(timezone.utc),
    )

    # Cache it
    save_summary(summary)
    return summary


def _summarize_llm(
    session_id: str,
    messages: list[JSONLMessage],
    base_url: str,
    api_key: str,
    model: str,
) -> SessionSummary:
    """LLM-powered summarization — higher quality, requires API."""
    llm_result = _call_llm(messages, base_url, api_key, model)

    if llm_result is None:
        # Fallback to extract mode if LLM fails
        logger.info("LLM failed, falling back to extract mode for %s", session_id)
        return _summarize_extract(session_id, messages)

    description, milestones, breakpoint, key_insights = _parse_llm_response(llm_result)

    summary = SessionSummary(
        session_id=session_id,
        mode="llm",
        description=description,
        milestones=milestones,
        breakpoint=breakpoint,
        key_insights=key_insights,
        generated_at=datetime.now(timezone.utc),
        model=model,
    )

    # Cache it
    save_summary(summary)
    return summary


# ─── AI Title Generation ─────────────────────────────────────────────────────


def _extract_json_object(text: str) -> Optional[dict]:
    """Extract the first JSON object from text using bracket-depth matching.

    Unlike regex ``\\{[^{}]+\\}``, this correctly handles values that
    contain braces (e.g., ``{"title": "Fix {Auth} Bug"}``).
    """
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start : i + 1])
                except json.JSONDecodeError:
                    return None
    return None

_TITLE_PROMPT_TEMPLATE = """\
Based on this conversation excerpt, generate:
1. A short title (≤8 Chinese characters) — the core task action
2. A one-line intent summary (≤20 Chinese characters) — the user's original request

Rules:
- 默认使用中文，专有名词（API、Redis、Claude Code 等）保留英文
- Title should be like "重构数据库", "修复登录Bug", "优化API性能" — action-oriented, ultra-concise
- Intent should be like "优化搜索性能并修复排序问题" — describe what user wants
- Output ONLY valid JSON: {{"title": "...", "intent": "..."}}

Conversation:
{context}"""


async def generate_ai_title(
    session_id: str,
    messages: list[JSONLMessage],
    force: bool = False,
) -> Optional[tuple[str, str]]:
    """Generate AI-powered short title and intent summary for a session.

    Uses haiku to produce:
    - title: ≤8 Chinese chars or ≤20 English chars (e.g., "重构数据库", "Fix Auth Bug")
    - intent: ≤20 Chinese chars one-line summary (e.g., "优化搜索性能并修复排序bug")

    Results are cached in SessionMeta.name (and notes) to avoid re-requesting.
    Returns (title, intent) tuple, or None if generation fails.

    Args:
        session_id: Session UUID
        messages: Parsed JSONL messages from the session
        force: If True, regenerate even if a name is already cached
    """
    from ccsm.core.meta import load_meta, save_meta

    # ── Cache check ──────────────────────────────────────────────────────────
    meta = load_meta(session_id)
    if meta.name and not force:
        return None  # Already has a cached name, skip

    # ── Build context from user messages ────────────────────────────────────
    user_messages = [m for m in messages if m.role == "user"]
    if not user_messages:
        return None

    # First 3 user messages + last user message (deduped)
    context_msgs = user_messages[:3]
    if len(user_messages) > 3:
        context_msgs.append(user_messages[-1])

    context = "\n---\n".join(m.content[:300] for m in context_msgs)
    if len(context) > 2000:
        context = context[:2000]

    # Escape braces in user content to prevent .format() KeyError
    safe_context = context.replace("{", "{{").replace("}", "}}")
    prompts = get_prompts()
    prompt = prompts.title_user.format(context=safe_context)

    # ── API call ─────────────────────────────────────────────────────────────
    try:
        import httpx

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                f"{DEFAULT_BASE_URL}/v1/chat/completions",
                json={
                    "model": DEFAULT_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 200,
                    "temperature": 0.3,
                },
            )
            resp.raise_for_status()
            data = resp.json()

        content = data["choices"][0]["message"]["content"].strip()

        # ── Parse JSON from response (handle possible markdown fences) ───────
        import re

        # Extract first JSON object using bracket-depth matching
        # (handles values containing braces, unlike simple regex)
        result = _extract_json_object(content)
        if result is None:
            logger.warning("AI title: no JSON found in response: %r", content[:200])
            return None
        title = result.get("title", "").strip()
        # 兼容新旧格式: summary (新) 或 intent (旧)
        intent = result.get("summary", result.get("intent", "")).strip()

        if not title:
            logger.warning("AI title: empty title in response")
            return None

        # ── Cache to sidecar meta ────────────────────────────────────────────
        meta.name = title
        if intent:
            meta.ai_intent = intent
        save_meta(meta)

        logger.info("AI title generated for %s: %r / %r", session_id, title, intent)
        return (title, intent)

    except Exception as e:
        logger.warning("AI title generation failed for %s: %s", session_id, e)
        return None


def generate_ai_title_sync(
    session_id: str,
    messages: list[JSONLMessage],
    force: bool = False,
) -> Optional[tuple[str, str]]:
    """Synchronous wrapper for generate_ai_title.

    Handles both cases:
    - Called from a plain synchronous context: uses asyncio.run()
    - Called from within a running event loop: offloads to a thread pool

    Args:
        session_id: Session UUID
        messages: Parsed JSONL messages from the session
        force: If True, regenerate even if a name is already cached

    Returns:
        (title, intent) tuple, or None if generation fails / already cached.
    """
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        # Already inside a running loop — run in a separate thread to avoid deadlock
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                generate_ai_title(session_id, messages, force),
            )
            return future.result(timeout=20)
    except RuntimeError:
        # No running loop — safe to call asyncio.run() directly
        return asyncio.run(generate_ai_title(session_id, messages, force))


# ─── Compact Summary AI Refinement ────────────────────────────────────────────


_COMPACT_REFINE_PROMPT = """\
你是一个会话分析师。下面是 Claude Code 在 compact 操作时自动生成的上下文摘要。
请从中提取结构化的工作进度信息。

提取规则：
1. 工作阶段进度（3-6个关键阶段）——每个标记 done/wip/pending
2. 被否决的方案和原因（帮用户避免 resume 后重复探索）
3. 用户明确说要做但还没做的事（只提取用户确认的，不包括 AI 建议的）
4. 当前阻塞点（如果有的话）
5. 默认使用中文，专有名词保留英文

输出 ONLY valid JSON:
{
  "phases": [
    {"label": "阶段名（≤10字）", "status": "done|wip|pending", "detail": "一句话说明"}
  ],
  "rejected": [
    {"approach": "被否决的方案", "reason": "原因"}
  ],
  "pending_user_tasks": ["用户确认要做的具体事项"],
  "blocker": {"description": "阻塞描述", "needs": "需要什么才能继续"} | null
}"""


_COMPACT_REFINE_USER = """\
以下是 Claude Code compact 摘要的内容：

{compact_text}

请提取结构化进度信息。输出 ONLY JSON。"""


async def refine_compact_summary(
    compact_text: str,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> Optional[dict]:
    """Use AI to refine a compact summary into structured progress data.

    Takes the raw compact summary text (typically 5-10K chars) and produces
    structured phases, rejected approaches, pending tasks, and blockers.

    Returns parsed JSON dict on success, None on failure.
    Cost: very low — input is a pre-existing summary, not raw conversation.
    """
    if not compact_text or len(compact_text) < 50:
        return None

    # Truncate very long summaries to control token cost
    if len(compact_text) > 8000:
        compact_text = compact_text[:8000] + "\n... (truncated)"

    safe_text = compact_text.replace("{", "{{").replace("}", "}}")
    user_prompt = get_prompts().compact_user.format(compact_text=safe_text)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": get_prompts().compact_system},
                        {"role": "user", "content": user_prompt},
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

            return json.loads(raw_text)

    except Exception as e:
        logger.warning("Compact summary refinement failed: %s", e)
        return None


def refine_compact_summary_sync(
    compact_text: str,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> Optional[dict]:
    """Synchronous wrapper for refine_compact_summary."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                refine_compact_summary(compact_text, base_url, api_key, model),
            )
            return future.result(timeout=25)
    except RuntimeError:
        return asyncio.run(
            refine_compact_summary(compact_text, base_url, api_key, model)
        )


# ─── AI Digest: Five-Dimension Structured Summary ──────────────────────────


_DIGEST_SYSTEM_PROMPT = """\
你是一个会话恢复分析师。给定用户与 Claude Code 的完整对话，生成五维结构化摘要，\
帮助用户打开这个会话时瞬间恢复上下文。

五个维度：
- goal: 一句话概括用户的原始目标
- progress: 2-3句话描述完成了什么（提及具体文件、工具操作、关键决策）
- breakpoint: 一句话说明工作停在了哪里
- next_steps: 1-3条具体行动项（"在 file.py 中实现 X"，而非"继续工作"）
- blocker: 如果有阻塞，说明需要什么才能继续；否则 null

规则：
- 默认使用中文，专有名词（API、Redis、Kubernetes）保留英文
- progress 要具体，不要泛泛而谈
- next_steps 必须是可执行的，不是"继续开发"这种
- 输出 ONLY valid JSON，无 markdown 围栏，无解释"""

_DIGEST_USER_TEMPLATE = """\
分析这段对话并生成五维恢复摘要。会话有 {msg_count} 条消息，历时 {duration}。

{compact_section}

{milestones_section}

以下是对话消息（用户 [U]，助手 [A]，工具操作 [T]）：

{conversation}

输出 ONLY valid JSON:
{{"goal": "...", "progress": "...", "breakpoint": "...", "next_steps": ["..."], "blocker": ... }}"""


def _format_messages_for_digest(
    messages: list[JSONLMessage],
    max_chars: int = 20000,
) -> str:
    """Format conversation for digest generation.

    Wider budget than _format_messages_for_prompt (milestone extraction):
    - User messages: keep first 500 chars (need decision context)
    - Assistant messages: keep first 200 chars (need outcomes/conclusions)
    - Tool operations: annotate with [Edited: file], [Ran: cmd]
    - Total budget: 20KB (vs 12KB for milestones)
    """
    lines: list[str] = []
    total_chars = 0

    for i, msg in enumerate(messages):
        prefix = "[U]" if msg.role == "user" else "[A]"
        text = msg.content.strip()

        if not text:
            continue

        if msg.role == "user":
            if len(text) > 500:
                text = text[:497] + "…"
        else:
            # Assistant: keep first meaningful line, up to 200 chars
            first_line = ""
            for line in text.split("\n"):
                line = line.strip()
                if line and len(line) > 5 and not line.startswith(("```", "─", "═", "###")):
                    first_line = line
                    break
            text = first_line[:200] if first_line else "(tool usage / code output)"

        line = f"{prefix} {text}"
        if total_chars + len(line) > max_chars:
            lines.append(f"... ({len(messages) - i} more messages)")
            break

        lines.append(line)
        total_chars += len(line) + 1

    return "\n".join(lines)


def _build_digest_prompt(
    messages: list[JSONLMessage],
    compact_summary_text: Optional[str] = None,
    milestones: Optional[list[Milestone]] = None,
) -> str:
    """Build the user prompt for digest generation."""
    conversation = _format_messages_for_digest(messages)
    duration = _format_duration(messages)

    # Compact summary section (high-quality, pre-existing)
    if compact_summary_text:
        safe_text = compact_summary_text[:6000]  # Limit compact injection
        # Escape braces to prevent .format() KeyError on user content
        safe_text = safe_text.replace("{", "{{").replace("}", "}}")
        compact_section = f"Claude Code 已有的上下文摘要（compact summary）：\n{safe_text}"
    else:
        compact_section = ""

    # Milestones section (structural context)
    if milestones:
        ms_lines = []
        for ms in milestones:
            status_icon = {"done": "✓", "wip": "▶", "pending": "○"}.get(
                ms.status.value, "·"
            )
            # Escape braces in user-generated labels/details
            label = ms.label.replace("{", "{{").replace("}", "}}")
            detail = (ms.detail or "").replace("{", "{{").replace("}", "}}")
            ms_lines.append(f"  {status_icon} {label}: {detail}")
        milestones_section = "已提取的里程碑：\n" + "\n".join(ms_lines)
    else:
        milestones_section = ""

    return get_prompts().digest_user.format(
        msg_count=len(messages),
        duration=duration,
        compact_section=compact_section,
        milestones_section=milestones_section,
        conversation=conversation,
    )


async def generate_digest(
    session_id: str,
    jsonl_path: Path,
    compact_summary_text: Optional[str] = None,
    milestones: Optional[list[Milestone]] = None,
    force: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> Optional[SessionDigest]:
    """Generate a five-dimension AI digest for a session.

    Input priority:
      1. Full user messages + partial assistant (via _format_messages_for_digest)
      2. Compact summary text (if available, high-quality pre-existing signal)
      3. Milestones (if available, structural context)

    Returns SessionDigest on success, None on failure.
    Result is cached in the existing SessionSummary sidecar file.
    """
    # Cache check
    if not force:
        cached = load_summary(session_id)
        if cached and cached.digest:
            return cached.digest

    # Parse messages
    messages = parse_session_messages(jsonl_path)
    if not messages:
        return None

    # Build prompt
    user_prompt = _build_digest_prompt(messages, compact_summary_text, milestones)

    try:
        import httpx

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": get_prompts().digest_system},
                        {"role": "user", "content": user_prompt},
                    ],
                    "max_tokens": 2048,
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

            # Legacy compat: default goal/next_steps to safe values
            # since existing consumers (MCP server, TUI) call digest.goal
            # and ', '.join(digest.next_steps) which would crash on None
            todo_items = data.get("todo", data.get("next_steps", []))
            digest = SessionDigest(
                progress=data.get("progress", ""),
                breakpoint=data.get("breakpoint", ""),
                decisions=data.get("decisions", []),
                todo=todo_items,
                goal=data.get("goal", ""),
                next_steps=todo_items,  # mirror todo for legacy consumers
                blocker=data.get("blocker"),
            )

            # Persist to summary cache
            summary = load_summary(session_id)
            if summary:
                summary.digest = digest
                save_summary(summary)

            return digest

    except Exception as e:
        logger.warning("Digest generation failed: %s", e)
        return None


def generate_digest_sync(
    session_id: str,
    jsonl_path: Path,
    compact_summary_text: Optional[str] = None,
    milestones: Optional[list[Milestone]] = None,
    force: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> Optional[SessionDigest]:
    """Synchronous wrapper for generate_digest."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                generate_digest(
                    session_id, jsonl_path, compact_summary_text,
                    milestones, force, base_url, api_key, model,
                ),
            )
            return future.result(timeout=35)
    except RuntimeError:
        return asyncio.run(
            generate_digest(
                session_id, jsonl_path, compact_summary_text,
                milestones, force, base_url, api_key, model,
            )
        )


# ─── Session Facts: Atomic Fact Extraction ──────────────────────────────────


_FACTS_SYSTEM_PROMPT = """\
你是一个知识提取专家。从以下会话摘要信息中提取原子化事实（atomic facts）。

原子化事实的要求：
- 每个 fact 是一个独立、自包含的陈述，不依赖上下文即可理解
- 不使用代词（"它"、"这个"），用具体名称替代
- 标注类型：decision（决策）/ discovery（发现）/ config（配置）/ constraint（约束）
- 标注来源：从哪个阶段/里程碑提取的
- 3-8 个 facts，质量优先，不要重复

默认使用中文，专有名词保留英文。

输出 ONLY valid JSON:
{{"facts": [{{"content": "...", "type": "decision|discovery|config|constraint", "source": "..."}}]}}"""


_FACTS_USER_TEMPLATE = """\
从以下会话信息中提取原子化事实：

{digest_section}

{compact_section}

{milestones_section}

提取 3-8 个原子化事实。输出 ONLY JSON。"""


async def extract_facts(
    session_id: str,
    compact_summary_text: Optional[str] = None,
    milestones: Optional[list[Milestone]] = None,
    digest: Optional[SessionDigest] = None,
    force: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> list[SessionFact]:
    """Extract atomic facts from pre-existing session artifacts.

    Key design: uses ALREADY-GENERATED data (compact + milestones + digest)
    as input, NOT raw messages. This keeps the LLM call very cheap (~2-3KB input).

    Returns list of SessionFact on success, empty list on failure.
    """
    # Cache check
    if not force:
        cached = load_summary(session_id)
        if cached and cached.facts:
            return cached.facts

    # Build input from pre-existing artifacts
    sections = []

    if digest:
        decisions_str = ', '.join(digest.decisions) if digest.decisions else ''
        todo_str = ', '.join(digest.todo or digest.next_steps or [])
        digest_section = (
            f"进度：{digest.progress}\n"
            f"断点：{digest.breakpoint}\n"
            f"决策：{decisions_str}\n"
            f"待办：{todo_str}"
        )
    else:
        digest_section = ""

    if compact_summary_text:
        compact_section = f"Compact 摘要：\n{compact_summary_text[:4000]}"
    else:
        compact_section = ""

    if milestones:
        ms_lines = []
        for ms in milestones:
            status_icon = {"done": "✓", "wip": "▶", "pending": "○"}.get(
                ms.status.value, "·"
            )
            detail = f": {ms.detail}" if ms.detail else ""
            ms_lines.append(f"  {status_icon} {ms.label}{detail}")
            for si in (ms.sub_items or []):
                si_icon = {"done": "✓", "wip": "▶", "pending": "○"}.get(
                    si.status.value, "·"
                )
                ms_lines.append(f"    {si_icon} {si.label}")
        milestones_section = "里程碑：\n" + "\n".join(ms_lines)
    else:
        milestones_section = ""

    # Need at least some input
    if not digest_section and not compact_section and not milestones_section:
        return []

    # Escape braces to prevent .format() KeyError on user content
    def _esc(s: str) -> str:
        return s.replace("{", "{{").replace("}", "}}")

    user_prompt = get_prompts().facts_user.format(
        digest_section=_esc(digest_section),
        compact_section=_esc(compact_section),
        milestones_section=_esc(milestones_section),
    )

    try:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": get_prompts().facts_system},
                        {"role": "user", "content": user_prompt},
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
            facts_data = data.get("facts", [])

            facts = [
                SessionFact(
                    content=fd.get("content", ""),
                    fact_type=fd.get("type"),
                    source=fd.get("source"),
                )
                for fd in facts_data
                if isinstance(fd, dict) and fd.get("content")
            ]

            # Persist to summary cache
            summary = load_summary(session_id)
            if summary:
                summary.facts = facts
                save_summary(summary)

            return facts

    except Exception as e:
        logger.warning("Facts extraction failed: %s", e)
        return []


def extract_facts_sync(
    session_id: str,
    compact_summary_text: Optional[str] = None,
    milestones: Optional[list[Milestone]] = None,
    digest: Optional[SessionDigest] = None,
    force: bool = False,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> list[SessionFact]:
    """Synchronous wrapper for extract_facts."""
    import asyncio
    import concurrent.futures

    try:
        asyncio.get_running_loop()
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run,
                extract_facts(
                    session_id, compact_summary_text, milestones,
                    digest, force, base_url, api_key, model,
                ),
            )
            return future.result(timeout=25)
    except RuntimeError:
        return asyncio.run(
            extract_facts(
                session_id, compact_summary_text, milestones,
                digest, force, base_url, api_key, model,
            )
        )


# ─── Batch Preprocessing Interface ─────────────────────────────────────────


def batch_preprocess(
    session_ids: list[str],
    jsonl_paths: dict[str, Path],
    compact_summaries: Optional[dict[str, str]] = None,
    mode: str = "llm",
    generate_digests: bool = True,
    extract_session_facts: bool = True,
    on_progress: Optional[object] = None,
    base_url: str = DEFAULT_BASE_URL,
    api_key: str = DEFAULT_API_KEY,
    model: str = DEFAULT_MODEL,
) -> dict[str, SessionSummary]:
    """Batch preprocess multiple sessions: summary + digest + facts.

    Designed for CLI or external package usage. Processes sessions
    sequentially with progress callback.

    Args:
        session_ids: List of session UUIDs to process
        jsonl_paths: Mapping of session_id -> Path to JSONL file
        compact_summaries: Optional mapping of session_id -> raw compact text
        mode: "extract" or "llm"
        generate_digests: Whether to also generate AI digests
        extract_session_facts: Whether to also extract facts
        on_progress: Callback(session_id: str, step: int, total: int)
        base_url, api_key, model: LLM configuration

    Returns:
        Dict of session_id -> SessionSummary (with digest and facts populated)
    """
    raise NotImplementedError(
        "Batch preprocessing — coming in next iteration. "
        "Use summarize_session() + generate_digest_sync() + extract_facts_sync() "
        "individually for now."
    )
