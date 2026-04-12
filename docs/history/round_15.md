# CCSM - Round 15

> 日期: 2026-04-12
> 会话轮数: 约 20 轮
> 主要方向: 审计并完整实施 fluent TUI 优化计划的全部 5 个 Task（单遍解析、增量 Widget 更新、Batch Enrich 定点更新、搜索 Debounce、Detail 渐进加载），Codex 代码审查并修复 2 Critical + 6 Warning

---

## 会话目标

1. 审计 `docs/plans/2026-04-10-fluent-tui-optimization.md` 中 5 个 Task 的实施状态
2. 将所有未实现 / 被回退的 Task 全部落地到 main branch 当前代码
3. 经过 Codex code review 修复质量问题，确保 132 个测试全部通过

---

## 行动路线

### 1. 审计 fluent 优化计划现状

**Prompt**: > 帮我审计一下 fluent TUI 优化计划中哪些 task 已经实现，哪些还没实现

**探索过程**:
- 读取 `docs/plans/2026-04-10-fluent-tui-optimization.md` 了解 5 个 Task 定义
- 用 git log 追溯 Task 1 (单遍解析) 的历史：commit `546cdd4` 曾实现，但被 `ca8a651`（TUI 简化重构）完整回退
- 检查 `ccsm/core/parser.py`：无 `parse_session_full` 函数，确认 Task 1 已丢失
- 检查 `ccsm/tui/widgets/session_list.py`：无 card pool 机制，Task 2 未实现
- 检查 `ccsm/tui/screens/main.py`：无 `_search_timer`，Task 4 未实现
- 检查 `ccsm/tui/widgets/session_detail.py`：无分帧加载，Task 5 未实现

**结果**:
- Task 1 (单遍解析): **已回退**，需重新实现
- Task 2 (增量 Widget 更新): **从未实现**
- Task 3 (Batch Enrich 定点更新): **从未实现**
- Task 4 (搜索 Debounce): **从未实现**
- Task 5 (Detail 渐进式加载): **从未实现**

---

### 2. Task 4 — 搜索 Debounce 150ms

**Prompt**: > 先实现 Task 4，最简单

**探索过程**:
- 在 `MainScreen.__init__` 添加 `_search_timer: Timer | None = None`
- 修改 `on_input_changed` 为 cancel-and-reschedule 模式：
  - 有内容时：cancel 旧 timer，`set_timer(0.15, callback)`
  - 清空时：立即执行（跳过 debounce，响应更快）
- 回调函数调用现有的 `_do_search()`

**结果**: Task 4 完成，150ms debounce 避免每次按键都触发 JSONL 扫描

---

### 3. Task 1 — 单遍解析 parse_session_full

**Prompt**: > 现在实现 Task 1，单遍解析

**探索过程**:
- 分析现有调用链：`_parse_and_display()` 依次调用：
  1. `parse_session_info()` — 读一次 JSONL
  2. `parse_lineage_signals()` — 读第二次 JSONL
  3. `get_last_assistant_messages()` — 读第三次 JSONL（triple-read 反模式）
- 在 `parser.py` 新增 `FullParseResult` dataclass，字段合并三函数全部返回值
- 新增 `parse_session_full(path)` 函数：单次文件读取，一遍扫描填充所有字段
- 更新 `_parse_and_display()` 改用 `parse_session_full()`
- 更新 `parse_cache.py`：新增 `cached_parse_full()` 指向新函数，保持旧函数兼容
- 实测性能（1.5MB JSONL）：新 API **60ms** vs 旧 triple-read **114ms**，加速 **1.9x**

**结果**: Task 1 完成，单遍解析落地，缓存层同步更新

---

### 4. Task 2 — 增量 Widget 更新（Card Pool）

**Prompt**: > 实现 Task 2，增量 widget 更新

**探索过程**:
- `SessionCard` 改造：
  - 为每个子 widget 添加 CSS ID（格式 `[:16]` 防碰撞）
  - 提取 `_render_title_markup()` / `_render_time_label()` / `_render_intent_markup()` 共享渲染方法
  - 新增 `update_data(new_session)` 方法：直接修改子 widget 内容，无需 unmount/remount
- `SessionListPanel` 改造：
  - 添加 `_card_pool: dict[str, SessionCard]` 和 `_visible_ids: list[str]`
  - 新增 `_incremental_update(sessions)` 方法：判断是否为搜索子集，复用已有 card 对象
  - `load_sessions` 智能分支：
    - `force_rebuild=True` → `_rebuild()`（全量）
    - filter 切换 → `_rebuild()`（需要重排序）
    - 搜索子集 → `_incremental_update()`（增量复用）
  - `_rebuild_list` 中复用 pool 中已有 card（同 session_id 直接 update_data，否则新建）

**结果**: Task 2 完成，搜索时避免整个列表 unmount/remount，减少渲染开销

---

### 5. Task 3 — Batch Enrich 定点更新

**Prompt**: > 实现 Task 3，batch enrich 后只更新变化的 card

**探索过程**:
- 问题：原 `_batch_enrich_sessions()` 每次 AI 标题生成后调用 `_update_session_list()` 全量 rebuild
- 新增 `_update_single_card(session_id, new_title)` 方法：
  - O(1) 从 `_card_pool` 查找对应 card
  - 调用 `card.update_data()` 仅刷新该 card
- `_batch_enrich_sessions` 改为：AI title 返回后 `call_from_thread(_update_single_card, sid, title)`
- 全量 rebuild 仅在初次加载时触发一次

**结果**: Task 3 完成，每个 AI 标题生成后 O(1) 定点更新，不再触发全量 rebuild

---

### 6. Task 5 — Detail 渐进式加载

**Prompt**: > 最后实现 Task 5，detail 渐进式加载

**探索过程**:
- 问题：`_rebuild()` 同步加载所有内容（digest + milestones + lineage + last exchange），阻塞 TUI
- 拆分为两帧：
  - **Frame 1**（立即）：挂载 digest 摘要区 + milestones 里程碑（核心恢复信息）
  - **Frame 2**（50ms 后）：挂载 collapsed sections（lineage、背景任务、last exchange）
- `_mount_deferred_sections(session_id)` 检查 `self._current_session_id == session_id`，防止快速切换时 stale mount
- 50ms 延迟让 Frame 1 先完成渲染，用户感知首屏更快

**结果**: Task 5 完成，Detail 首屏加载时间缩短，快速切换 session 无残影

---

### 7. Codex 代码审查 + 修复

**Prompt**: > 用 codex rescue 对所有改动做代码审查

**探索过程**:
- 运行 Codex read-only review，发现 **2 Critical + 6 Warning**：
  - **C1**: `_incremental_update` 内 150 行死代码路径（subset detection 逻辑从未触达）→ 重写接入搜索快速路径
  - **C2**: `main.py` 导入 `lineage.py` 私有常量（`_COMPACT_PREFIXES` 等）→ 在 `lineage.py` 重命名为公共 API：`COMPACT_SUMMARY_PREFIXES`、`BRANCH_SUFFIX_RE`
  - **W1**: `main.py` 中 3 个未使用的导入（清理）
  - **W2**: `SessionCard.update_data` 中 `except Exception: pass` → 改为 `logger.debug(...)` 保留可调试性
  - **W3**: card CSS ID 用 `session_id[:12]` → 改为 `[:16]` 降低碰撞概率
  - **W4**: `_full_rebuild` 和 `_rebuild` 存在重复代码 → 合并为单一实现路径
- 逐一修复所有问题，重新运行测试

**结果**:
- 所有修复完成
- `python3 -m pytest tests/ -v` 输出：**132 passed, 0 failed, 0 errors**
- 零回归

---

## 关键决策

| 决策点 | 选择 | 原因 | 备选方案 |
|--------|------|------|----------|
| 单遍解析 API 形式 | 新增 `parse_session_full()` + `FullParseResult` dataclass | 不破坏旧 API，渐进迁移 | 直接修改旧函数签名（破坏性） |
| Card pool 复用策略 | `dict[session_id, SessionCard]` | O(1) 查找，生命周期与 list 绑定 | 每次重建新 card（开销大） |
| Batch enrich 更新粒度 | `_update_single_card` O(1) 定点 | 避免全量 rebuild 打断用户交互 | 事件驱动全量刷新 |
| Detail 分帧延迟时长 | 50ms | 足够 Frame 1 完成首次渲染 | 100ms（用户感知延迟明显）|
| 公共常量命名 | `COMPACT_SUMMARY_PREFIXES`、`BRANCH_SUFFIX_RE` | Codex 审查指出私有常量不应跨模块导入 | 保留私有命名（不规范）|
| 搜索 debounce 时长 | 150ms | 平衡响应速度与减少无效查询 | 200ms（略慢）/ 100ms（减少效果有限）|

---

## 关键数据 / 指标

| 指标 | 优化前 | 优化后 | 提升 |
|------|--------|--------|------|
| JSONL 解析（1.5MB） | 114ms（triple-read） | 60ms（single-pass） | **1.9x 加速** |
| 搜索触发延迟 | 每次按键立即触发 | 150ms debounce | 减少无效解析 |
| Batch enrich 更新成本 | O(N) 全量 rebuild | O(1) 定点更新 | 线性 → 常数 |
| Detail 首屏内容到达 | 全部内容同步加载 | Frame 1 立即 + Frame 2 延迟 50ms | 首屏感知更快 |
| 测试通过数 | 132 | 132 | **零回归** |
| Codex 发现 Critical | — | 2 → 0（全部修复） | — |
| Codex 发现 Warning | — | 6 → 0（全部修复） | — |

---

## 文件变更摘要

- `/home/v-tangxin/GUI/projects/ccsm/ccsm/core/parser.py` — 新增 `FullParseResult` dataclass + `parse_session_full()` 单遍解析函数
- `/home/v-tangxin/GUI/projects/ccsm/ccsm/core/parse_cache.py` — 新增 `cached_parse_full()`，旧函数保持兼容
- `/home/v-tangxin/GUI/projects/ccsm/ccsm/core/lineage.py` — 私有常量重命名为公共 API：`COMPACT_SUMMARY_PREFIXES`、`BRANCH_SUFFIX_RE`
- `/home/v-tangxin/GUI/projects/ccsm/ccsm/tui/screens/main.py` — 搜索 debounce（`_search_timer`）+ 使用单遍解析 + `_update_single_card()` + 清理 3 个未使用导入
- `/home/v-tangxin/GUI/projects/ccsm/ccsm/tui/widgets/session_card.py` — `update_data()` 方法 + 渲染方法提取 + `except Exception` 改为 `logger.debug`
- `/home/v-tangxin/GUI/projects/ccsm/ccsm/tui/widgets/session_list.py` — `_card_pool` + `_visible_ids` + `_incremental_update()` + 智能 `load_sessions` 分支 + `_full_rebuild`/`_rebuild` 合并
- `/home/v-tangxin/GUI/projects/ccsm/ccsm/tui/widgets/session_detail.py` — 渐进式加载：Frame 1（立即）+ Frame 2（50ms 延迟）+ stale mount 防护

---

## 问题与发现

- **triple-read 反模式**: 原代码对同一 JSONL 文件进行 3 次独立读取，导致 1.5MB 文件解析耗时 114ms → 单遍解析后降至 60ms
- **私有常量跨模块导入**: `main.py` 引用 `lineage._COMPACT_PREFIXES` 等私有符号，Codex 审查发现后重命名为公共 API → 已修复
- **死代码路径**: `_incremental_update` 内的 subset detection 逻辑因条件判断顺序错误永远无法触达 → Codex C1 发现，重写后接入正确快速路径
- **stale mount 问题**: Detail 分帧加载时若用户快速切换 session，Frame 2 可能在新 session 上挂载旧 section → 添加 `session_id` 比对防护 → 已修复
- **fluent worktree 优化被回退**: Task 1 曾在 `546cdd4` 实现，但 `ca8a651`（TUI 简化重构）将其完整回退，本轮重新实现并适配新架构

---

## 当前状态

`docs/plans/2026-04-10-fluent-tui-optimization.md` 中全部 5 个 Task 均已完成落地：

- [x] Task 1: 单遍解析 `parse_session_full()` — 1.9x 加速
- [x] Task 2: 增量 Widget 更新（Card Pool）— 减少列表渲染开销
- [x] Task 3: Batch Enrich O(1) 定点更新 — 不再触发全量 rebuild
- [x] Task 4: 搜索 Debounce 150ms — 减少无效 JSONL 扫描
- [x] Task 5: Detail 渐进式加载（Frame 1 + Frame 2）— 首屏更快

132 个测试全部通过，Codex 审查无遗留 Critical/Warning。

---

## 下一步

- [ ] 实测多个真实大 session（>3MB JSONL）下的加速效果，收集更多性能数据
- [ ] 考虑 Card Pool 的最大容量限制（防止内存无限增长）
- [ ] `parse_session_full()` 稳定后，逐步废弃旧的 triple-call 路径
- [ ] 评估 Detail Frame 2 的 50ms 延迟在低性能机器上是否需要动态调整
- [ ] 关注 `_incremental_update` 的 subset detection 在实际搜索场景中的命中率
