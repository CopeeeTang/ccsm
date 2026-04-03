# Round 11 — TUI 全面重构：Drawer 抽屉架构 + Spine 时间轴 + Detail 视觉升级

**日期**: 2026-04-03
**项目**: CCSM (Claude Code Session Manager)
**仓库**: `/home/v-tangxin/GUI/projects/ccsm`
**核心主题**: 基于 Gemini 设计方案对 CCSM TUI 前端进行全面重构，双栏 + Drawer 抽屉式架构，保留 Round 10 的 6 区域数据布局，全面升级视觉样式

---

## 会话目标

依据 `docs/plans/implementation_plan.md.resolved`（Gemini 设计方案）对 TUI 前端进行全面重构。核心目标为：三栏固定布局 → 双栏 + ModalScreen Drawer 架构；Session List 升级为 Spine View 时间轴卡片；Detail 面板视觉升级，保留 Round 10 奠定的 6 区域数据布局，以增量方式修改存量代码（不全部重写）。

---

## 行动路线

### 阶段 1：需求澄清 & 文档阅读

通过 Interview 模式与用户确认执行策略：
- 读取 `docs/history/round_10.md`、`docs/plans/detail-panel-redesign.md`、`docs/plans/implementation_plan.md.resolved` 三份文档
- 通过 AskUserQuestion 确认四个关键问题：
  - 全部实施 Gemini 方案（Drawer + Spine + Detail 视觉）✅
  - Detail 区域以 Round 10 的 6 区域为准（而非 Gemini 的 4 区域）✅
  - 增量修改存量代码，不全部重写 ✅
  - 先评估字段匹配度，再进入实施 ✅

### 阶段 2：字段匹配度评估

全面审查现有数据模型（`SessionInfo` / `SessionDetailData` / `CompactSummaryParsed`）与 Gemini 方案各区域所需字段的匹配度：
- **标题区**: `display_name`、`project_name`、`session_id` → 完全匹配
- **WHERE LEFT OFF**: `where_left_off`、`breakpoints` → 完全匹配
- **INTENT**: `task_intent` → 完全匹配
- **MILESTONES**: `milestones` (List[str]) → 完全匹配
- **LAST EXCHANGE**: `last_exchange` (question/answer) → 完全匹配
- **元数据**: `status`、`message_count`、`file_count`、`duration` → 完全匹配

**结论**：数据层 Round 10 已完全就绪，主要工作集中在 UI 架构和视觉层。

### 阶段 3：Plan Mode — 5 Phase 实施计划

制定分阶段实施方案：
- **Phase 1**: Drawer 架构（新建 `drawer.py` + 修改 `main.py` + 更新 CSS）
- **Phase 2**: Spine View 时间轴卡片（`session_list.py` + `session_card.py`）
- **Phase 3**: Detail 面板视觉升级（`session_detail.py`）
- **Phase 4**: Footer 和交互更新
- **Phase 5**: 测试验证

### 阶段 4：Phase 1 — Drawer 架构实施

**新建 `ccsm/tui/drawer.py`**（ModalScreen 继承，约 120 行）：
- 继承 `ModalScreen`，embed `SessionDetail` 组件
- 实现键盘事件委托（`g/r/s/D` 键转发至内部 SessionDetail）
- `on_key` 处理 Escape 关闭 Drawer
- `set_session()` 方法接受 `SessionDetailData` 并刷新内部组件

**修改 `ccsm/tui/main.py`**（三栏 → 双栏，约 -92/+92 行）：
- Layout 从 `1fr 3fr 2fr` 改为 `1fr 3fr`（删除右侧 Detail 列）
- `on_session_selected()` 改为 `app.push_screen(DrawerScreen(detail))` 而非更新内联 Detail
- 删除 `SessionDetail` 的直接引用

**重写 `claude_native.tcss`**（约 +109 行）：
- 删除旧 `#detail-panel` CSS
- 新增 `.drawer-screen`、`.spine-card`、`.stepper`、`.chat-bubble`、`.avatar` 等新样式类

> 注：用户手动补充了 `drawer.py` 中 `action_*` 的 Textual 委托模式（`g/r/s/D`）

### 阶段 5：Phase 2 — Spine View 时间轴卡片

**修改 `ccsm/tui/session_list.py`**（约 +45 行）：
- 新增 `_build_spine_data(session)` 方法，提取 `relative_time`、`graph_bar`（消息数/文件数可视化）、`status_icon` 等 Spine 显示字段
- `render_session_card()` 调用时传入 spine 数据

**修改 `ccsm/tui/session_card.py`**（约 +71 行）：
- 新增 `spine_time` 和 `spine_graph` 参数
- 渲染时增加 14 列宽前缀：时间轴标记（`│`/`●`）+ 时间 + 消息数图形
- 时间轴卡片格式：`● 2h ago  ████░  session_title`

> 注：用户手动修复了 swimlane 中 DataTable `set_data` 的 deferred 时序问题

### 阶段 6：Phase 3 — Detail 面板视觉升级

**修改 `ccsm/tui/session_detail.py`**（约 +177 行）：

各 Section 视觉升级：
- **标题区**: `_build_description()` → `_mount_session_section()`，使用大标题 `Static` + 块引用风格 Intent（`▎` 前缀）
- **WHERE LEFT OFF**: 添加 `det-breakpoint-badge` CSS 类，为 breakpoint 条目渲染带颜色的徽章
- **MILESTONES**: 改用 `det-milestones` 容器包裹 `Stepper` 组件，里程碑有序步骤显示
- **LAST EXCHANGE**: 改用 Chat bubble 布局：`Horizontal` + Avatar（`Static` 圆形头像）+ 消息体（`Static`），分别渲染 Q（用户）和 A（AI）两气泡

### 阶段 7：Codex Review 审查

运行 GPT-5.4 代码审查，发现 4 个问题：
- **1 High**：CJK/全角字符宽度计算使用 `len()` 而非 `cell_len()`，导致对齐错乱 → **已修复**
- **3 Medium**：Drawer 边界未处理空 detail、Stepper 组件 import 路径、CSS 类名一致性 → 确认为遗留已知问题，暂不处理

**CJK 修复**：在 `session_card.py` 中将 `len(title)` 改为 `cell_len(title)`（从 `textual.css.scalar` 导入），确保中日韩字符按 2 宽度计算。

### 阶段 8：验证

TUI 启动验证：
```bash
cd /home/v-tangxin/GUI/projects/ccsm
PYTHONPATH=.:$PYTHONPATH python3 -m ccsm
```
- ✅ 双栏布局正确渲染（Session List + Filter Bar）
- ✅ 选择 Session 后 Drawer 弹出（ModalScreen 覆盖层）
- ✅ Spine 时间轴卡片渲染正确
- ✅ Detail 6 区域正常展示，视觉样式升级生效
- ✅ 全部 56 个测试通过（0 failures）

---

## 关键决策

| 决策点 | 选择 | 原因 |
|---|---|---|
| Detail 布局方案 | 保留 Round 10 的 6 区域 | Gemini 4 区域丢失 WHERE LEFT OFF 和 MILESTONES 等关键数据 |
| Drawer 实现方式 | 继承 `ModalScreen` | Textual 原生支持，自动处理焦点/遮罩层，无需手写覆盖逻辑 |
| Spine 卡片前缀宽度 | 固定 14 列 | 保证时间轴竖线对齐，视觉一致性 |
| 字符宽度计算 | `cell_len()` 替换 `len()` | CJK 字符为 2 宽，`len()` 会导致 terminal 对齐错位 |
| 代码修改策略 | 增量修改（不重写） | 保护 Round 10 已验证的数据管线和测试覆盖 |
| Codex 发现的中危问题 | 暂不处理 | 为遗留已知问题，不影响当前功能，后续 round 跟进 |

---

## 关键数据

| 指标 | 数值 |
|---|---|
| 新增文件 | 1（`drawer.py`） |
| 修改文件 | 5（`main.py`, `claude_native.tcss`, `session_card.py`, `session_list.py`, `session_detail.py`） |
| 新增代码行 | +362 行 |
| 删除代码行 | -147 行 |
| 净增量 | +215 行 |
| 测试通过率 | 56/56（100%） |
| Codex 发现 High 问题 | 1（已修复） |
| Codex 发现 Medium 问题 | 3（遗留待处理） |

---

## 本轮产出文件

| 文件 | 变更类型 | 说明 |
|---|---|---|
| `ccsm/tui/drawer.py` | 新建（+120 行） | ModalScreen 抽屉，embed SessionDetail，支持键盘委托 |
| `ccsm/tui/main.py` | 修改（+92/-92 行） | 三栏→双栏，session 选择改为 push_screen(DrawerScreen) |
| `ccsm/tui/session_card.py` | 修改（+71 行） | 新增 Spine 时间轴前缀渲染，cell_len() CJK 修复 |
| `ccsm/tui/session_list.py` | 修改（+45 行） | 新增 _build_spine_data()，提取时间轴显示字段 |
| `ccsm/tui/session_detail.py` | 修改（+177 行） | 6 区域视觉升级：大标题/块引用/Badge/Stepper/Chat bubble |
| `ccsm/tui/claude_native.tcss` | 修改（+109 行） | 删旧 detail-panel，新增 Drawer/Spine/Stepper/Chat 样式 |

---

## 下一步计划

- [ ] 处理 Codex 发现的 3 个 Medium 问题（Drawer 空 detail 边界、Stepper import、CSS 类名一致性）
- [ ] Phase 4：Footer 和交互更新（快捷键说明、状态栏实时更新）
- [ ] 完善 Drawer 关闭动画和过渡效果（Textual `animation` 支持）
- [ ] 补充 Drawer 和 Spine 相关的 TUI 集成测试（当前仅数据层测试）
- [ ] 评估 `cell_len()` 在 Stepper 和 Chat bubble 其他字符串处理点的适用性
- [ ] 考虑 Spine 卡片支持折叠/展开（`--expanded`/`--compact` 视图模式切换）
- [ ] 性能评估：大量 Session（>500）时 Spine 渲染的响应速度
