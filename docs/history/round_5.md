# CCSM Round 5 — Resume 痛点驱动的核心增强 + 双重代码审查

> 日期: 2026-04-02
> 会话轮数: 约 40+ 轮
> 主要方向: 从 8 个 Claude Code resume 真实痛点出发，系统设计并并行实现血缘检测、时间戳解析、全文索引、DAG 可视化等核心模块，完成双重代码审查并通过真实数据 Smoke Test

---

## 一、会话目标

用户在实际使用 Claude Code 过程中积累了 8 个 resume 痛点，基于已调研的 3 份 Claude Code 源码研究文档，设计并实现一套系统性增强。目标是：9 个 Task 全部完成、33 个测试全过、两轮代码审查发现并修复所有 P0/P1，最终用真实 worktree 数据验证。

---

## 二、行动路线

### 阶段 1：痛点分析 + 实施计划

**Prompt**:
> 我在使用 Claude Code 过程中遇到了 8 个 resume 痛点：
> 1. Fork 不可区分 — resume 后 fork 出来的子 session 和原始 session 在列表里一模一样，无任何血缘标记
> 2. 标题还原 — compact 后 Claude 会重新生成一个标题，导致我之前手动起的标题被覆盖
> 3. Worktree 列表乱 — 多 worktree 下同一个项目开了 N 个 session，没有任何分组/排序依据
> 4. 搜索无用 — 现有搜索只匹配标题，消息内容完全搜不到
> 5. Compact 幽灵 session — compact 后原 session 还留着，不知道哪个是"真"的
> 6. 时间轴变动 — compact 操作会改变 session 的 created_at，时间轴因此错乱
> 7. 多 SSH 重复 — 同一台机器从不同 SSH 连接进来，会话重复出现
> 8. 缺少树状图 — 没有任何工具能可视化"这个 session 是从哪个 fork 出来的"

**探索过程**:
- 调取 3 份 Claude Code 源码研究文档（SQLite 数据库结构、session JSON 格式、compact 机制分析）
- 逐一分析每个痛点的根因，映射到具体的数据字段或行为
- fork 痛点根因: `parentSessionUuid` 字段存在但未暴露给用户
- compact 标题覆盖: compact 后 `name` 字段被重新写入，无锁定机制
- 时间轴错乱: compact 会创建新的 JSONL 文件，`created_at` 重置
- 搜索局限: 现有 index 仅索引 `name` 字段，`messages[]` 未涉及
- 设计 9-Task 实施计划，使用 writing-plans skill 输出完整文档

**结果**:
- 计划文件保存到 `docs/superpowers/plans/2026-04-02-ccsm-resume-painpoints.md`
- 9 个 Task 覆盖：数据模型扩展 → 血缘检测 → 时间戳解析 → Meta 序列化 → 全文索引 → 重复检测 → TUI 集成 → DAG 可视化 → E2E 测试

---

### 阶段 2：Task 1 — 数据模型扩展（串行）

**Prompt**:
> 先执行 Task 1：扩展数据模型

**探索过程**:
- 在 `ccsm/core/models.py` 新增 `LineageType` 枚举（ORIGINAL / FORK / COMPACT / DUPLICATE）
- 新增 `SessionLineage` dataclass（type、parent_id、fork_point_msg_count、confidence_score）
- `SessionMeta` 新增字段：`title_locked: bool`、`last_message_at: datetime | None`、`last_accessed_at: datetime | None`、`lineage: SessionLineage | None`

**结果**:
- models.py 改动完成，为后续 Task 2-8 提供数据基础
- `LineageType.COMPACT` 对应 compact 后续 session，`confidence_score` 字段用于歧义场景

---

### 阶段 3：Task 2-5 — 4 个 Background Subagent 并行

**Prompt**:
> 现在并行执行 Task 2、3、4、5，用 4 个 background subagent

**探索过程**:
- 派发 4 个并行子任务，无文件冲突（不同文件）：
  - **Task 2** `core/lineage.py`: 血缘信号检测函数（`detect_lineage()`），检测 fork/compact/duplicate 三种类型；信号来源：`parentSessionUuid`、消息数量跳变、路径变更、created_at 重置
  - **Task 3** `core/parser.py`: 新增 `parse_session_timestamps()` 函数，从 JSONL 消息序列提取 `first_message_at` / `last_message_at`，解决 compact 后 `created_at` 失真问题
  - **Task 4** `core/meta.py`: 扩展序列化/反序列化支持新字段；新增 `lock_title(session_id)` 函数，写入 `title_locked=True` 防止 compact 后标题覆盖
  - **Task 5** `core/index.py`: 全文模糊搜索索引，索引 `messages[]` 内容；支持 TF-IDF 评分、中英文分词、结果高亮

**结果**:
- Task 2: 9 个测试（fork/compact/duplicate 各 3 个，加边界条件）
- Task 3: 3 个测试（正常序列、空序列、乱序序列）
- Task 4: 2 个新测试（lock_title 幂等性、序列化往返）
- Task 5: 8 个测试（索引构建、查询排序、高亮、空结果）
- 全部 22 个测试通过

---

### 阶段 4：Task 6 — 重复 Session 检测（串行）

**Prompt**:
> 执行 Task 6：core/discovery.py detect_duplicates()

**探索过程**:
- `detect_duplicates(sessions)` 函数：基于 worktree 路径 + 创建时间窗口（±5 分钟）+ 消息内容哈希三重信号
- 返回 `List[DuplicateGroup]`，每组包含 primary（最近访问）和 duplicates（其余）
- 专门解决痛点 7（多 SSH 产生重复）

**结果**:
- 3 个测试：完全重复、部分重复、无重复
- 全部通过

---

### 阶段 5：Task 7 — TUI Pipeline 集成（串行）

**Prompt**:
> 执行 Task 7：TUI pipeline 集成

**探索过程**:
- `main.py`: 启动时调用 `lineage.detect_all()` 和 `discovery.detect_duplicates()`，结果存入 app state
- `session_list.py`: 新增 `LineageFilter` 控件（全部/仅 Fork/仅 Compact/重复）；按 worktree 分组显示
- `session_card.py`: 在卡片左侧添加血缘徽标（🔀 Fork / 📦 Compact / 🔁 Duplicate）；`title_locked` 时标题前显示 🔒

**结果**:
- TUI 新增血缘过滤维度，解决痛点 1、3、5、7
- 标题锁定徽标解决痛点 2

---

### 阶段 6：Task 8 — DAG 可视化 Widget（串行）

**Prompt**:
> 执行 Task 8：tui/widgets/session_graph.py DAG 可视化

**探索过程**:
- 基于 Textual 的 `Static` widget，渲染 session 血缘树
- 使用 ASCII 树形结构（`├──` / `└──` / `│`）
- 节点显示：session 短 ID + 标题前 20 字 + LineageType 徽标
- 支持键盘导航（↑↓ 选中节点，Enter 跳转到该 session）
- 挂载到右侧面板 `⑤ Session Graph` 新 Tab

**结果**:
- 解决痛点 8（缺少树状图）
- ASCII DAG 在终端环境完全兼容，无依赖

---

### 阶段 7：Task 9 — E2E 测试 + 第一轮代码审查（串行）

**Prompt**:
> 执行 Task 9：E2E 测试，然后用 Claude code-reviewer agent 审查

**探索过程**:
- 跑全量测试：`pytest ccsm/tests/ -v`
- 33 个测试全部通过（0 failed, 0 error）
- 派发 Claude code-reviewer agent 读取全量 diff 进行审查

**Claude code-reviewer 发现（4 High + 5 Medium）**:

| 严重度 | 文件 | 问题 |
|--------|------|------|
| High | `core/lineage.py` | naive datetime 与 aware datetime 混用导致 TypeError |
| High | `core/index.py` | 多线程并发写入索引无锁，race condition |
| High | `core/meta.py` | lock_title() 直接覆盖文件，未处理写入中途崩溃 |
| High | `core/parser.py` | JSONL 解析无容错，单行损坏导致整个 session 失败 |
| Medium | `core/lineage.py` | confidence_score 未做区间校验（可能 >1.0） |
| Medium | `core/index.py` | 索引加载无容错（文件损坏时崩溃） |
| Medium | `tui/widgets/session_graph.py` | 大型 DAG（>500节点）未做性能保护 |
| Medium | `core/discovery.py` | 5 分钟时间窗口写死为魔法数字 |
| Medium | `core/meta.py` | 序列化时未验证 lineage.confidence_score 范围 |

**修复**:
- datetime 统一为 UTC aware
- 索引写入加 threading.Lock
- lock_title 使用原子写入（tempfile → rename）
- JSONL 解析加 try/except 逐行容错
- confidence_score 钳制到 [0.0, 1.0]
- 索引加载加 JSON 解析容错
- DAG widget 超 200 节点时折叠深层节点

**结果**: 全部 High/Medium 修复，33 个测试仍全过

---

### 阶段 8：Codex GPT-5.4 第二轮审查

**Prompt**:
> 再用 Codex GPT-5.4 从 GitHub 读代码做第二轮审查

**探索过程**:
- Codex 从 GitHub `CopeeeTang/ccsm` 读取最新代码
- 独立于第一轮审查，专注于语义正确性和边界条件

**Codex 发现（2 High + 4 Medium）**:

| 严重度 | 文件 | 问题 | 修复 |
|--------|------|------|------|
| High | `core/lineage.py` | `session_id` 在 fork 检测后未同步到 `SessionLineage.parent_id`，导致血缘链断裂 | 修复赋值逻辑 |
| High | `core/lineage.py` | COMPACT 阶段缺失：只检测了触发 compact 的 session，未标记 compact 产生的后继 session | 补全后继标记逻辑 |
| Medium | `core/discovery.py` | `abs()` 计算时间差后，正负重叠导致误判为重复 | 改用 `timedelta.total_seconds()` |
| Medium | `tui/session_list.py` | Filter 切换后未清空 stale session state，旧选中项残留 | 切换时 reset selected |
| Medium | `core/index.py` | 从磁盘加载的 JSON 未做 shape 验证，字段缺失时 KeyError | 加 `.get()` + 默认值 |
| Medium | `core/lineage.py` | `except Exception` 过宽，掩盖了真实错误；改为具体异常类型 | 改为 `ValueError`, `KeyError` |

**结果**: 全部 6 个 finding 修复并推送

---

### 阶段 9：Smoke Test（真实数据验证）

**Prompt**:
> 用真实 worktree 数据跑 Smoke Test

**测试数据**:
- `GUI/panel` worktree: 5 sessions
- `GUI/memory` worktree: 73 sessions

**测试结果**:

| 指标 | GUI/panel | GUI/memory |
|------|-----------|------------|
| Session 加载 | ✅ 5/5 | ✅ 73/73 |
| Compact 检测 | 0 | 9 个 |
| Duplicate 检测 | 0 | 8 组 |
| 搜索 "streaming" | — | 命中 6 条 |
| DAG 渲染 | ✅ | ✅ |
| 崩溃 / 异常 | 无 | 无 |

---

## 三、关键决策

| 决策点 | 选择 | 原因 | 备选方案 |
|--------|------|------|----------|
| 血缘检测信号 | 三重信号（parentSessionUuid + 消息数跳变 + created_at 重置） | 单一信号误判率高，compact 操作同时触发多个信号 | 纯基于 parentSessionUuid（但 Claude Code 并非始终填写） |
| 全文搜索实现 | 内置 TF-IDF，不依赖外部库 | 保持零依赖安装体验 | sqlite FTS5（需 sqlite3 扩展） |
| DAG 渲染 | ASCII 树形（终端原生） | Textual 无内置 Graph widget，ASCII 零依赖 | Rich Tree（无交互） |
| 并行策略 | Task 2-5 并行，其余串行 | Task 2-5 无文件冲突；Task 6+ 依赖 Task 1-5 的接口定义 | 全串行（慢 3-4x） |
| 时间戳策略 | 以 `last_message_at` 替代 `created_at` 作为"真实时间轴" | compact 后 `created_at` 失真，但消息时间戳不受影响 | 完全废弃 `created_at` |

---

## 四、关键数据

```
测试: 33 passed, 0 failed, 0 error
代码: 40 个文件，7505 行
Commit 1 (初始): ed00a0b
Commit 2 (review 修复): 89a5854
仓库: https://github.com/CopeeeTang/ccsm

审查汇总:
  Claude code-reviewer: 4 High + 5 Medium → 全部修复
  Codex GPT-5.4:        2 High + 4 Medium → 全部修复

真实数据验证:
  GUI/panel (5 sessions):  全部加载 ✅
  GUI/memory (73 sessions): 全部加载 ✅
    - compact 检测: 9 个
    - duplicate 检测: 8 组
    - 搜索命中: "streaming" → 6 条
```

---

## 五、新增文件

| 文件 | 说明 |
|------|------|
| `ccsm/core/models.py` | 新增 `LineageType` 枚举、`SessionLineage` dataclass；`SessionMeta` 扩展 4 个字段 |
| `ccsm/core/lineage.py` | 血缘信号检测，支持 FORK / COMPACT / DUPLICATE 三种类型 |
| `ccsm/core/parser.py` | 新增 `parse_session_timestamps()`，从 JSONL 提取真实时间戳 |
| `ccsm/core/meta.py` | 序列化扩展 + `lock_title()` 原子写入 |
| `ccsm/core/index.py` | 全文模糊搜索索引（TF-IDF，含消息内容） |
| `ccsm/core/discovery.py` | `detect_duplicates()` 三重信号重复检测 |
| `ccsm/tui/widgets/session_graph.py` | ASCII DAG 可视化 widget（Textual） |
| `ccsm/tui/main.py` | 启动时调用血缘检测 + 重复检测 |
| `ccsm/tui/session_list.py` | 新增 LineageFilter、worktree 分组 |
| `ccsm/tui/widgets/session_card.py` | 血缘徽标 + 标题锁定图标 |
| `docs/superpowers/plans/2026-04-02-ccsm-resume-painpoints.md` | 9-Task 实施计划原文 |

---

## 六、痛点解决状态

| 痛点 | 状态 | 解决方案 |
|------|------|----------|
| 1. Fork 不可区分 | ✅ | `lineage.py` 检测 + 卡片 🔀 徽标 |
| 2. 标题还原 | ✅ | `meta.lock_title()` + 🔒 徽标 |
| 3. Worktree 列表乱 | ✅ | `session_list.py` worktree 分组 + LineageFilter |
| 4. 搜索无用 | ✅ | `index.py` 全文 TF-IDF 索引消息内容 |
| 5. Compact 幽灵 session | ✅ | COMPACT 类型标记 + 过滤维度 |
| 6. 时间轴变动 | ✅ | `parser.parse_session_timestamps()` 替代 created_at |
| 7. 多 SSH 重复 | ✅ | `discovery.detect_duplicates()` 三重信号 |
| 8. 缺少树状图 | ✅ | `session_graph.py` ASCII DAG widget |

---

## 七、当前状态

- ✅ 9 个 Task 全部完成
- ✅ 33 个测试全过
- ✅ 双重代码审查（Claude + Codex）完成，6 High + 9 Medium 全部修复
- ✅ 真实数据 Smoke Test 通过（GUI/panel + GUI/memory）
- ✅ 两次 commit 推送到 GitHub（ed00a0b + 89a5854）

---

## 八、下一步

- [ ] **性能基准**: 在 500+ session 的 worktree 上测试索引构建时间和搜索延迟
- [ ] **增量索引**: 当前全量重建索引，大型 worktree 启动较慢，考虑增量更新
- [ ] **标题锁定 UI**: TUI 内支持 `L` 键快捷锁定/解锁当前 session 标题（目前需 CLI）
- [ ] **DAG 导出**: 支持将 session 血缘图导出为 Mermaid 或 DOT 格式
- [ ] **compact 后继追踪**: 自动将 compact 后的 session 链接到原始会话，提供"继续阅读"入口
- [ ] **Round 6**: 用户体验打磨 + 发布 v0.5 changelog
