# CCSM Round 14 — AI Digest + Session Facts + 渐进式披露实施

**日期**：2026-04-03
**状态**：全部 8 Phase 代码完成，TUI 启动正常，待用户实测 Digest 生成质量

---

## 会话目标

调研 Memory 系统（Mem0、Claude-mem、M-Flow）的架构模式，借鉴其信息压缩与渐进式披露理念，为 CCSM 设计并实施：

1. **AI Digest**：五维结构化摘要（goal / progress / breakpoint / next_steps / blocker）
2. **Session Facts**：原子化事实提取（基于已有结构化数据，LLM 成本极低）
3. **Detail 面板渐进式披露**：SESSION + DIGEST + MILESTONES 固定展开，其余全部 Collapsible 折叠

---

## 阶段一：调研三大 Memory 系统

读取 round_10~12 历史文档后，对以下三个系统做深度调研：

### Mem0
- 核心机制：原子化 facts + ADD/UPDATE/DELETE 三操作合并
- 每条记忆是独立的 fact 字符串，LLM 负责判断增删改
- 适合长期跨会话记忆管理，但对单 session 摘要粒度偏粗

### Claude-mem
- 五维结构化摘要：`request / investigated / learned / completed / next_steps`
- 三层渐进披露：search → timeline → detail，与 CCSM 的 Card → Detail 架构高度吻合
- **关键借鉴**：命名映射为 `goal / progress / breakpoint / next_steps / blocker`

### M-Flow
- 倒锥图（Inverted Cone）拓扑：Entity → FacetPoint → Facet → Episode
- Graph-Routed Bundle Search：意图感知 → 选择最优搜索路径
- 最具启发性：倒锥搜索思路可指导未来 CCSM 搜索索引升级

---

## 阶段二：方案设计讨论

与用户确认以下关键决策：

| 议题 | 决策 |
|------|------|
| Digest 输入源 | 全部用户消息 + 部分 assistant 输出（优先 compact summary） |
| Card 层 UI | 不变 |
| Detail 首屏布局 | SESSION 卡片化展开 + AI DIGEST 展开 + MILESTONES 展开，其余全折叠 |
| Milestone 信号检测 | 不改，沿用现有规则 |
| Session Facts | 做，但基于已有结构化数据而非原始消息 |
| 存储层 | 不改（sidecar JSON），留 `batch_preprocess()` 接口桩 |

---

## 阶段三：Plan Mode 制定实施计划

使用 Plan Mode 制定 **8 Phase** 实施计划，写入：
`docs/plans/2026-04-03-ai-digest-implementation.md`

| Phase | 内容 |
|-------|------|
| 1 | 数据模型：SessionDigest + SessionFact dataclass |
| 2 | 序列化：meta.py 支持 digest/facts 读写 |
| 3 | Digest 生成：summarizer.py LLM 调用链 |
| 4 | Facts 提取：summarizer.py 原子化事实提取 |
| 5 | Pipeline 集成：main.py 链式调用 |
| 6 | UI 重构：session_detail.py 渐进式布局 |
| 7 | CSS 样式：claude_native.tcss 新增类 |
| 8 | 批量接口桩：batch_preprocess() 占位 |

---

## 阶段四：Mode C 串行实施

### Phase 1 — 数据模型（`ccsm/models/session.py`）

新增两个 dataclass：

```python
@dataclass
class SessionDigest:
    goal: str = ""           # 本次会话想解决什么问题
    progress: str = ""       # 做了哪些工作，达成了什么
    breakpoint: str = ""     # 在哪里中断，context 是什么
    next_steps: str = ""     # 下一步要做什么
    blocker: str = ""        # 当前阻碍或未解决的问题

@dataclass
class SessionFact:
    content: str = ""        # 事实内容
    fact_type: str = ""      # decision / finding / constraint / artifact
    source: str = ""         # 来源标注
```

`SessionSummary` 扩展 `digest: Optional[SessionDigest]` 和 `facts: List[SessionFact]`，全部用 `Optional` + 默认值确保向后兼容。

### Phase 2 — 序列化（`ccsm/core/meta.py`）

在 `_summary_to_dict()` 和 `_dict_to_summary()` 中添加 digest/facts 读写逻辑：
- 旧文件无 `digest`/`facts` 键时，字段为 `None`/`[]`，不报错
- 验证：向后兼容 ✓（旧 `.summary.json` 正常加载）

### Phase 3 — Digest 生成（`ccsm/core/summarizer.py`）

新增三个关键函数：

- `_format_messages_for_digest(messages)`：用户消息截取 500 字，assistant 消息截取 200 字，总体限制 20KB
- `_DIGEST_SYSTEM_PROMPT`：五维结构 JSON 格式 prompt，要求返回 `{"goal":..., "progress":..., ...}`
- `generate_digest(session_id)` / `generate_digest_sync(session_id)`：async + sync 双版本

Digest 输入优先使用 compact summary（信息保真度最高），辅以全部用户消息。

### Phase 4 — Facts 提取（`ccsm/core/summarizer.py`）

新增：

- `_FACTS_SYSTEM_PROMPT`：要求从已有结构化数据中提取 3-7 条原子化事实，JSON 数组格式
- `extract_facts(session_id)` / `extract_facts_sync(session_id)`：输入为 compact + milestones + digest，LLM 成本极低
- `batch_preprocess(session_ids)` 桩：接口占位，待后续批量实现

### Phase 5 — Pipeline 集成（`ccsm/tui/screens/main.py`）

在 `_run_llm_summarize()` 中实现链式调用：

```
summary → digest → facts → 一次性 save_summary()
```

三步顺序执行，任意一步失败不影响其他步骤（try/except 隔离）。

### Phase 6 — UI 重构（`ccsm/tui/widgets/session_detail.py`）

重构 `_rebuild()` 为 **7 区域渐进式布局**：

1. `_mount_session_card()` — SESSION 元信息卡片（固定展开）
2. `_mount_digest_section()` — AI DIGEST 五维展示（固定展开）
3. MILESTONES — 固定展开
4. CONTEXT — Collapsible，初始 `collapsed=True`
5. WHERE LEFT OFF — Collapsible，初始 `collapsed=True`
6. FACTS — Collapsible，初始 `collapsed=True`
7. RAW INFO — Collapsible，初始 `collapsed=True`

### Phase 7 — CSS 样式（`ccsm/tui/styles/claude_native.tcss`）

新增两个样式类：
- `.det-session-card`：SESSION 卡片背景色、边框、内边距
- `.det-digest-section`：DIGEST 区域标题与内容样式

---

## 阶段五：Bug 修复（Gemini 回归）

测试 TUI 启动时发现两处由 Gemini 实现计划（`implementation_plan.md.resolved`）引入的回归：

### 回归 1：`session_card.py` — meta 参数被删
- **症状**：`TypeError: _mount_session_card() got an unexpected keyword argument 'meta'`
- **原因**：Gemini 删除了 `meta` 参数，但 `_rebuild()` 仍传 `meta=meta`
- **修复**：恢复 `meta` 参数定义

### 回归 2：`lineage_group.py` — last_thoughts 参数缺失
- **症状**：`TypeError: missing required argument 'last_thoughts'`
- **原因**：Gemini 未同步更新 lineage_group 中的 card 构造调用
- **修复**：补充 `last_thoughts=...` 参数传递

修复后：**67 个测试全部通过，TUI 启动正常**。

---

## 关键数据

| 指标 | 数值 |
|------|------|
| 修改文件数 | 8 个 |
| 新增代码行数 | ~540 行 |
| 新增 dataclass | 2 个（SessionDigest, SessionFact） |
| 新增 LLM 函数 | 4 个（generate_digest ×2, extract_facts ×2） |
| 修复回归 | 2 处（meta 参数、last_thoughts 参数） |
| 测试通过率 | 67/67（100%） |

---

## 文件变更清单

| 文件 | 变更内容 | 净增行数 |
|------|----------|----------|
| `ccsm/models/session.py` | +SessionDigest, +SessionFact, SessionSummary 扩展 | +42 |
| `ccsm/core/meta.py` | digest/facts 序列化与反序列化 | +30 |
| `ccsm/core/summarizer.py` | Digest 生成 + Facts 提取 + batch 桩 | +380 |
| `ccsm/tui/screens/main.py` | Pipeline 链式调用 summary→digest→facts | +20 |
| `ccsm/tui/widgets/session_detail.py` | 7 区域渐进式布局 + mount 函数 | +50/-30 |
| `ccsm/tui/styles/claude_native.tcss` | .det-session-card, .det-digest-section | +18 |
| `ccsm/tui/widgets/session_card.py` | 修复 meta 参数回归 | ~0 |
| `ccsm/tui/widgets/lineage_group.py` | 修复 last_thoughts 参数回归 | ~0 |

---

## 架构决策记录

### ADR-1：Digest 输入策略
- **决策**：优先使用 compact summary，辅以全部用户消息
- **原因**：compact summary 信息保真度最高，用户消息补充细节；避免解析原始 JSONL 的复杂性
- **截断策略**：用户消息 500 字/条，assistant 200 字/条，总体 20KB

### ADR-2：Facts 提取输入源
- **决策**：基于已有结构化数据（compact + milestones + digest）而非原始消息
- **原因**：结构化数据已经过压缩，LLM Token 消耗极低；避免重复解析
- **输出**：3-7 条原子化 fact，类型为 decision / finding / constraint / artifact

### ADR-3：向后兼容策略
- **决策**：所有新字段用 `Optional` + 默认值
- **原因**：旧 `.summary.json` 无 `digest`/`facts` 键时不报错，静默返回 None/[]
- **验证**：现有 67 个测试全部通过，无回归

### ADR-4：渐进式披露层级
- **决策**：SESSION + DIGEST + MILESTONES 固定展开，其余全部 Collapsible 初始折叠
- **原因**：恢复工作现场最需要这三块；其余按需展开降低认知负担
- **参考**：Claude-mem 的三层披露哲学

---

## 当前状态

- ✅ 所有 8 Phase 代码实施完成
- ✅ 67 个测试全部通过
- ✅ TUI 启动正常，session 列表渲染正确
- ✅ AI Digest 生成链路就绪（按 `s` 触发）
- ⏳ 待用户实际按 `s` 测试 Digest 生成质量
- ⚠️ LLM 代理地址需确认（代码当前用 `http://127.0.0.1:4142`）

---

## 下一步

1. **实测 Digest**：选择有 compact summary 的 session 按 `s`，验证五维摘要质量
2. **确认代理地址**：核查 summarizer.py 中 LLM endpoint 是否指向正确代理
3. **Facts 搜索集成**：将 SessionFacts 内容纳入全文搜索索引
4. **批量预处理**：实现 `batch_preprocess()` 对历史 sessions 批量生成 Digest+Facts
5. **UI 细化**：根据实测反馈调整 Digest 展示格式与截断策略

---

## 背景参考

- **前序**：Round 13 完成 TUI List 渐进式加载与 lineage tree 分组，本轮在 Detail 面板纵深
- **调研基础**：Round 10-12 建立的 milestone 系统和 compact summary 机制
- **下游影响**：Digest/Facts 将成为未来 "会话恢复引导" 和 "跨 session 搜索" 的数据基础
