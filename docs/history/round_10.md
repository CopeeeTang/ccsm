# Round 10 — AI 角色评估 & Detail 面板"面向恢复"重设计

**日期**: 2026-04-03  
**项目**: CCSM (Claude Code Session Manager)  
**仓库**: `/home/v-tangxin/GUI/projects/ccsm`  
**核心主题**: 评估 AI 在 pipeline 中的角色，将 Detail 面板从"面向回顾"重设计为"面向恢复"

---

## 阶段 1：全景评估——AI 在 8 阶段 Pipeline 中的角色

读取全部 9 轮历史（round_1 ~ round_9），系统梳理 AI 在各阶段的参与情况：

| Pipeline 阶段 | 当前 AI 参与 | 评估 |
|---|---|---|
| 发现 / 解析 | ❌ 纯规则 | 合理 |
| 血缘检测 | ❌ 纯规则 | 合理 |
| 状态分类 | ❌ 时间阈值 | 可优化 |
| 里程碑提取 | ⚠️ 部分 AI | 可优化 |
| 标题生成 | ✅ LLM | 已实现 |
| 摘要生成 | ✅ LLM | 已实现 |
| 聚类命名 | ✅ LLM | 已实现 |
| 搜索索引 | ❌ 纯文本 | 合理 |

**发现**：AI 当前仅做"文本美化"（标题/意图/命名），不参与任何决策环节。

**提出 6 个 AI 升级方向**：
1. 智能状态推断（ACTIVE/BACK/IDEA/DONE）
2. 跨会话问题追踪
3. Resume 推荐排序
4. 实验血缘识别
5. 知识迁移提炼
6. 元规划（任务优先级）

---

## 阶段 2：用户需求聚焦

用户从 6 个方向中明确聚焦三个：

1. **状态推断优化**：输入应是用户消息（而非工具调用），判断依据更准确
2. **主题归类**：超越 compact/fork 的纯规则聚类，支持语义层面的 session 归组
3. **Detail 内容改进**：核心痛点是"无断点恢复"——打开 Detail 就能知道从哪继续

**关键修正**：
- 用户不需要"复制 resume prompt"，而是直接点进 session 就能 resume 进入 Claude Code
- `isCompactSummary` 字段被当前代码 `if data.get("isCompactSummary"): continue` 完全跳过——这是被忽视的金矿

---

## 阶段 3：数据探查——发现 compact summary 的价值

对真实 JSONL 数据扫描结果：

| 指标 | 数值 |
|---|---|
| compact summaries 总数 | 95 个 |
| 包含 7 标准段落的比例 | 97.9% |
| last-prompt 记录数 | 2884 个 |
| tool_use blocks 数 | 912 个 |

**7 个标准段落结构**（Claude Code 自动生成）：
1. Primary Request and Intent
2. Key Technical Concepts
3. Files and Code Sections
4. Problem Solving Approach
5. Pending Tasks and Next Steps
6. Current Work in Progress
7. Summary (可选)

用户已准备好设计文档：`docs/plans/2026-04-03-detail-panel-redesign.md`

---

## 阶段 4：布局优先级确认

用户最终确认的 Detail 面板 6 区域优先级：

| 优先级 | 区域 | 数据来源 |
|---|---|---|
| 1 | 🧭 MILESTONES | compact summary 段落 → AI 对用户消息判断 |
| 2 | 📝 CONTEXT SUMMARY | compact 的 Primary Request + Key Concepts |
| 3 | 📍 WHERE YOU LEFT OFF | last prompt / last insight |
| 4 | 📋 SESSION | 元数据辅助视图（最上层） |
| 5 | 🔧 WHAT WAS DONE | compact 的 Problem Solving（可折叠） |
| 6 | 💬 LAST EXCHANGE | 最后一轮对话（可折叠） |

**r 键一键 resume**：从本轮计划中移除，延后实现。

---

## 阶段 5：6 Phase 实施

### Phase 1 — Model 扩展
- `SessionInfo` 新增 6 个字段：`compact_summaries`、`last_prompt`、`tool_use_blocks`、`model`、`token_usage`、`last_user_message`
- 新增 `CompactSummaryParsed` dataclass（7 段落结构化）
- 新增 `SessionDetailData` dataclass（聚合 Detail 面板所需数据）

### Phase 2 — Parser 增强
- `parser.py` 扩展：提取 compact_summaries / last_prompt / tool_use / model / usage / last_user_message
- 解析时不再跳过 `isCompactSummary` 条目

### Phase 3 — Compact Parser
- 新增 `compact_parser.py`
- 结构化解析 7 段落内容
- 从 Pending Tasks / Current Work 中提取里程碑进度（4 层：目标→已解决→进行中→待完成）

### Phase 4 — Detail 面板重写
- `session_detail.py` 完全重写
- 6 区域恢复优先布局
- `Collapsible` 折叠组件实现 WHAT WAS DONE + LAST EXCHANGE

### Phase 5 — AI 增强
- compact summary 二次提炼 prompt（从 7 段落生成精炼摘要）
- 状态推断增强 `_infer_from_compact()`：有 Pending Tasks → BACK，无 → DONE

### Phase 6 — 测试
- 新增 8 个测试（compact parser + detail data）
- 全部 48 原有测试保持通过
- 最终结果：**56 passed**

---

## 阶段 6：Codex Review + 修复

使用 Codex GPT-5.4 进行代码审查，发现 **2 High + 3 Medium** 问题，全部修复：

| 级别 | 问题 | 修复方式 |
|---|---|---|
| H1 | `_running` 类型注解 `dict[str,bool]` 与实际 `dict[str,dict]` 不匹配 | 修正类型注解 |
| H2 | `_read_tail_lines()` 当 chunk_size == file_size 时无限循环 | 添加 break 退出条件 |
| M1 | 静默 LLM 摘要因缓存策略永远不触发 | 缓存命中条件加 mode 匹配 |
| M2 | `classify_all` 在时间戳修正之前执行 | 移动 lineage 扫描到 classify 之前 |
| M3 | `parse_session_messages()` 未过滤 compact/meta 条目 | 添加过滤逻辑 |

---

## 阶段 7：前端对接修复

发现并修复 TUI 层的对接问题：
- `_run_llm_summarize` 回调未传递 `detail_data` 和 `compact_parsed`
- 新增 `Collapsible` widget 暗色主题样式（CSS 补充）
- `main.py` 传播 6 个新 `SessionInfo` 字段到 Detail 面板

---

## 关键数据

| 指标 | 数值 |
|---|---|
| 测试通过数 | 56 passed（48 existing + 8 new） |
| 代码变更 | +1376 行 / -250 行 |
| 新增文件 | `compact_parser.py`、`test_compact_parser.py` |
| 重写文件 | `session_detail.py` |
| Commits | `87fe0f4`（主功能）+ `6a52d4b`（前端修复） |
| Codex 发现 | 2H + 3M，全部修复 |

---

## 关键决策

| 决策 | 选择 | 原因 |
|---|---|---|
| 数据源优先级 | compact summary > last-prompt > rule-based > AI | 零成本数据优先，AI 只做补漏 |
| Milestone 数据源 | compact 段落提取 | 97.9% 覆盖率，4 层进度结构 |
| Detail 布局核心 | MILESTONES 第一优先级 | 用户确认：进度概览比 context 更重要 |
| 折叠设计 | WHAT WAS DONE + LAST EXCHANGE 折叠 | 辅助信息默认收起，减少噪音 |
| 状态推断增强 | 从 compact 的 Pending Tasks 推断 | 比时间阈值更语义准确 |
| summarize 缓存 | 按 mode 匹配 | 防止 extract 缓存阻塞 LLM 升级路径 |

---

## 下一步计划

- [ ] TUI 实际运行截图验证（端到端视觉确认）
- [ ] r 键一键 resume 功能（用户要求延后实现）
- [ ] AI 对 compact summary 二次提炼的实际效果验证
- [ ] 跨 session 主题归类（Phase 7，独立实施，超越 compact/fork 规则）

---

## 本轮产出文件

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `ccsm/core/compact_parser.py` | 新增 | 7 段落结构化解析 + milestone 提取 |
| `ccsm/tui/session_detail.py` | 重写 | 6 区域恢复优先布局 |
| `ccsm/models/session.py` | 扩展 | +6 字段 + 2 dataclass |
| `ccsm/core/parser.py` | 增强 | 不再跳过 compact summary |
| `ccsm/core/summarizer.py` | 修复 | 缓存 mode 匹配 + AI 增强接口 |
| `ccsm/core/status.py` | 增强 | compact 推断状态 |
| `ccsm/tui/main.py` | 修复 | 传播新字段 + 修复回调 |
| `tests/test_compact_parser.py` | 新增 | 8 个新测试 |
| `docs/plans/2026-04-03-detail-panel-redesign.md` | 新增 | Detail 重设计规格文档 |
