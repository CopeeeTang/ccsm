# CCSM Round 3 — 会话历史

**项目**: CCSM (Claude Code Session Manager)
**日期**: 2026-04-02
**接续**: Round 2（Codex 安全审查 + 功能增强）
**迭代版本**: v0.3 → v0.4

---

## 背景

本轮由 PM（产品经理朋友）深度体验驱动，完成了从「功能可用」到「体验打磨」的跨越。核心改变是将 Timeline 从原始消息流转变为里程碑叙事，并引入 AI 摘要（Haiku）作为语义理解层。

---

## 阶段 1：PM 体验指南撰写

**动机**: 让产品经理朋友能够有结构地体验 CCSM，而不是漫无目的地乱点。

### 输出文件

- `docs/ccsm-product-review-guide.md` — PM 体验指南，包含：
  - ASCII mockup（完整 TUI 布局，三栏：会话列表 / 卡片 / 详情）
  - 4 条体验路径（导航流、会话切换流、搜索流、Timeline 阅读流）
  - 22 个引导性问题，覆盖信息密度、视觉层次、操作流畅度、设计语言
  - 设计语言对照说明（Claude 风格 vs. 系统终端风格）

---

## 阶段 2：PM 反馈——Timeline 里程碑设计洞察

**PM 核心洞察**: "不是每句话都是 milestone，只有阶段转换点才值得记录。"

### PM 描述的真实工作流

```
讨论 → Plan → 执行 → Review → 验收 → 反馈 → 发布
```

每个箭头都是一个潜在的 milestone 边界。

### 关键设计原则（PM 提出）

| 原则 | 说明 |
|------|------|
| 索引而非全文 | Milestone 是导航锚点，不是消息副本 |
| 折叠展开 | 只展开进行中的阶段，其余收起 |
| 断点优先 | 未完成的任务、被打断的计划是最有价值的信息 |
| 行动导向 | Breakpoint 措辞要告诉用户"下一步做什么"，而非描述"发生了什么" |

### 方案选择

用户选择 **方案 B（紧凑里程碑）**：
- 每个 milestone 一行，状态图标 + 标题 + 时间戳
- 当前进行中展开显示 3 个 sub_items
- Breakpoint 单独橙色高亮框

---

## 阶段 3：Milestone 数据模型 + Detail 面板重写

### 新增数据类型（models/session.py）

```python
class MilestoneStatus(Enum):
    COMPLETED = "completed"    # ✓
    IN_PROGRESS = "in_progress"  # ◎
    BLOCKED = "blocked"        # ✗
    SKIPPED = "skipped"        # ○

@dataclass
class MilestoneItem:
    text: str
    done: bool = False

@dataclass
class Milestone:
    title: str
    status: MilestoneStatus
    timestamp: str
    sub_items: list[MilestoneItem]
    message_range: tuple[int, int]  # (start_idx, end_idx)

@dataclass
class Breakpoint:
    title: str           # 行动导向标题
    context: str         # 简短背景
    next_action: str     # 明确的下一步
```

### Detail 面板重构（tui/widgets/session_detail.py）

**重构前**: 5 区块碎片化（元数据 / 统计 / 标签 / 摘要 / 最后回复）

**重构后**: 4 区块叙事化

```
┌─ SESSION ────────────────────────────────┐
│  会话 ID、时间范围、消息数、活跃度         │
├─ MILESTONES ─────────────────────────────┤
│  ✓ Phase 1  ○ Phase 2  ◎ Phase 3 (展开)  │
│    ├── sub_item 1 ✓                       │
│    ├── sub_item 2 ✓                       │
│    └── sub_item 3 □ (进行中)              │
├─ BREAKPOINT ─────────────────────────────┤  ← 橙色高亮
│  [!] 标题：行动导向描述                   │
│  背景：...  下一步：...                   │
├─ LAST REPLY ─────────────────────────────┤
│  最后一条 assistant 消息前 5 行            │
└──────────────────────────────────────────┘
```

**CSS 新增**: `breakpoint` 橙色高亮框样式（`border: solid $warning`）。

---

## 阶段 4：Rule-based Milestone 提取器 v1 → v2

### v1 问题

`plan_produced` 信号依赖"Claude 回复中含列表"检测，但 Claude 几乎每次回复都有列表，导致大量**假阳性**。

### v2 改进（core/milestones.py）

**完全移除 `plan_produced`**，只检测 **user 消息**中的 6 类语义信号：

| 信号类型 | 触发词示例 | 语义含义 |
|----------|-----------|---------|
| `topic_switch` | "接下来讨论", "换个话题", "下一步" | 主动切换话题 |
| `confirm_pivot` | "OK", "好的", "明白了" + 后续内容 | 确认后转折 |
| `exec_command` | "执行", "开始实现", "帮我写" | 进入执行阶段 |
| `review_enter` | "看一下", "检查", "review" | 进入审查阶段 |
| `wrap_up` | "总结", "回顾", "整理一下" | 阶段收尾 |
| `slash_cmd` | `/save-session`, `/commit` | 显式阶段命令 |

**去噪与合并规则**:
- 同一信号类型 5 条消息内不重复触发
- 连续两个相邻 milestone 合并（消息间距 < 3）
- 最终 milestone 数量剪枝到 3~15 个

### 真实数据测试结果

| 会话 | 消息数 | 提取里程碑数 | 耗时 |
|------|--------|-------------|------|
| Session A | 733 msgs | 10 milestones | 0.04s |
| Session B | 311 msgs | 2 milestones | 0.02s |
| Session C | 339 msgs | 3 milestones | 0.02s |

---

## 阶段 5：Haiku LLM 摘要对比

### 本地代理发现

发现本地代理 `127.0.0.1:4142` 支持 `claude-haiku-4.5`，可通过 OpenAI 兼容 API 调用。

### Rule-based vs. Haiku 对比

**Rule-based 输出**（示例）:
```
Milestone 1: exec_command [msg 12-45]
Milestone 2: review_enter [msg 89-134]
...（原文截断）
```

**Haiku 输出**（同一会话）:
```json
{
  "milestones": [
    {
      "title": "确定 Timeline 数据模型设计方向",
      "status": "completed",
      "sub_items": ["讨论 3 种方案", "用户选择方案 B", "完成原型"],
      "insights": "从平铺消息流到里程碑叙事的关键决策点"
    },
    ...
  ],
  "breakpoint": {
    "title": "Haiku 摘要集成待验收",
    "context": "LLM 摘要已实现但未与 TUI 联通",
    "next_action": "运行 TUI 验证 Detail 面板渲染效果"
  }
}
```

**质量差距**: Haiku 生成「语义概括」，Rule-based 只做「原文截断」。

| 指标 | Rule-based | Haiku |
|------|-----------|-------|
| 耗时 | 0.04s | 20.66s |
| 里程碑数 | 10 | 8 |
| 质量 | 原文片段 | 语义概括 |
| sub_items | ✗ | ✓ |
| breakpoint | ✗ | ✓（行动导向）|
| insights | ✗ | ✓ |

---

## 阶段 6：PM v0.4 完整改进方案 + 并行实施

### PM 4 阶段改进方案

**阶段 1：视觉降噪（已实施）**
- 面板比例 18/38/44 → 15/35/50（Detail 面板更大）
- 边框降噪：减少 box-drawing 字符，保留结构感
- 橙色克制：仅 Breakpoint 使用，其余用灰色

**阶段 2：叙事化详情**
- Milestone 渐进折叠：已完成收起，进行中展开
- Breakpoint 行动导向措辞

**阶段 3：功能演进（待实施）**
- 全局搜索（跨会话关键词）
- 批量操作（多选归档 / 标签）

**阶段 4：技术雷区（注意事项）**
- 东亚字符宽度对齐问题
- 大型会话（5000+ 消息）的滚动性能

### 并行实施（subagent 派发）

同时派发 3 个 subagent + 主线程：

| 任务 | 负责 | 文件 |
|------|------|------|
| css-refactor | subagent-1 | `tui/styles/claude_native.tcss` |
| card-refactor | subagent-2 | `tui/widgets/session_card.py` |
| detail-refactor | subagent-3 | `tui/widgets/session_detail.py` |
| 静默 AI 摘要 | 主线程 | `tui/screens/main.py` |

**静默 AI 摘要机制**:
- 用户 hover 会话卡片 1.5 秒后自动触发 Haiku
- 摘要结果写入内存缓存，避免重复调用
- Detail 面板更新无需用户手动刷新

### 卡片三行流重构（session_card.py）

**重构前**（4 行）:
```
[会话 ID]              [状态]
[时间戳]               [消息数]
[标签列表]
[摘要预览...]
```

**重构后**（3 行）:
```
[会话 ID]   [状态图标] [消息数] [时间差]
[摘要预览前 60 字...]
[标签...]
```

---

## 关键数据汇总

| 指标 | 旧值 | 新值 |
|------|------|------|
| 面板比例 | 18/38/44 | 15/35/50 |
| 卡片行数 | 4 行 | 3 行（三行流）|
| Detail 区块数 | 5 区块 | 4 区块（叙事化）|
| 里程碑提取耗时 | N/A | 0.04s（rule）/ 20.66s（haiku）|
| Haiku 摘要质量 | N/A | 8 milestones + sub_items + breakpoint |

---

## 文件变更清单

| 文件 | 状态 | 变更摘要 |
|------|------|---------|
| `docs/ccsm-product-review-guide.md` | 新增 | PM 体验指南，ASCII mockup + 22 个引导问题 |
| `models/session.py` | 改写 | 新增 Milestone / MilestoneItem / MilestoneStatus / Breakpoint |
| `core/milestones.py` | 新增 | Rule-based 提取器 v2，6 类信号，去噪+合并+剪枝 |
| `core/summarizer.py` | 新增/改写 | 双模式：extract（rule）+ llm（haiku）|
| `tui/widgets/session_detail.py` | 改写 | 4 区块叙事化，渐进折叠，行动导向 Breakpoint |
| `tui/widgets/session_card.py` | 改写 | 三行流紧凑布局（4 行→3 行）|
| `tui/styles/claude_native.tcss` | 改写 | 视觉降噪，面板比例 15/35/50，橙色克制 |
| `tui/screens/main.py` | 改写 | 静默 AI 摘要，1.5s hover timer，内存缓存 |

---

## 设计决策记录

### 为什么选择 Rule-based + LLM 双模式？

- Rule-based：秒级响应，适合列表快速渲染，不依赖外部 API
- Haiku LLM：语义级质量，适合 Detail 面板深度展示，20s 可接受（hover 触发）
- 两者互补：列表用 Rule，Detail 用 Haiku

### 为什么 Breakpoint 用橙色？

- 橙色 = 警告/注意，但不像红色那样"出错"
- 只有 Breakpoint 使用橙色，其他元素用灰/白/蓝，降低视觉噪音
- PM 反馈：橙色高亮框让"断点"信息在视觉上自然弹出

### 为什么移除 plan_produced 信号？

- Claude 几乎每次回复都包含有序列表
- 导致 v1 提取器在 733 条消息中产生 30+ 假阳性 milestone
- v2 完全依赖 user 消息检测，user 切换话题的信号比 assistant 更可靠

---

## 下一步

1. **实际跑 TUI**：验证 v0.4 视觉效果，重点看 Detail 叙事化 + Breakpoint 橙色框
2. **PM v0.4 方案第二阶段微调**：Milestone 折叠动画、sub_items 缩进层次
3. **PM v0.4 方案第三阶段**：全局搜索（跨会话关键词检索）
4. **东亚字符宽度问题**：处理中文/日文字符在 Textual 中的对齐偏移
5. **大型会话性能**：5000+ 消息会话的滚动流畅性测试

---

*保存于 2026-04-02 | Round 3 结束*
