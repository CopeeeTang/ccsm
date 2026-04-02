"""Rule-based milestone extraction from conversation messages.

Detects phase-transition signals in user messages to build a session timeline.
Zero API cost — pure pattern matching on discourse markers.

Transition Signal Taxonomy (what triggers a new milestone):
─────────────────────────────────────────────────────────────
1. TOPIC_SHIFT     — "好，我们接下来讨论…" / "换一个方面" / "接下去"
2. APPROVAL_PIVOT  — "没问题" / "可以" / "OK" → followed by new topic
3. DIRECTIVE       — "开始实施" / "你去执行" / "帮我改" / "spawn"
4. REVIEW_ENTRY    — "我看一下" / "demo" / "review" / "验收"
5. SUMMARY         — "总结一下" / "目前状态" / "做了ABC"

Key design principle: only USER messages trigger milestones.
Claude's responses fill in the detail/content of each phase,
but the structural turning points are driven by the human.

v2 changes:
- Removed plan_produced signal (too noisy — Claude outputs lists constantly)
- Added detail enrichment from surrounding assistant context
- Added phase grouping: consecutive same-type signals merge
- Improved breakpoint extraction with system noise filtering
"""

from __future__ import annotations

import re
from typing import Optional

from ccsm.models.session import (
    Breakpoint,
    JSONLMessage,
    Milestone,
    MilestoneItem,
    MilestoneStatus,
)

# ─── Signal patterns ─────────────────────────────────────────────────────────
# Each pattern matches user messages that indicate a phase transition.
# Patterns are checked in order; first match wins for that message.

# Topic shift: user explicitly announces a new discussion topic
_TOPIC_SHIFT = re.compile(
    r"(接下来|接下去|下一个|下一步|换一个|另外一个|我们来|那我们|下面|先)"
    r".{0,15}"
    r"(讨论|聊|看|做|处理|搞|实现|设计|优化|分析|调研|研究|确认|对比|测试)",
    re.IGNORECASE,
)

# Approval + pivot: short confirmation followed by new direction
_APPROVAL = re.compile(
    r"^(没问题|没有问题|可以|OK|好的?|行|嗯|对|确认|同意|LGTM|approved?|继续|A|B|C|D)\s*$",
    re.IGNORECASE,
)

# Directive: user tells Claude to execute something concrete
_DIRECTIVE = re.compile(
    r"(开始实施|开始执行|开始做|你去|帮我|spawn|dispatch|执行|实现一下"
    r"|改一下|写一下|跑一下|部署|发布|commit|push|提交|配置一下"
    r"|调研|安装|创建|搭建|升级|迁移|重构|添加|删除|修复)",
    re.IGNORECASE,
)

# Review entry: user enters verification/review mode
_REVIEW = re.compile(
    r"(我看一下|看看效果|给我看|demo|review|验收|检查一下|测试一下|跑一下测试"
    r"|codex.*review|效果[怎如]|试一下|有没有问题|不满意|优化一下)",
    re.IGNORECASE,
)

# Summary/retrospective: user asks for status or triggers save
_SUMMARY = re.compile(
    r"(总结|目前[的所]|现在[的所]|做了[什哪]|进[度展]|汇报|save.?session|保存|todo|状态)",
    re.IGNORECASE,
)

# Question: user asks about a specific topic (signals discussion focus)
_QUESTION = re.compile(
    r"(为什么|怎么|如何|是不是|能不能|有没有|什么是|哪个|哪些|是否|可否"
    r"|\.{3}吗|吗\s*$|\?\s*$)",
    re.IGNORECASE,
)

# Slash commands that indicate phase transitions
_SLASH_TRANSITIONS = re.compile(
    r"^/(spawn|save-session|commit|interview-mode|plan|review|codex-review)",
    re.IGNORECASE,
)

# ─── Noise filters ───────────────────────────────────────────────────────────

# System-injected content to filter out
_SYSTEM_NOISE = re.compile(
    r"(<(command-message|task-notification|local-command|system-reminder|antml:|user-prompt-submit)"
    r"|Base directory for this skill"
    r"|This session is being continued"
    r"|Caveat: The messages below"
    r"|Copied to clipboard"
    r"|Compacted \(ctrl)",
    re.IGNORECASE,
)


def _clean_text(text: str) -> str:
    """Strip system-injected XML tags and noise from message content."""
    # Remove XML tag blocks
    text = re.sub(r"<[a-z_-]+>[^<]*</[a-z_-]+>", "", text, flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"<[a-z_-]+(?:\s[^>]*)?>", "", text, flags=re.IGNORECASE)
    text = re.sub(r"</[a-z_-]+>", "", text, flags=re.IGNORECASE)
    return text.strip()


# ─── Signal detection ─────────────────────────────────────────────────────────


def _detect_signal(msg: JSONLMessage) -> Optional[str]:
    """Detect the transition signal type in a user message.

    Returns signal name or None. Only user messages produce structural signals.
    Assistant messages are never signal sources (v2: removed plan_produced).
    """
    if msg.role != "user":
        return None

    text = msg.content.strip()
    if not text:
        return None

    # Filter out system-injected noise
    if _SYSTEM_NOISE.search(text[:200]):
        return None

    # Slash command transitions
    if _SLASH_TRANSITIONS.match(text):
        return "directive"

    # Clean text for pattern matching
    clean = _clean_text(text)
    if not clean:
        return None

    # Short approval (< 30 chars) — likely a pivot point
    if len(clean) < 30 and _APPROVAL.match(clean):
        return "approval"

    # Order matters: more specific patterns first
    if _REVIEW.search(clean):
        return "review"
    if _SUMMARY.search(clean):
        return "summary"
    if _TOPIC_SHIFT.search(clean):
        return "topic_shift"
    if _DIRECTIVE.search(clean):
        return "directive"

    return None


# ─── Topic & detail extraction ────────────────────────────────────────────────


def _extract_user_intent(msg: JSONLMessage) -> str:
    """Extract a condensed intent phrase from a user message.

    Strategy: strip filler → extract key noun phrases → join as tags.
    Goal: "emoji有点多，前端可以模仿Claude极简设计" → "前端极简设计 · emoji优化"
    """
    text = _clean_text(msg.content)
    if not text:
        return ""

    # Remove conversational prefixes
    text = re.sub(
        r"^(好[的吧]?[，,]?\s*|OK[,，]?\s*|没问题[，,]?\s*|嗯[，,]?\s*"
        r"|对[，,]?\s*|行[，,]?\s*|继续[，,]?\s*)",
        "", text,
    )
    text = re.sub(r"^(接下来|那我们|下面|然后)\s*", "", text)

    # Take first line only
    first_line = ""
    for line in text.split("\n"):
        line = line.strip()
        if line and len(line) > 2:
            first_line = line
            break

    if not first_line:
        return ""

    # If short enough already (< 25 chars), use as-is
    if len(first_line) <= 25:
        return first_line

    # Otherwise, try to extract key phrases:
    # 1. Look for quoted content or backtick content
    quoted = re.findall(r'[「"\'`]([^「"\'`]+)[」"\'`]', first_line)
    if quoted:
        return " · ".join(q[:20] for q in quoted[:3])

    # 2. Extract verb+object phrases (Chinese: verb + noun within 15 chars)
    vo_phrases = re.findall(
        r"(讨论|实现|修复|优化|设计|添加|删除|创建|配置|调研|升级|测试|部署|发布|检查|分析)"
        r"(.{1,15}?)"
        r"(?:[，,。；;！!？?\s]|$)",
        first_line,
    )
    if vo_phrases:
        return " · ".join(f"{v}{o.strip()}" for v, o in vo_phrases[:3])

    # 3. Fallback: extract key nouns (real tech terms, not UUIDs)
    tech_terms = re.findall(
        r"(?:[A-Z][a-z]{2,}(?:[A-Z][a-z]+)*"  # CamelCase (min 3 chars)
        r"|[A-Z]{2,6}"                          # Acronyms 2-6 chars (API, TUI, CSS)
        r"|[a-z]{3,}(?:_[a-z]{2,})+"            # snake_case (min 3+2 chars)
        r"|[a-z]{3,}\.[a-z]{2,})"               # module.name (min 3+2 chars)
        , first_line,
    )
    if tech_terms:
        cn_prefix = re.sub(r"[a-zA-Z_.\-\d]+", "", first_line).strip()
        cn_prefix = re.sub(r"\s+", "", cn_prefix)[:15]
        terms = " · ".join(t for t in tech_terms[:3] if len(t) > 2)
        if cn_prefix and len(cn_prefix) > 2 and terms:
            return f"{cn_prefix} ({terms})"
        if terms:
            return terms

    # 4. Last resort: truncate at sentence boundary
    truncated = first_line[:28]
    for sep in ["，", ",", "。", "；", "、", " ", "？", "！"]:
        idx = truncated.rfind(sep)
        if idx > 8:
            return truncated[:idx]
    return truncated + "…"


def _extract_assistant_summary(msgs: list[JSONLMessage], start: int, end: int) -> Optional[str]:
    """Extract a condensed summary from assistant messages in a range.

    Looks for actionable first lines, skipping boilerplate.
    """
    for i in range(start, min(end + 1, len(msgs))):
        m = msgs[i]
        if m.role != "assistant":
            continue

        text = _clean_text(m.content)
        if not text or len(text) < 20:
            continue

        for line in text.split("\n"):
            line = line.strip()
            # Skip: headers, separators, code fences, boilerplate openers
            if re.match(
                r"^(#{1,4}\s|[-=*]{3,}|```|─|═"
                r"|好的[，,]|让我|我来|没问题|明白|理解"
                r"|OK[,，]|Sure|Great|\*\*|>\s"
                r"|\(|\[|<)",
                line,
            ):
                continue
            if line and len(line) > 8:
                if len(line) > 40:
                    for sep in ["。", "，", ",", "；", "—", " ", "："]:
                        idx = line[:40].rfind(sep)
                        if idx > 12:
                            return line[:idx]
                    return line[:38] + "…"
                return line

    return None


# ─── Core extraction ─────────────────────────────────────────────────────────


_SIGNAL_TO_LABEL = {
    "topic_shift": "💬 讨论",
    "approval": "✅ 确认",
    "directive": "🔧 执行",
    "review": "🔍 评审",
    "summary": "📊 总结",
}


def extract_milestones(messages: list[JSONLMessage]) -> list[Milestone]:
    """Extract milestones from a chronological list of conversation messages.

    v2 algorithm:
    1. Scan USER messages for transition signals (no assistant plan detection)
    2. For each signal, create a milestone with the user's intent as detail
    3. Enrich with assistant's opening response as secondary context
    4. Merge adjacent same-type milestones
    5. Cap at ~12 milestones max (skip low-value signals if too many)
    """
    if not messages:
        return []

    # Phase 1: detect transition points (user messages only)
    transitions: list[tuple[int, str]] = []
    for i, msg in enumerate(messages):
        signal = _detect_signal(msg)
        if signal:
            transitions.append((i, signal))

    # Phase 2: build raw milestones
    if not transitions:
        # No signals at all — create a single milestone from first user message
        first_user = next((m for m in messages if m.role == "user"), None)
        intent = _extract_user_intent(first_user) if first_user else "Discussion"
        asst_summary = _extract_assistant_summary(messages, 0, len(messages) - 1)
        return [Milestone(
            label=intent or "Discussion",
            detail=asst_summary,
            status=MilestoneStatus.IN_PROGRESS,
            start_msg_idx=0,
            end_msg_idx=len(messages) - 1,
        )]

    # Prepend an implicit "start" milestone for messages before first signal
    if transitions[0][0] > 2:
        # There's substantial conversation before the first signal
        first_user = next((m for m in messages if m.role == "user"), None)
        intent = _extract_user_intent(first_user) if first_user else ""
        raw_milestones = [(0, "topic_shift", intent)]
    else:
        raw_milestones = []

    for t_idx, (msg_idx, signal) in enumerate(transitions):
        msg = messages[msg_idx]
        intent = _extract_user_intent(msg)
        raw_milestones.append((msg_idx, signal, intent))

    # Phase 3: create Milestone objects with enriched detail
    milestones: list[Milestone] = []
    for r_idx, (msg_idx, signal, intent) in enumerate(raw_milestones):
        # Determine end index
        if r_idx + 1 < len(raw_milestones):
            end_idx = raw_milestones[r_idx + 1][0] - 1
        else:
            end_idx = len(messages) - 1

        label = _SIGNAL_TO_LABEL.get(signal, "💬 讨论")

        # Detail: user intent (primary) + assistant summary (secondary)
        detail = intent
        if not detail or len(detail) < 5:
            # Fallback to assistant's response
            asst_summary = _extract_assistant_summary(messages, msg_idx, end_idx)
            detail = asst_summary or ""

        ms = Milestone(
            label=label,
            detail=detail if detail else None,
            start_msg_idx=msg_idx,
            end_msg_idx=end_idx,
        )
        milestones.append(ms)

    # Phase 4: assign statuses
    for ms in milestones[:-1]:
        ms.status = MilestoneStatus.DONE
    if milestones:
        milestones[-1].status = MilestoneStatus.IN_PROGRESS

    # Phase 5: merge adjacent same-type milestones
    milestones = _merge_adjacent(milestones)

    # Phase 6: cap at ~12 milestones (skip less informative ones if needed)
    if len(milestones) > 12:
        milestones = _prune(milestones, max_count=12)

    return milestones


def _merge_adjacent(milestones: list[Milestone]) -> list[Milestone]:
    """Merge consecutive milestones of the same type.

    Prevents spam from rapid successive signals of the same kind.
    Keeps the most informative detail.
    """
    if len(milestones) <= 1:
        return milestones

    merged: list[Milestone] = [milestones[0]]
    for ms in milestones[1:]:
        prev = merged[-1]
        # Same label type AND both DONE → merge
        # (don't merge if current is the active WIP milestone)
        same_type = ms.label == prev.label
        both_done = prev.status == MilestoneStatus.DONE and ms.status == MilestoneStatus.DONE
        if same_type and both_done:
            prev.end_msg_idx = ms.end_msg_idx
            # Keep longer/more informative detail
            if ms.detail and (not prev.detail or len(ms.detail) > len(prev.detail)):
                prev.detail = ms.detail
        else:
            merged.append(ms)

    return merged


def _prune(milestones: list[Milestone], max_count: int) -> list[Milestone]:
    """Prune milestones to max_count by removing least informative ones.

    Strategy: keep first, last, and milestones with the most detail.
    Remove short-detail approval/confirmation milestones first.
    """
    if len(milestones) <= max_count:
        return milestones

    # Always keep first and last
    keep_set = {0, len(milestones) - 1}

    # Score remaining by information value
    scored = []
    for i, ms in enumerate(milestones):
        if i in keep_set:
            continue
        score = 0
        score += len(ms.detail or "") * 2  # Detail length
        score += (ms.end_msg_idx - (ms.start_msg_idx or 0)) * 3  # Message span
        if "执行" in ms.label or "评审" in ms.label:
            score += 20  # Phase transitions are valuable
        if "确认" in ms.label:
            score -= 10  # Approvals are less valuable
        scored.append((i, score))

    # Keep top-scoring milestones
    scored.sort(key=lambda x: -x[1])
    for i, _ in scored[:max_count - len(keep_set)]:
        keep_set.add(i)

    return [ms for i, ms in enumerate(milestones) if i in keep_set]


# ─── Breakpoint extraction ────────────────────────────────────────────────────


def extract_breakpoint(
    messages: list[JSONLMessage],
    milestones: list[Milestone],
) -> Optional[Breakpoint]:
    """Extract the breakpoint — where the user left off.

    Finds the last meaningful user message (filtering out system noise)
    and creates a breakpoint anchored to the last milestone.
    """
    if not messages or not milestones:
        return None

    last_ms = milestones[-1]

    # Find last meaningful user message (skip noise)
    last_user_text = None
    for m in reversed(messages):
        if m.role != "user":
            continue
        text = m.content.strip()
        if not text or _SYSTEM_NOISE.search(text[:200]):
            continue
        clean = _clean_text(text)
        if clean and len(clean) > 2:
            last_user_text = clean
            break

    # Find last meaningful assistant message
    last_asst_text = None
    for m in reversed(messages):
        if m.role != "assistant":
            continue
        text = _clean_text(m.content)
        if text and len(text) > 20:
            first_line = text.split("\n")[0].strip()
            if first_line and len(first_line) > 5:
                last_asst_text = first_line
                break

    # Build detail from user's last message
    detail = last_ms.detail or last_ms.label
    if last_user_text:
        user_line = last_user_text.split("\n")[0]
        if len(user_line) > 80:
            user_line = user_line[:79] + "…"
        detail = user_line

    # Topic: the specific thing being discussed
    last_topic = None
    if last_asst_text:
        if len(last_asst_text) > 80:
            last_asst_text = last_asst_text[:79] + "…"
        last_topic = last_asst_text

    return Breakpoint(
        milestone_label=last_ms.label,
        detail=detail,
        last_topic=last_topic,
    )
