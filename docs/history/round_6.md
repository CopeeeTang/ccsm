# CCSM Round 6 — 会话历史摘要

**日期**: 2026-04-02
**承接**: Round 4（PM反馈优化）+ Round 5（v1 lineage/search）
**目标**: CCSM v0.4→v1.0 UI/功能完善 + Claude Code 源码审计修复

---

## 阶段1：PM 反馈 6 Task 并行执行

### 背景
PM 提出 6 个改进点，采用 worktree 隔离并行派发。

### Task 清单

| Task | 模块 | 内容 |
|------|------|------|
| Task1 | `parser.py` `_sanitize_content` | XML 脏数据清洗 |
| Task2 | `session_card.py` `render` 重写 | 卡片2行精准对齐 |
| Task3 | `session_detail.py` | Detail 压缩 + 里程碑定宽 |
| Task4 | CSS/tcss | BREAKPOINT 留白 + padding 调整 |
| Task5 | `session_list.py` `StatusTabBar` | Tab 状态切换 |
| Task6 | `summarizer.py` `generate_ai_title` | AI 摘要标题生成 |

### 执行过程
- 第一轮：Task2/3 被用户中断，"先验证 Task1"
- 第二轮：补派 Task2/3，最终全部完成
- Codex review 发现：
  - **3个P0**：ReDoS 正则、`.format()` 注入、Tab 点击区域过小
  - **10个P1**：类型提示、边界判断等
- 修复全部 P0 后合入

---

## 阶段2：TUI 视觉验证

- 用 Textual test mode 截图验证四区块布局（Header / Tab / Card列表 / Detail）
- 发现 `"Base directory for this skill"` 漏过 sanitizer → 修复
- 使用 memory worktree 中的 422msg session 演示 Haiku LLM 效果：
  - **AI Title**: `"整理GPT5.2数据并对比分析"` （2.7s）
  - **LLM Milestones**: 7个语义阶段（12.1s）
  - **Rule-based**: 10个信号节点（0.0s）
- 用户 prompt：`"跑一下haiku看看效果"`

---

## 阶段3：AI 标题/意图集成到卡片

### 变更
- `SessionMeta` 新增 `ai_intent` 字段
- 卡片第二行优先显示 `meta.ai_intent`（fallback 到 rule-based intent）
- Detail 面板 Intent 行同样优先使用 AI 意图

### Bug 修复
- `Widget.notify()` positional args bug（lambda 包裹解决）
- 用户 prompt：`"notify那个bug要修"`

---

## 阶段4：README + Pipeline 讲解

- 撰写完整 `README.md`（features / architecture / shortcuts / security）
- 技术路线 Pipeline 全景图（8个Stage）：
  1. JSONL 解析 → 2. 内容清洗 → 3. Rule 信号提取 → 4. LLM 增强 → 5. 摘要生成 → 6. 标题/意图 → 7. 存储 → 8. TUI 渲染
- 产品路线分层设计（L0-L4 渐进增强）：
  - L0: 纯 Rule-based，离线，零 API
  - L1: AI Title + Intent（Haiku）
  - L2: LLM Milestones（批量）
  - L3: 搜索 / lineage
  - L4: 多会话对比分析
- Rule-based vs API 位置对比表

---

## 阶段5：Claude Code 源码审计

### 背景
用户 prompt：`"派一个 Opus agent，对照 /home/v-tangxin/github/claude-code/ 源码，审计我们的 parser"`

### 审计发现（5个模块）

| 问题 | 级别 | 描述 |
|------|------|------|
| 遗漏 JSONL type | P0 | 15+ 种 type 未处理（tool_use_partial、compact 等） |
| PID 验证缺失 | P0 | process status 仅靠字段，未用 `os.kill` 验证 liveness |
| fork 大小写 | P1 | `isFork` 字段名大小写不匹配 Claude Code 实际输出 |
| display_name 来源 | P1 | 错误来源字段，应为 `humanTurn.display` |
| compact 前缀 | P2 | `isCompactSummary` 判断逻辑过时 |

---

## 阶段6：审计修复执行（7 Task）

worktree 隔离，7 Task 分批执行：

| Task | 内容 |
|------|------|
| Task1 | PID liveness（`os.kill`）+ kind 字段补全 |
| Task2 | custom-title / ai-title 解析修复 |
| Task3 | fork case-insensitive + `forkedFrom` + microcompact |
| Task4 | BACKGROUND 状态经 PID kind 判断 |
| Task5 | +9 slash commands + EN discourse markers |
| Task6 | `isCompactSummary` 跳过逻辑 |
| Task7 | display_name 来源字段修正 |

---

## 阶段7：仓库整理 + Codex Review + Push

### 仓库管理
- 确认 CCSM 有独立仓库 `CopeeeTang/ccsm`
- 主仓库 `.gitignore` 添加 `projects/ccsm/`
- 恢复 CCSM 独立 `.git`，push 审计修复分支

### Codex Review 发现（2个P2）
1. `classify_all` 未传 `all_running` 参数
2. 新字段未拷贝到 live `SessionInfo` 对象

### 修复
- 两个 P2 修复后 push
- 最终 commit：`1a4ef0a`（审计修复）→ `36034c7`（Codex review fix）

---

## 关键数据总结

| 指标 | 数值 |
|------|------|
| 审计发现问题 | 15+ 遗漏 JSONL type，3个 P0 安全问题 |
| LLM 里程碑质量 | 7 个语义阶段（vs Rule-based 10 个信号碎片）|
| Haiku 延迟（title） | 2.7s |
| Haiku 延迟（summary） | 12.1s |
| 最终 CCSM commit | `36034c7` |
| PM Task 总数 | 6个 Task + Codex 修复 13个P |

---

## 遗留 / 下一步

- [ ] Task3 Detail 里程碑宽度在超长 session 下的显示压力测试
- [ ] L2 LLM Milestones 批量生成模式（避免单 session 12s 等待）
- [ ] lineage 图谱与 fork 关系的 TUI 可视化（Round 5 遗留）
- [ ] `classify_all` 大规模并发压力测试

---

*Round 6 结束。下一轮建议从 L2 批量 LLM Milestones 或 TUI lineage 可视化切入。*
