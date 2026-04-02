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

from ccsm.core.meta import load_summary, save_summary
from ccsm.core.milestones import extract_breakpoint, extract_milestones
from ccsm.core.parser import parse_session_messages
from ccsm.models.session import (
    Breakpoint,
    JSONLMessage,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
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

    user_prompt = _USER_PROMPT_TEMPLATE.format(
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
            system=_SYSTEM_PROMPT,
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
    # Check cache first
    if not force:
        cached = load_summary(session_id)
        if cached and cached.milestones:
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
1. A short title (≤8 Chinese characters or ≤20 English characters) — the core task action
2. A one-line intent summary (≤20 Chinese characters) — the user's original request

Rules:
- Title should be like "重构数据库" or "Fix Login Bug" — action-oriented, ultra-concise
- Intent should be like "优化搜索性能并修复排序问题" — describe what user wants
- Use the same language as the conversation
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
    prompt = _TITLE_PROMPT_TEMPLATE.format(context=safe_context)

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
        intent = result.get("intent", "").strip()

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
