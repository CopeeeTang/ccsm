# 记忆管理架构调研：Mem0 与 Claude-Mem

> 调研日期：2026-04-03
> 调研目的：为 CCSM 会话压缩/分类/快速恢复提取可复用的架构洞见

---

## 一、Mem0 — AI 记忆层

### 1.1 记忆结构与类型层级

Mem0 定义了三种核心记忆类型（`MemoryType` 枚举）：

| 类型 | 对应概念 | 实现方式 |
|------|----------|----------|
| **Semantic** | 语义记忆——事实性知识 | LLM 提取 facts → 向量化存储，去重合并 |
| **Episodic** | 情景记忆——对话片段 | 按 user/agent/run 三维 scope 存储原始消息 |
| **Procedural** | 程序记忆——操作步骤 | LLM 生成结构化执行历史摘要 |

**核心洞见：三维 Scope 模型**

每条记忆必须绑定至少一个 scope ID：
- `user_id` — 用户维度
- `agent_id` — Agent 维度
- `run_id` — 单次运行维度

这允许同一事实在不同粒度被共享或隔离。类比到 CCSM：一个 session 的知识可以属于 project 级（跨 session 共享）或 session 级（仅本 session 可见）。

### 1.2 信息压缩机制

Mem0 的压缩分两阶段：

**阶段 1：事实提取（Fact Extraction）**

LLM 从对话中提取原子化事实列表。核心 prompt 设计：
- 输入：完整对话历史
- 输出：`{"facts": ["Name is John", "Is a software engineer"]}`
- 区分 User Memory 提取（只看用户消息）和 Agent Memory 提取（只看 Agent 消息）
- 要求事实独立可理解、不含代词、保留原始语言

**关键设计：事实原子化**
每个 fact 是一个自包含的陈述，不依赖上下文就能理解。这是信息压缩的基本单元。

**阶段 2：记忆合并（Memory Update）**

新提取的 facts 与已有记忆对比，LLM 决策四种操作：

| 操作 | 触发条件 | 示例 |
|------|----------|------|
| **ADD** | 全新信息 | "Name is John" → 新增 |
| **UPDATE** | 同一主题但信息更新/更全 | "Likes pizza" → "Loves cheese and chicken pizza" |
| **DELETE** | 新信息与旧信息矛盾 | "Dislikes cheese pizza" → 删除 "Loves cheese pizza" |
| **NONE** | 信息已存在且无变化 | 跳过 |

**UUID 映射防幻觉**：内部将真实 UUID 映射为整数索引传给 LLM，避免 LLM 幻觉出不存在的 ID。

### 1.3 记忆检索机制

**双通道并行检索**：

```
Query → [向量检索] ←→ [图谱检索] → 合并结果
         ↓                ↓
    Embedding 相似度    BM25 重排序
         ↓                ↓
      Score 阈值过滤    Top-N 三元组
         ↓
    可选 Reranker 重排序
```

- **向量检索**：Query embedding → 向量数据库相似度搜索 → 分数过滤
- **图谱检索**：从 query 中提取实体 → 在 Neo4j 中搜索关联关系 → BM25 重排序
- **Reranker**：可选的第二轮排序，用独立的 reranker 模型对结果精排
- **高级过滤**：支持 eq/ne/gt/lt/in/contains 等操作符，支持 AND/OR/NOT 逻辑组合

### 1.4 Memory Graph 概念

Memory Graph 是 Mem0 的核心差异化特性，用 Neo4j 图数据库存储知识图谱：

**数据模型**：
```
Entity Node ──RELATIONSHIP──→ Entity Node
  (source)                      (destination)
  + entity_type                 + entity_type
  + user_id                     + user_id
  + embedding                   + embedding
```

**关键流程**：
1. **实体提取**：LLM tool-calling 从文本提取 `(entity, entity_type)` 对
2. **关系建立**：LLM 建立 `(source, relationship, destination)` 三元组
3. **冲突检测**：搜索已有图谱，找到需要删除的过时关系
4. **图谱更新**：支持 add/update/delete 三种图操作

**冲突处理规则**：
- 相同 source+destination 但不同 relationship → 更新 relationship
- 新信息矛盾旧信息 → 删除旧关系
- 可能存在同类型但不同目标的关系 → 不删除（"Alice loves pizza" 和 "Alice loves burger" 共存）

### 1.5 记忆变更历史

SQLite 存储每条记忆的完整变更日志：
```
history: id | memory_id | old_memory | new_memory | event | created_at | updated_at | actor_id | role
```

每次 ADD/UPDATE/DELETE 都记录 before/after，支持回溯记忆演变过程。

---

## 二、Claude-Mem — Claude Code 会话持久记忆

### 2.1 架构概览

Claude-Mem 是一个 Claude Code 插件，通过 Hook 系统实现自动化记忆采集：

```
Claude Code 会话
  ├── SessionStart hook → 启动 Worker + 注入上下文
  ├── UserPromptSubmit hook → session-init（会话初始化）
  ├── PostToolUse hook → observation（每次工具调用后记录观察）
  ├── Stop hook → summarize（会话暂停时生成摘要）
  └── SessionEnd hook → complete（会话结束标记）
```

**核心组件**：
- **Worker Service** (port 37777)：后台常驻服务，处理观察记录和查询
- **MCP Server**：暴露 search/timeline/get_observations/smart_search/smart_outline/smart_unfold 工具
- **Context Generator**：会话开始时生成上下文注入
- **SQLite DB** (`~/.claude-mem/claude-mem.db`)：持久化存储

### 2.2 数据模型——分层观察体系

**三层数据结构**：

| 层级 | 表 | 内容 | 粒度 |
|------|------|------|------|
| **Session** | `sdk_sessions` | content_session_id, memory_session_id, project, status | 一次完整会话 |
| **Observation** | `observations` | title, subtitle, facts, narrative, concepts, files | 一次工具调用产生的知识 |
| **Summary** | `session_summaries` | request, investigated, learned, completed, next_steps | 一个 prompt 周期的进度 |

**Observation 字段详解**：

```
observation:
  title      — 核心行动/主题的短标题（如 "Authentication now supports OAuth2"）
  subtitle   — 一句话解释（最多 24 词）
  facts[]    — 原子化事实列表（自包含，无代词）
  narrative  — 完整上下文：做了什么、怎么工作、为什么重要
  concepts[] — 知识分类标签（2-5 个）
  files_read[], files_modified[] — 涉及的文件
  type       — 观察类型
  discovery_tokens — 产生此知识的原始 token 成本
```

### 2.3 观察类型分类体系

6 种观察类型 + 7 种概念标签构成二维分类矩阵：

**观察类型**（做了什么）：
| 类型 | 含义 | Emoji |
|------|------|-------|
| bugfix | 修复了一个 bug | 🔴 |
| feature | 新增功能 | 🟣 |
| refactor | 代码重构，行为不变 | 🔄 |
| change | 通用修改（文档、配置等） | ✅ |
| discovery | 了解现有系统 | 🔵 |
| decision | 架构/设计决策 | ⚖️ |

**概念标签**（什么知识）：
how-it-works, why-it-exists, what-changed, problem-solution, gotcha, pattern, trade-off

**关键设计：类型与概念正交**
类型描述"发生了什么"，概念描述"学到了什么"。同一个 bugfix 可能包含 problem-solution + gotcha 两个概念。

### 2.4 三层检索协议（Progressive Disclosure）

Claude-Mem 的核心创新是强制执行的三层渐进式检索：

```
Layer 1: search()         → 索引视图  (~50-100 tokens/条)
  ↓ 人工/AI 筛选
Layer 2: timeline()       → 时间上下文 (anchor 前后 N 条)
  ↓ 人工/AI 筛选
Layer 3: get_observations() → 完整详情 (~500-1000 tokens/条)
```

**Layer 1 — Search（索引）**：
```
| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #11131 | 3:48 PM | 🟣 | Added JWT authentication | ~75 |
```
返回 ID、时间、类型 emoji、标题、预估阅读成本。每条约 50-100 tokens。

**Layer 2 — Timeline（上下文）**：
以某个 observation 为锚点，取前后 N 条，展现时间线上下文。支持自动锚点匹配。

**Layer 3 — Get Observations（详情）**：
批量获取完整 observation 对象（title, subtitle, narrative, facts, concepts, files）。

**Token 经济学**：
- 不过滤直接获取 100 条 = ~50,000-100,000 tokens
- 三层协议获取 5 条相关结果 = ~2,500-5,000 tokens
- **节省约 10-20 倍 token**

### 2.5 Smart Search/Outline/Unfold 模式

这是一套代码探索工具链，基于 tree-sitter AST 解析：

| 工具 | 用途 | Token 成本 |
|------|------|------------|
| `smart_search(query, path)` | 跨目录搜索符号名和文件 | ~2,000-6,000 |
| `smart_outline(file_path)` | 文件结构骨架（签名 + 折叠体） | ~1,000-2,000 |
| `smart_unfold(file_path, symbol)` | 展开单个符号的完整实现 | ~400-2,100 |

**核心原则："先索引，按需获取"**

对比传统 Read 全文件（~12,000+ tokens），smart_outline + smart_unfold 节省 4-8 倍。这个模式直接映射到记忆检索的渐进式展开。

### 2.6 会话摘要（Session Summary）

摘要结构化为五个维度：

```xml
<session_summary>
  <request>用户请求 + 讨论/执行的实质内容</request>
  <investigated>已探索的内容</investigated>
  <learned>学到的系统知识</learned>
  <completed>已完成的工作</completed>
  <next_steps>当前轨迹和下一步</next_steps>
  <notes>额外洞见</notes>
  <files_read>...</files_read>
  <files_edited>...</files_edited>
</session_summary>
```

**关键设计**：next_steps 是"当前正在做的事的延续"，而非"会话后的未来工作"。这对会话恢复极为重要——它告诉下一个 session 精确的续接点。

### 2.7 Context Injection（上下文注入）

会话启动时，Context Generator 自动注入最近活动摘要到 CLAUDE.md：

```markdown
# Recent Activity

### Jan 10, 2026

| ID | Time | T | Title | Read |
|----|------|---|-------|------|
| #39050 | 3:44 PM | 🔵 | Plugin commands directory is empty | ~255 |
```

这是"被动记忆回放"——不需要用户主动搜索，系统自动将最近上下文注入到新会话。

---

## 三、对比分析与架构洞见

### 3.1 信息压缩策略对比

| 维度 | Mem0 | Claude-Mem |
|------|------|------------|
| **压缩单元** | 原子化 fact（一个独立陈述） | 结构化 observation（title/subtitle/facts/narrative） |
| **压缩方法** | LLM 提取 + 去重合并 | LLM 观察者模式 + 分层字段 |
| **去重机制** | 向量相似度 + LLM 判断 ADD/UPDATE/DELETE | content_hash 去重（30 秒窗口内相同内容跳过） |
| **压缩比** | 对话 → facts，约 10-50:1 | 工具调用 → observation，约 5-20:1 |
| **层级** | 扁平 facts + 可选图谱 | title(~10 tokens) → subtitle(~25) → facts(~100) → narrative(~500) |

### 3.2 记忆层级对比

| 维度 | Mem0 | Claude-Mem |
|------|------|------------|
| **短期** | run_id scope 的 facts | 当前 session 的 observations |
| **长期** | user_id scope 的 facts + 图谱 | 跨 session 的 SQLite DB + 向量索引 |
| **情景** | episodic memory（原始对话） | observation timeline（时间线视图） |
| **语义** | semantic memory（提取的 facts）+ graph | concepts 标签 + FTS5 全文搜索 |
| **程序** | procedural memory（执行步骤摘要） | session_summaries（request/investigated/learned/completed/next_steps） |

### 3.3 信息过载解决方案

**Mem0 的方案**：
- 向量相似度排序 + 阈值过滤
- 可选 Reranker 精排
- 图谱提供结构化关联
- 但**没有显式的渐进式展开**——search 一次性返回完整 memory

**Claude-Mem 的方案**：
- **强制三层协议**：search → timeline → get_observations
- **Token 预算意识**：每条结果标注预估阅读成本（~75, ~255 等）
- **时间线锚定**：timeline 工具提供上下文窗口，避免孤立查看
- **被动注入**：Context Injection 自动提供最近活动，不需要主动搜索

### 3.4 可复用的设计模式

#### 模式 1：渐进式披露（Progressive Disclosure）
**来源**：Claude-Mem 三层检索协议
**原理**：先展示低成本索引 → 用户/AI 筛选 → 按需加载详情
**CCSM 应用**：
- Layer 1: 会话列表（title + status + date）~20 tokens/条
- Layer 2: 会话概要（summary 五维度）~100 tokens/条
- Layer 3: 会话详情（milestones + lineage + tool usage）~500 tokens/条

#### 模式 2：原子化事实（Atomic Facts）
**来源**：Mem0 fact extraction
**原理**：每个事实自包含、无代词、可独立理解
**CCSM 应用**：
- 里程碑提取时生成原子化事实
- 每个 fact 附带文件路径和时间戳
- 便于跨 session 搜索和去重

#### 模式 3：观察者分类矩阵（Type x Concept Matrix）
**来源**：Claude-Mem 6 types x 7 concepts
**原理**：类型（做了什么）和概念（学了什么）正交分类
**CCSM 应用**：
- 会话状态（ACTIVE/BACK/IDEA/DONE）是"状态"维度
- 可增加"知识类型"维度：implementation / debugging / research / design / refactor

#### 模式 4：LLM 驱动的记忆合并（Memory Reconciliation）
**来源**：Mem0 ADD/UPDATE/DELETE/NONE 决策
**原理**：新信息与旧信息对比，LLM 判断最优操作
**CCSM 应用**：
- 同一 project 下跨 session 的 milestone 去重/合并
- Fork session 的知识继承和冲突检测

#### 模式 5：被动上下文注入（Passive Context Injection）
**来源**：Claude-Mem Context Generator + SessionStart hook
**原理**：新会话启动时自动注入最近相关活动摘要
**CCSM 应用**：
- 用户启动新 Claude Code 会话时，CCSM 可生成 project 级活动摘要
- 注入最近 3-5 个相关 session 的 summary

#### 模式 6：Token 经济学标注（Token Budget Awareness）
**来源**：Claude-Mem 每条结果标注 `~255` 预估阅读成本
**原理**：让用户/AI 在获取详情前了解成本
**CCSM 应用**：
- 会话详情面板标注预估 token 成本
- 帮助用户决定是否展开完整详情

#### 模式 7：结构化摘要五维度（Five-Dimension Summary）
**来源**：Claude-Mem session_summaries
**原理**：request / investigated / learned / completed / next_steps
**CCSM 应用**：
- 替代或补充当前的自由文本 summary
- 特别是 `next_steps` 对会话恢复至关重要——精确标记续接点

---

## 四、对 CCSM 的建议

### 4.1 会话压缩改进

当前 CCSM 的 summarizer 生成自由文本摘要。建议：

1. **引入结构化摘要**：采用 Claude-Mem 的五维度模型
   - request: 本次会话的核心目标
   - investigated: 探索了什么
   - learned: 发现了什么
   - completed: 完成了什么
   - next_steps: 精确续接点

2. **引入原子化事实**：采用 Mem0 的 fact 提取
   - 从 JSONL 工具调用中提取关键事实
   - 每个 fact 自包含、可搜索
   - 便于跨 session 知识聚合

### 4.2 检索体验改进

1. **三层渐进式检索**：
   - 泳道图 → 会话卡片（title + status） → 详情面板
   - 每层标注预估信息量

2. **时间线锚定**：
   - 在详情面板中提供"前后 session"导航
   - 展示同 project 下的时间线上下文

### 4.3 知识图谱探索

Mem0 的 Memory Graph 提示了一个方向：
- session 之间的 fork/compact 关系本身就是图结构
- 可以在 project 级建立 entity → session 的关联图
- 支持"哪些 session 涉及了 X 模块？"这类图查询

---

## 附录：关键源码位置

### Mem0
- 核心记忆管理：`/tmp/mem0/mem0/memory/main.py` (Memory 类)
- 事实提取 prompt：`/tmp/mem0/mem0/configs/prompts.py` (FACT_RETRIEVAL_PROMPT, UPDATE_MEMORY_PROMPT)
- 记忆类型枚举：`/tmp/mem0/mem0/configs/enums.py` (MemoryType)
- 图谱记忆：`/tmp/mem0/mem0/memory/graph_memory.py` (MemoryGraph)
- 图谱工具定义：`/tmp/mem0/mem0/graphs/tools.py`
- 关系提取 prompt：`/tmp/mem0/mem0/graphs/utils.py` (EXTRACT_RELATIONS_PROMPT)

### Claude-Mem
- Hook 配置：`~/.claude/plugins/cache/thedotmack/claude-mem/10.6.3/hooks/hooks.json`
- 观察类型 + prompt 模板：`~/.claude/plugins/cache/thedotmack/claude-mem/10.6.3/modes/code.json`
- DB Schema + Context Generator：`~/.claude/plugins/cache/thedotmack/claude-mem/10.6.3/scripts/context-generator.cjs`
- 三层检索协议：`~/.claude/plugins/cache/thedotmack/claude-mem/10.6.3/skills/mem-search/SKILL.md`
- Smart Search 模式：`~/.claude/plugins/cache/thedotmack/claude-mem/10.6.3/skills/smart-explore/SKILL.md`
- 执行计划模式：`~/.claude/plugins/cache/thedotmack/claude-mem/10.6.3/skills/make-plan/SKILL.md`
