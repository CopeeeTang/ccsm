"""国际化模块 — 双语 prompt 和显示字符串。

支持语言: zh-CN, en
配置方式: 环境变量 CCSM_LANG 或调用 set_language()

Usage:
    from ccsm.core.i18n import get_prompts, get_strings, set_language

    prompts = get_prompts()        # 返回当前语言的所有 prompt
    strings = get_strings()        # 返回当前语言的 UI 字符串
    set_language("en")             # 切换到英文
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Literal

Language = Literal["zh-CN", "en"]

# ─── 全局语言设置 ──────────────────────────────────────────────────────────

_VALID_LANGUAGES = {"zh-CN", "en"}
_env_lang = os.environ.get("CCSM_LANG", "zh-CN")
_current_language: Language = _env_lang if _env_lang in _VALID_LANGUAGES else "zh-CN"  # type: ignore


def get_language() -> Language:
    return _current_language


def set_language(lang: Language) -> None:
    global _current_language
    if lang not in ("zh-CN", "en"):
        raise ValueError(f"Unsupported language: {lang}. Use 'zh-CN' or 'en'.")
    _current_language = lang


# ─── Prompt 数据类 ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PromptSet:
    """一个 AI 组件的完整 prompt 配置。"""
    title_user: str
    digest_system: str
    digest_user: str
    milestones_system: str
    milestones_user: str
    facts_system: str
    facts_user: str
    compact_system: str
    compact_user: str


@dataclass(frozen=True)
class UIStrings:
    """UI 显示字符串。"""
    progress: str
    decisions: str
    breakpoint: str
    todo: str
    milestones: str
    compact: str
    facts: str
    blocker: str
    no_blocker: str
    status_done: str
    status_wip: str
    status_pending: str
    rejected: str
    pending_tasks: str


# ═══════════════════════════════════════════════════════════════════════════
# 中文 Prompt (zh-CN)
# ═══════════════════════════════════════════════════════════════════════════

_TITLE_USER_ZH = """\
基于以下对话片段，生成：
1. 标题（15-25字）—— 行动导向，反映整个 session 的核心任务
2. 一句话摘要（30-60字）—— 涵盖"做了什么 + 停在哪"

标题规则：
- 格式：「主题域: 行动/状态」，如 "DMS 复用逻辑：重构 Session 存储结构"
- 行动导向，反映核心任务，而非讨论过程
- 严禁使用"关于 XXX 的讨论"、"探讨 XXX"等被动式
- 专有名词（API、Redis、Claude Code）保留英文

摘要规则：
- 叙事一句话，涵盖做了什么 + 停在哪
- 如 "拆分 SessionStore 为独立模块，解决了循环引用问题，但 KV-Cache 兼容性未验证"

输出 ONLY valid JSON: {{"title": "...", "summary": "..."}}

对话片段：
{context}"""

_DIGEST_SYSTEM_ZH = """\
你是一个会话恢复分析师。生成结构化 Digest 帮助用户瞬间恢复上下文。

Digest 包含四个区块（无需 goal/intent，Title 已覆盖）：
- progress: 2-3 句话描述完成了什么（提及具体文件、命令、关键操作）
- decisions: 2-5 个关键决策，每个含简短理由（如 "选择 SQLite — 并发安全"）
- breakpoint: 1-2 句话说明工作停在了哪里（这是最重要的信息，必须具体）
- todo: 1-3 条具体待办事项（可执行的，不是"继续开发"）

规则：
- 默认使用中文，专有名词保留英文
- progress 要具体：不是"分析了数据"，而是"用 GPT-5.2 在 subset 上跑了评测"
- decisions 每条格式："决策内容 — 原因"
- breakpoint 必须具体到正在做的事
- todo 必须是可执行的行动项
- 输出 ONLY valid JSON，无 markdown 围栏"""

_DIGEST_USER_ZH = """\
分析这段对话，生成四区块 Digest。会话有 {msg_count} 条消息，历时 {duration}。

{compact_section}

{milestones_section}

对话消息（[U] 用户, [A] 助手）：

{conversation}

输出 ONLY valid JSON:
{{"progress": "...", "decisions": ["决策1 — 原因", "决策2 — 原因"], "breakpoint": "...", "todo": ["待办1", "待办2"]}}"""

_MILESTONES_SYSTEM_ZH = """\
你是一个会话分析师。提取会话的结构大纲——像书的目录一样。

规则：
- 只提取结构性转折点（讨论→计划、计划→执行、执行→评审等）
- 每个里程碑只有「阶段名 + 状态」，不包含具体内容
- 阶段名 ≤15 字，简洁明确
- 最后一个活跃里程碑可以有 sub_items 列出具体子话题
- 默认使用中文，专有名词保留英文

输出 ONLY valid JSON:
{{
  "milestones": [
    {{
      "label": "阶段名",
      "status": "done|wip|pending",
      "sub_items": [{{"label": "子话题", "status": "done|wip|pending"}}]
    }}
  ],
  "breakpoint": {{
    "milestone_label": "活跃阶段",
    "detail": "具体停在哪"
  }}
}}"""

_MILESTONES_USER_ZH = """\
提取这段对话的结构大纲。{msg_count} 条消息，历时 {duration}。

{conversation}

输出 ONLY JSON，无 markdown。"""

_FACTS_SYSTEM_ZH = """\
你是一个知识索引专家。从会话信息中提取原子化事实，用于后台检索索引（不用于展示）。

原子化事实要求：
- 每个 fact 是独立、自包含的陈述，不依赖上下文
- 不使用代词，用具体名称替代
- 标注类型：decision / discovery / config / constraint
- 标注来源阶段
- 3-8 个 facts，质量优先
- 默认使用中文，专有名词保留英文

输出 ONLY valid JSON:
{{"facts": [{{"content": "...", "type": "decision|discovery|config|constraint", "source": "..."}}]}}"""

_FACTS_USER_ZH = """\
从以下会话信息中提取原子化事实（用于检索索引）：

{digest_section}

{compact_section}

{milestones_section}

提取 3-8 个原子化事实。输出 ONLY JSON。"""

_COMPACT_SYSTEM_ZH = """\
你是一个会话分析师。从 Claude Code 的 compact 摘要中提取结构化进度信息。

提取规则：
1. 工作阶段进度（3-6个关键阶段）—— done/wip/pending
2. 被否决的方案和原因
3. 用户确认要做但还没做的事
4. 当前阻塞点
5. 默认使用中文，专有名词保留英文

输出 ONLY valid JSON:
{{
  "phases": [{{"label": "阶段名", "status": "done|wip|pending", "detail": "一句话说明"}}],
  "rejected": [{{"approach": "方案", "reason": "原因"}}],
  "pending_user_tasks": ["事项"],
  "blocker": {{"description": "阻塞", "needs": "需要什么"}} | null
}}"""

_COMPACT_USER_ZH = """\
以下是 Claude Code compact 摘要：

{compact_text}

提取结构化进度信息。输出 ONLY JSON。"""


# ═══════════════════════════════════════════════════════════════════════════
# English Prompts (en)
# ═══════════════════════════════════════════════════════════════════════════

_TITLE_USER_EN = """\
Based on this conversation excerpt, generate:
1. A title (8-15 words) — action-oriented, reflecting the session's core task
2. A one-line summary (15-30 words) — covering "what was done + where stopped"

Title rules:
- Format: "Topic: Action/State", e.g. "DMS Reuse: Refactor Session Storage"
- Action-oriented, reflecting the core task, NOT the discussion process
- NEVER use "Discussion about X", "Exploring X", or passive forms
- Keep proper nouns as-is

Summary rules:
- One narrative sentence covering what was done + where stopped
- e.g. "Split SessionStore into independent module, fixed circular refs, KV-Cache compat unverified"

Output ONLY valid JSON: {{"title": "...", "summary": "..."}}

Conversation:
{context}"""

_DIGEST_SYSTEM_EN = """\
You are a session recovery analyst. Generate a structured Digest to help users instantly \
recover context when resuming a session.

Digest has four sections (no goal/intent needed — Title already covers it):
- progress: 2-3 sentences describing what was accomplished (mention specific files, commands, key operations)
- decisions: 2-5 key decisions, each with brief rationale (e.g. "Chose SQLite — concurrency safe")
- breakpoint: 1-2 sentences on where work stopped (this is the most important info, must be specific)
- todo: 1-3 concrete action items (executable, not "continue development")

Rules:
- progress must be specific: not "analyzed data" but "ran GPT-5.2 eval on subset"
- decisions format: "Decision — Reason"
- breakpoint must be specific to the exact point of interruption
- todo must be executable action items
- Output ONLY valid JSON, no markdown fences"""

_DIGEST_USER_EN = """\
Analyze this conversation and generate a four-section Digest. Session has {msg_count} messages over {duration}.

{compact_section}

{milestones_section}

Conversation ([U] user, [A] assistant):

{conversation}

Output ONLY valid JSON:
{{"progress": "...", "decisions": ["Decision1 — reason", "Decision2 — reason"], "breakpoint": "...", "todo": ["action1", "action2"]}}"""

_MILESTONES_SYSTEM_EN = """\
You are a session analyst. Extract the structural outline of the conversation — like a table of contents.

Rules:
- Only extract STRUCTURAL turning points (discussion→plan, plan→execution, execution→review, etc.)
- Each milestone has only "phase name + status", no detailed content
- Phase name ≤ 6 words, concise and clear
- The last active milestone may have sub_items listing specific sub-topics
- Keep proper nouns as-is

Output ONLY valid JSON:
{{
  "milestones": [
    {{
      "label": "phase name",
      "status": "done|wip|pending",
      "sub_items": [{{"label": "sub-topic", "status": "done|wip|pending"}}]
    }}
  ],
  "breakpoint": {{
    "milestone_label": "active phase",
    "detail": "where exactly it stopped"
  }}
}}"""

_MILESTONES_USER_EN = """\
Extract the structural outline of this conversation. {msg_count} messages over {duration}.

{conversation}

Output ONLY JSON, no markdown."""

_FACTS_SYSTEM_EN = """\
You are a knowledge indexing expert. Extract atomic facts from session info for backend search indexing (not for display).

Atomic fact requirements:
- Each fact is independent and self-contained, no context dependency
- No pronouns — use specific names
- Tag type: decision / discovery / config / constraint
- Tag source phase
- 3-8 facts, quality over quantity

Output ONLY valid JSON:
{{"facts": [{{"content": "...", "type": "decision|discovery|config|constraint", "source": "..."}}]}}"""

_FACTS_USER_EN = """\
Extract atomic facts from the following session info (for search indexing):

{digest_section}

{compact_section}

{milestones_section}

Extract 3-8 atomic facts. Output ONLY JSON."""

_COMPACT_SYSTEM_EN = """\
You are a session analyst. Extract structured progress info from Claude Code's compact summary.

Extraction rules:
1. Work phases (3-6 key phases) — done/wip/pending
2. Rejected approaches and reasons
3. User-confirmed pending tasks (not AI suggestions)
4. Current blockers if any

Output ONLY valid JSON:
{{
  "phases": [{{"label": "phase name", "status": "done|wip|pending", "detail": "one-line description"}}],
  "rejected": [{{"approach": "approach", "reason": "reason"}}],
  "pending_user_tasks": ["task"],
  "blocker": {{"description": "blocker", "needs": "what's needed"}} | null
}}"""

_COMPACT_USER_EN = """\
Here is a Claude Code compact summary:

{compact_text}

Extract structured progress info. Output ONLY JSON."""


# ═══════════════════════════════════════════════════════════════════════════
# UI 显示字符串
# ═══════════════════════════════════════════════════════════════════════════

_STRINGS_ZH = UIStrings(
    progress="进度",
    decisions="关键决策",
    breakpoint="断点",
    todo="待办",
    milestones="里程碑",
    compact="原始上下文",
    facts="检索索引",
    blocker="阻塞",
    no_blocker="无阻塞",
    status_done="完成",
    status_wip="进行中",
    status_pending="待开始",
    rejected="被否决",
    pending_tasks="待办事项",
)

_STRINGS_EN = UIStrings(
    progress="Progress",
    decisions="Key Decisions",
    breakpoint="Breakpoint",
    todo="Todo",
    milestones="Milestones",
    compact="Raw Context",
    facts="Search Index",
    blocker="Blocker",
    no_blocker="No blockers",
    status_done="Done",
    status_wip="In Progress",
    status_pending="Pending",
    rejected="Rejected",
    pending_tasks="Pending Tasks",
)


# ═══════════════════════════════════════════════════════════════════════════
# 注册表
# ═══════════════════════════════════════════════════════════════════════════

_PROMPTS: dict[Language, PromptSet] = {
    "zh-CN": PromptSet(
        title_user=_TITLE_USER_ZH,
        digest_system=_DIGEST_SYSTEM_ZH,
        digest_user=_DIGEST_USER_ZH,
        milestones_system=_MILESTONES_SYSTEM_ZH,
        milestones_user=_MILESTONES_USER_ZH,
        facts_system=_FACTS_SYSTEM_ZH,
        facts_user=_FACTS_USER_ZH,
        compact_system=_COMPACT_SYSTEM_ZH,
        compact_user=_COMPACT_USER_ZH,
    ),
    "en": PromptSet(
        title_user=_TITLE_USER_EN,
        digest_system=_DIGEST_SYSTEM_EN,
        digest_user=_DIGEST_USER_EN,
        milestones_system=_MILESTONES_SYSTEM_EN,
        milestones_user=_MILESTONES_USER_EN,
        facts_system=_FACTS_SYSTEM_EN,
        facts_user=_FACTS_USER_EN,
        compact_system=_COMPACT_SYSTEM_EN,
        compact_user=_COMPACT_USER_EN,
    ),
}

_STRINGS: dict[Language, UIStrings] = {
    "zh-CN": _STRINGS_ZH,
    "en": _STRINGS_EN,
}


# ─── 公开 API ──────────────────────────────────────────────────────────────

def get_prompts(lang: Language | None = None) -> PromptSet:
    """获取指定语言的 prompt 集合。默认使用全局语言设置。"""
    return _PROMPTS[lang or _current_language]


def get_strings(lang: Language | None = None) -> UIStrings:
    """获取指定语言的 UI 字符串。默认使用全局语言设置。"""
    return _STRINGS[lang or _current_language]
