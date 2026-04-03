# CCSM 产品体验指南

> **给**: 产品经理 / 设计师
> **来自**: tangxin
> **日期**: 2026-04-02
> **版本**: v0.4 (Lineage & Search)

---

## 一、这是什么？

**CCSM** (Claude Code Session Manager) 是一个终端里的**会话管理器**——帮助 Claude Code 的重度用户管理几千个历史对话。

### 要解决的核心痛点

| 痛点 | 现状 | CCSM 的方案 |
|------|------|------------|
| 🔍 **找不到历史会话** | Claude Code 只提供一个扁平的 `/resume` 列表，几百个会话混在一起 | 按 **项目 → 分支 → 状态** 三级导航 |
| 🏷️ **标题丢失** | 用户 rename 的标题会被后续操作覆盖 | 独立的 sidecar 元数据，永不丢失 |
| 🗑️ **噪音太多** | 插件自动创建的观察者会话、测试用的"hi"占 95% | 自动识别并隐藏 NOISE 会话 |
| 🧠 **无法快速回忆** | 进入会话前不知道"上次做到哪了" | **里程碑时间线** + 断点定位 + Claude 最后回复 |
| 📊 **没有分类** | 所有会话一视同仁 | 自动分为 ACTIVE / BACKGROUND / IDEA / DONE / NOISE |
| 🔗 **Session 关系不可见** | fork/compact/重复 session 之间没有任何关联显示 | **血缘检测** + DAG 可视化 + lineage badge |
| 🔍 **搜索无用** | Claude Code 的 resume 搜索只扫描 64KB 窗口，数量上限不稳定 | **全文模糊搜索**，无数量限制 |
| ⏱️ **时间轴混乱** | 看一眼就改变排序（按文件 mtime 而非消息时间） | 按 `last_message_at` 排序 |

### 目标用户画像

- 每天使用 Claude Code 4-8 小时
- 同时管理 3-5 个项目 / 分支
- 累积了 500-3000+ 个历史会话
- 经常需要"回到之前那个会话继续做"

---

## 二、如何体验

### 启动方式

在服务器终端中执行：

```bash
cd /home/v-tangxin/GUI
source ml_env/bin/activate
PYTHONPATH=projects/ccsm:$PYTHONPATH python3 -m ccsm
```

### 界面概览

```
┌──────────────────────────────────────────────────────────────────────────┐
│ ⬡ CCSM — Claude Code Session Manager                                   │
├──────────┬────────────────────────┬──────────────────────────────────────┤
│          │                        │                                      │
│ WORKTREES│      SESSIONS          │          DETAIL                      │
│          │                        │                                      │
│ ▸ GUI    │  🟢 ACTIVE (3)         │  ─── 📋 SESSION ───                 │
│   main   │  ● ▲ CCSM设计与实现 2h│  Title   CCSM架构设计与全栈实现      │
│   panel ●│    📝"我想做一个管理器" │  Status  🟢 ACTIVE                  │
│   streamI│    💭 P0已修复…  42msg  │  Messages 42 total, 18 user         │
│   benchma│                        │                                      │
│   ...    │  🔵 BACKGROUND (2)     │  ─── 🧭 MILESTONES ───              │
│          │  ◐ △ eval-pipeline 5d  │  ✓ 需求讨论    确认三面板方案        │
│ ▸ VLM-Rou│    📝"跑一下full eval" │  ✓ Plan 产出   3批次实施计划        │
│          │    💭 正在处理第3批… 89m│  ✓ Batch 1     parser+discovery     │
│          │                        │  ✓ Codex Review  P0/P1全修复        │
│          │  🟣 IDEA (5)           │  ▶ Round 2 优化  卡片+渲染           │
│          │  ◇ ▽ brainstorm 3d ago │    ✓ 状态推断扩展                    │
│          │    📝"试一下proactive"  │    ✓ 卡片4行Rich布局                │
│          │                        │    ▶ PM反馈处理  ← HERE             │
│          │  ⚪ DONE (12)          │    ○ Summarizer模块                  │
│          │  ○ ▽ fix-bug    5d ago │  ○ 端到端测试                        │
│          │    📝"修复import错误"  │                                      │
│          │                        │  ─── 📍 BREAKPOINT ───              │
│          │                        │  ┌─────────────────────────────┐     │
│          │                        │  │ Round 2 优化                 │    │
│          │                        │  │ PM反馈 — Timeline里程碑设计  │    │
│          │                        │  │ Topic: 选定方案B紧凑里程碑   │    │
│          │                        │  └─────────────────────────────┘     │
│          │                        │                                      │
│          │                        │  ─── 💬 LAST REPLY ───              │
│          │                        │  全部修复验证通过。新的Detail…       │
│          │                        │                                      │
├──────────┴────────────────────────┴──────────────────────────────────────┤
│ ↑↓ Navigate  Tab Panel  Enter Select  s AI Summary  r Resume  q Quit   │
└──────────────────────────────────────────────────────────────────────────┘
```

### 操作说明

| 快捷键 | 功能 |
|--------|------|
| `↑` `↓` | 在当前面板中上下导航 |
| `Tab` | 在三个面板之间切换焦点 |
| `Enter` | 选中 worktree → 加载会话列表；选中会话 → 显示详情 |
| `r` | 恢复选中的会话（退出 TUI 后自动启动 `claude --resume`） |
| `s` | **AI 摘要**：调用 Claude API 为选中的会话生成高质量里程碑（需本地 proxy） |
| `h` | 显示/隐藏被过滤的 NOISE 会话 |
| `g` | 切换 **Session DAG 图** — 显示 fork/compact/duplicate 关系的树状图 |
| `/` | **全文搜索** — 跨 title、intent、内容、分支、标签的模糊搜索 |
| `q` | 退出 |

---

## 三、体验路径（建议按顺序）

### 路径 1: 浏览与导航（2 分钟）

1. 启动后自动选中一个 worktree → 观察中间面板的会话列表
2. 注意会话按 **ACTIVE → BACKGROUND → IDEA → DONE** 分组显示
3. 用 `↑↓` 浏览不同会话卡片，观察每张卡片的 4 行信息
4. 按 `h` 打开 NOISE → 观察被过滤掉的噪音有多少

### 路径 2: 切换项目/分支（1 分钟）

1. 按 `Tab` 切到左侧 Worktree 面板
2. 用 `↑↓` 选择不同的 worktree（如 `panel`、`streamIT`）
3. 观察中间面板如何动态加载该分支下的会话

### 路径 3: 查看里程碑时间线（3 分钟）★ 核心体验

1. 在中间面板选中一个 ACTIVE 或 DONE 状态的会话
2. 观察右侧 Detail 面板的 **🧭 MILESTONES** 区块
3. 注意每个里程碑的 ✓/▶/○ 状态——一眼看出做了什么、卡在哪
4. 关注 **📍 BREAKPOINT** 区块——橙色高亮框直接告诉你"上次停在哪"
5. 向下滚动看 **💬 LAST REPLY** 区块——Claude 最后回复的 Markdown 渲染

### 路径 4: 触发 AI 摘要（可选，需本地 proxy）

1. 选中一个消息数较多（>10msg）的会话
2. 按 `s` 触发 AI 摘要——底部会提示"Generating AI summary…"
3. 几秒后 MILESTONES 区块会更新为 LLM 生成的更高质量版本
4. 对比 AI 生成和规则提取的里程碑质量差异

### 路径 5: 恢复会话（可选）

1. 选中一个你感兴趣的会话
2. 按 `r` 会退出 TUI 并自动执行 `claude --resume {session_id}`

### 路径 6: 查看 Session 血缘关系（2 分钟）★ v0.4 新增

1. 在中间面板注意 session 卡片上的 **lineage badge**：
   - `fork` (蓝色) — 从另一个 session fork 出来的分支
   - `compact` (紫色) — compact 后的延续
   - `dup` (红色) — 多 SSH 终端产生的重复
2. 按 `g` 切换到 **Session Graph** 视图
3. 观察 DAG 树状图：
   - `●` ROOT — 独立 session
   - `◆` FORK — 分支节点
   - `◇` COMPACT — 压缩延续
   - `◉` DUPLICATE — 重复 session
4. 当前选中的 session 以 **橙色高亮** 显示

### 路径 7: 全文搜索（1 分钟）★ v0.4 新增

1. 按 `/` 打开搜索框
2. 输入关键词（支持中英文），观察列表实时过滤
3. 搜索范围覆盖：标题、AI 意图摘要、首条用户消息、分支名、标签
4. 注意搜索**没有数量上限**——所有匹配的 session 都会显示

---

## 四、设计语言

### 配色方案: Claude Native

| 用途 | 颜色 | 色值 |
|------|------|------|
| 主色（交互、焦点） | 🟠 Orange-400 | `#fb923c` |
| 背景 | ⬛ Stone-900 | `#1c1917` |
| 面板 | ⬛ Stone-800 | `#292524` |
| 正文 | 🔲 Stone-200 | `#e7e5e4` |
| 辅助文字 | 🔲 Stone-400 | `#a8a29e` |
| 弱化/muted | 🔲 Stone-500 | `#78716c` |
| 边框 | 🔲 Stone-700 | `#44403c` |

### 状态系统（自动推断）

| 状态 | 图标 | 颜色 | 含义 |
|------|------|------|------|
| ACTIVE | ● | 🟢 `#22c55e` | 24h 内有活动，主要工作会话 |
| BACKGROUND | ◐ | 🔵 `#3b82f6` | 长时间运行的自动任务 |
| IDEA | ◇ | 🟣 `#a855f7` | 短探索/头脑风暴 |
| DONE | ○ | ⚪ `#78716c` | 48h+ 无活动 |
| NOISE | · | ⬛ `#44403c` | 插件噪音/测试 (默认隐藏) |

### 优先级标记（注意力管理）

| 优先级 | 图标 | 含义 |
|--------|------|------|
| FOCUS | ▲ | 需要立即关注 |
| WATCH | △ | 关注但不紧急 |
| PARK | ▽ | 暂时搁置 |
| HIDE | ▿ | 低价值/隐藏 |

---

## 五、会话卡片信息架构

每张卡片 4 行，信息密度递减：

```
行1: ● ▲ CCSM 架构设计与全栈实现        ⚡ 2h ago    ← 是什么？何时？
行2:   📝 "我想做一个会话管理工具"                     ← 当时想做什么？
行3:   💭 所有P0已修复，TUI可运行…        42msg        ← 做到哪了？规模？
行4:   #session-manager  #tui                          ← 分类标签
```

| 层级 | 回答的问题 | 数据来源 |
|------|-----------|---------|
| 标题行 | "这是哪个会话？还活跃吗？" | display_name / slug / 状态 + 时间 |
| 意图行 | "我当时想做什么？" | 首条用户消息 (自动提取) |
| 进展行 | "做到哪了？花了多少工夫？" | Claude 最后回复摘要 + 消息计数 |
| 标签行 | "属于哪个分类？" | 用户自定义 tags (可选) |

---

## 六、Detail 面板信息架构 ★ v0.3 重新设计

> **按 `g` 切换图模式**：Detail 面板可以切换为 Session DAG 图，展示当前 worktree 下所有 session 的 fork/compact/duplicate 关系。

### 设计理念：里程碑时间线 + 断点定位

> **核心洞察**（来自 PM 反馈）：用户回来时不需要"每个字段分别看"，而是需要一个**时间线故事**——"我在做什么 → 做到哪了 → 停在哪了"。
>
> 原来的 5 个碎片化区块（SESSION / LAST REPLY / DECISION TRAIL / INSIGHTS / RETROSPECTIVE）被重组为 **4 个叙事性区块**：

### 新的 4 区块结构

| 区块 | 内容 | 目的 |
|------|------|------|
| 📋 SESSION | Title / Status / Priority / Duration / Branch / Intent | 压缩版元数据（去掉了次要字段） |
| 🧭 **MILESTONES** | 里程碑时间线，每个节点一行，✓/▶/○ 三态 | **核心**: "做了什么阶段？每个阶段什么状态？" |
| 📍 **BREAKPOINT** | 橙色高亮框，标注中断位置和最后讨论话题 | **最有价值**: 直接回答"我停在哪了" |
| 💬 LAST REPLY | Claude 最后一条回复 (Markdown 渲染) | 帮助回忆最后的讨论细节 |

### 里程碑设计方法论

**什么样的节点会被提取为里程碑？**

不是每句对话都是里程碑——只有**阶段转换点**（phase transition）才值得记录：

| 信号类型 | 触发条件 | 示例 |
|---------|---------|------|
| 话题转移 | 用户说"接下来讨论…" / "换一个方面" | "接下来讨论数据迁移方案" |
| 确认转折 | 用户简短确认后方向改变 | "OK" → 进入新话题 |
| 执行指令 | 用户要求执行具体操作 | "开始实施" / "帮我改一下" / `/spawn` |
| 评审进入 | 用户进入验证/review 模式 | "我看一下效果" / "demo" / "codex review" |
| 总结回顾 | 用户要求总结或记录状态 | "总结一下" / "目前进度" / `/save-session` |

**关键设计原则**：
- 只有**用户消息**触发里程碑（Claude 的回复填充内容，但不决定结构）
- 每个里程碑是**索引而非全文**——只说"讨论了什么"，不说"具体讨论了什么内容"
- 完成的阶段折叠成一行，**只有进行中的阶段展开子项**
- 底部的 📍 BREAKPOINT 是**最有价值的信息**——它直接回答"我停在哪了"

### 双模式生成

| 模式 | 触发方式 | 成本 | 质量 |
|------|---------|------|------|
| **Extract**（规则提取） | 选中 session 时自动触发 | 零成本，即时 | 基于模式匹配，覆盖常见场景 |
| **LLM**（AI 生成） | 按 `s` 手动触发 | 调用 claude-haiku-4.5 API | 语义理解，更准确的阶段划分 |

**LLM 模式工作原理**：
1. 将对话消息压缩为 ~12000 字符（用户消息保留 200 字符，assistant 只保留首行）
2. 发送给 Claude API，要求输出严格 JSON（milestones 数组 + breakpoint 对象）
3. 解析 JSON 为数据模型，缓存到 `~/.ccsm/summaries/`
4. 下次打开同一 session 直接读缓存，不再调用 API

### 里程碑视觉示例

```
─── 🧭 MILESTONES ───

  ✓ 需求讨论    确认三面板TUI + 自动分类方案
  ✓ Plan 产出   12节设计文档, 3批次实施
  ✓ Batch 1     parser + discovery + meta + status
  ✓ Batch 2     TUI三面板 + CSS主题
  ✓ Batch 3     MCP Server + CLI
  ✓ Codex Review  2 P0 + 4 P1 全部修复
  ▶ Round 2 优化  卡片+状态+渲染 三方向        ← 进行中的展开子项
    ✓ 状态推断扩展 (NOISE规则)
    ✓ 卡片4行Rich布局
    ✓ Detail渲染优化
    ▶ PM反馈 — Timeline设计  ← HERE            ← 当前位置标记
    ○ Summarizer模块
  ○ TUI端到端测试
  ○ 发布

─── 📍 BREAKPOINT ───
  ┌──────────────────────────────────────┐
  │ Round 2 优化                          │      ← 橙色高亮边框
  │ PM反馈处理 — Timeline里程碑设计        │
  │ Topic: 选定方案B紧凑里程碑            │
  └──────────────────────────────────────┘
```

### 与原设计的对比

| 维度 | v0.2（5区块碎片化） | v0.3（里程碑时间线） |
|------|-------------------|---------------------|
| 信息组织 | 按数据类型分区（元数据/回复/决策/洞察/回顾） | 按时间线叙事（做了什么→做到哪→停在哪） |
| 核心回答 | 多个区块各自回答不同问题 | 一个 MILESTONES 区块讲完整个故事 |
| 断点信息 | 混在 RETROSPECTIVE 的 last_context 里 | 独立的 📍 BREAKPOINT 高亮框 |
| 生成方式 | 全部依赖缓存/placeholder | Extract（自动）+ LLM（按需） |
| 可扩展性 | 每加一种信息就要新增区块 | 里程碑节点天然可扩展 |

---

## 七、希望你关注的问题

### 里程碑设计（★ 新增）

- [ ] 里程碑的粒度是否合适？太粗（3个节点）还是太细（15个节点）？
- [ ] ✓/▶/○ 三态标记是否直觉清晰？
- [ ] 只展开"进行中"阶段的子项——还是所有阶段都应该可展开？
- [ ] 📍 BREAKPOINT 的橙色高亮框是否足够醒目？
- [ ] 里程碑的"索引而非全文"策略——你是否会想点击展开看详细内容？
- [ ] 断点提醒的措辞风格——应该更像"系统通知"还是更像"同事提醒"？

### 信息架构

- [ ] 三面板的宽度比例（18% / 38% / 44%）是否合理？
- [ ] 卡片的 4 行信息是否足够帮你区分不同会话？还需要什么信息？
- [ ] Detail 面板从 5 区块精简为 4 区块——是否丢失了重要信息？
- [ ] 状态分组（ACTIVE/BACKGROUND/IDEA/DONE）是否直觉清晰？

### 交互体验

- [ ] 左侧 Worktree 树的层级（Project → Worktree）是否好理解？
- [ ] 会话按状态分组 vs 按时间排序，你更倾向哪种？
- [ ] "按 `r` 恢复会话"的交互是否自然？是否需要确认弹窗？
- [ ] "按 `s` 生成 AI 摘要"——这个交互是否直觉？应该自动还是手动？
- [ ] NOISE 默认隐藏、按 `h` 显示——这个策略是否合适？

### 视觉设计

- [ ] 深色主题 + 橙色主色的整体感受？
- [ ] 状态图标（●/◐/◇/○/·）和颜色是否可区分？
- [ ] 卡片之间的间距和分组标题是否清晰？
- [ ] 右侧详情面板的文字密度是否合适？太密还是太疏？
- [ ] 里程碑时间线的缩进和层级是否清晰？

### 功能优先级

以下功能尚未实现，请帮排序你认为最重要的：

- [ ] **搜索**: 在所有会话中搜索关键词
- [ ] **标签管理**: 给会话打自定义标签
- [ ] **导出**: 将会话历史导出为 Markdown
- [ ] **批量操作**: 一次性归档多个 DONE 会话
- [ ] **里程碑点击展开**: 点击一个里程碑查看该阶段的对话摘要
- [ ] **分支对比**: 对比同一项目不同分支的工作进展
- [ ] **自动 AI 摘要**: 选中 session 时自动调用 LLM（当前需手动按 `s`）

---

## 八、已知限制（v0.3 阶段）

1. **纯终端 TUI** — 不是 Web 应用，需要 SSH 到服务器使用
2. **AI 摘要需要本地 proxy** — LLM 模式通过 `http://127.0.0.1:4142` 访问 Claude API
3. **不能编辑/删除会话** — 完全只读 Claude Code 数据，只在 `~/.ccsm/` 写 sidecar 元数据
4. **首次加载** — 3000+ 会话的解析需要约 4 秒（后续缓存）
5. **搜索功能** — ✅ 已实现全文模糊搜索（v0.4）
6. **里程碑点击展开** — 当前只是索引，还不能点击查看详情
7. **Session Graph** — 当前为 git-log 风格线性树，v2 将升级为泳道双轴视图

---

## 九、技术架构（了解即可）

```
用户 ──→ TUI (Textual)  ← 主要体验入口
         ├── Left:   Worktree 树
         ├── Middle: Session 卡片列表 (4行 Rich markup)
         └── Right:  Session 详情 (Milestones + Breakpoint)

         CLI (Click)     ← 命令行快捷操作
         MCP Server      ← Claude Code 内集成

         ↕ 调用

Core Library
├── discovery.py    扫描 ~/.claude/projects/
├── parser.py       解析 .jsonl 会话文件
├── status.py       自动推断状态和优先级
├── milestones.py   规则提取里程碑 (零成本)
├── summarizer.py   LLM 生成里程碑 (API 调用)
└── meta.py         读写 ~/.ccsm/ 元数据 + 缓存
```

### Summarizer 双模式

```
选中 Session
    │
    ├─ 有缓存? ──→ 直接读取 ~/.ccsm/summaries/{id}.summary.json
    │
    ├─ 无缓存 ──→ Extract 模式 (自动)
    │             parser.py 解析全部消息
    │             milestones.py 规则匹配阶段转换信号
    │             → 生成里程碑 + 断点
    │             → 缓存到 sidecar
    │
    └─ 按 's' ──→ LLM 模式 (手动)
                  压缩对话 → 发送给 claude-haiku-4.5
                  → 严格 JSON schema 输出
                  → 解析为 Milestone/Breakpoint 数据模型
                  → 覆盖缓存
```

**数据安全**: 完全只读访问 Claude Code 数据（`~/.claude/`），所有用户元数据和摘要缓存存储在独立目录 `~/.ccsm/`。

---

## 十、版本更新日志

### v0.4 (2026-04-02) — Lineage & Search
- ✨ **血缘检测**: 自动识别 fork/compact/duplicate 关系（`core/lineage.py`）
- ✨ **Session DAG**: 按 `g` 查看 session 关系树状图，含 ●/◆/◇/◉ 四种节点类型
- ✨ **全文搜索**: 按 `/` 搜索，覆盖 title/intent/content/branch/tags，无数量上限
- ✨ **Lineage Badge**: 会话卡片显示 fork(蓝)/compact(紫)/dup(红) 标记
- ✨ **时间戳修复**: 按最后实质消息时间排序，"看一眼"不再改变排序
- ✨ **标题锁定**: `lock_title()` 防止 Claude Code 的 64KB 窗口崩溃丢标题
- ✨ **重复检测**: 自动识别多 SSH 终端产生的重复 session
- 🧪 33 个新测试全部通过

### v0.3 (2026-04-02) — Milestone Timeline
- ✨ **Detail 面板重设计**: 5 区块碎片化 → 4 区块里程碑时间线
- ✨ **🧭 MILESTONES**: 紧凑里程碑时间线（✓/▶/○），进行中阶段展开子项
- ✨ **📍 BREAKPOINT**: 独立高亮断点框，直接回答"我停在哪了"
- ✨ **AI Summary**: 按 `s` 调用 Claude API 生成高质量里程碑
- ✨ **双模式 Summarizer**: Extract（规则，免费）+ LLM（API，高质量）
- 🔧 **Detail 滚动修复**: `VerticalScroll` 高度约束修正

### v0.2 (2026-04-02) — Session Card Enhancement
- ✨ Session 卡片 4 行 Rich markup 布局（标题/意图/进展/标签）
- ✨ Detail 面板 Rich + Markdown 渲染
- ✨ NOISE 检测扩展（slash-command + 极短内容规则）
- 🔒 Rich markup 注入防护（`rich_escape`）
- 🔒 路径穿越校验 + MCP TTL 缓存

### v0.1 (2026-04-01) — MVP
- 三面板 TUI 布局（Worktree / Session List / Detail）
- 5 状态自动推断 + 4 优先级
- MCP Server (6 tools) + CLI 骨架
- 3000+ 会话发现与分类

---

*感谢体验！任何想法都可以直接记在这个文档里或者口头告诉我。*
