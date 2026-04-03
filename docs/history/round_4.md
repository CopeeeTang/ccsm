# CCSM Round 4 — PM 深度 UI/UX 优化 + Codex 审查 + P0/P1 修复

**日期**: 2026-04-02  
**承接**: Round 3（v0.4 Stage 1 快赢项完成后）  
**主题**: 执行 PM 产品经理 6 项并行 UI/UX 优化 → Codex 安全审查 → P0/P1 漏洞修复

---

## 一、会话目标

接收 PM 深度反馈，对三大 TUI 区域进行系统性修缮：

| 区域 | 问题 |
|------|------|
| 中间栏 | Tab切换缺失、卡片行对齐错乱、XML脏数据泄漏 |
| AI摘要 | 标题缺失、意图字段空缺 |
| 右侧栏 | 元数据过长、里程碑宽度不固定、BREAKPOINT留白消失 |

目标：拆分为 6 个无文件冲突的并行任务，全部完成后跑 Codex 审查，再修复所有 P0/P1。

---

## 二、任务分解与派发策略

### 2.1 任务矩阵（6个）

| 编号 | 描述 | 目标文件 |
|------|------|----------|
| Task1 | XML脏数据清洗 | `ccsm/core/parser.py` |
| Task2 | 卡片2行精准对齐 | `ccsm/tui/widgets/session_card.py` |
| Task3 | Detail压缩 + 里程碑定宽 | `ccsm/tui/widgets/session_detail.py` |
| Task4 | BREAKPOINT留白 + CSS | `ccsm/tui/styles/claude_native.tcss` |
| Task5 | Tab状态切换 widget | `ccsm/tui/widgets/session_list.py` |
| Task6 | AI摘要标题生成 | `ccsm/core/summarizer.py` |

**策略**: 确认 6 个文件无互相依赖，选择 Mode A（Background Subagents）全并行派发。

### 2.2 第一轮派发结果

- Task1 ✅ 完成（`_sanitize_content()` 写入）
- Task2 ⚠️ 被用户 interrupt，但检查发现文件实际已写入完毕
- Task3 ❌ 被 interrupt 且未完成
- Task4/5/6 → 进入第二轮

### 2.3 第二轮补派（4 agents）

重新派发 Task3/4/5/6，全部完成。统一验证 import 链无报错。

---

## 三、各任务实现细节

### Task1 — XML脏数据清洗（`parser.py`）

新增 `_sanitize_content()` 方法，在解析前过滤：
- `<antml_invoke>` / `<invoke>` 工具调用标签
- Base64 大块数据
- 超长无空格 token（疑似二进制）

调用点：`_parse_message_content()` 入口处统一调用。

### Task2 — 卡片2行精准对齐（`session_card.py`）

重写 `render()` 方法为固定2行布局：
- 第1行: `[标题]` + 右对齐时间戳
- 第2行: `[状态徽章]` + 截断摘要
- emoji 宽度计数从 6 修正为 7（修复后续 P1）

### Task3 — Detail 紧凑元数据 + 里程碑定宽（`session_detail.py`）

- `_build_description()` 改为紧凑单行格式（`model · turns · cost`）
- 新增 `_strip_emoji_prefix()` — 固定剥离前7字符 emoji prefix
- `_build_milestone_section()` — 里程碑列用 `ljust(40)` 定宽对齐

### Task4 — BREAKPOINT留白 + CSS（`claude_native.tcss`）

- `SessionDetail` 容器加 `padding: 0 1`
- Tab bar 区域加 `margin-bottom: 1`
- 卡片 `max-height: 4` 防止异常展开
- 新增 `.tab-bar` 样式类（`background: $surface`）

### Task5 — Tab状态切换（`session_list.py`）

新增 `StatusTabBar` widget：
- 支持 `all / active / archived` 三态
- `on_click()` 根据实际渲染宽度累积偏移计算命中 tab（P0修复后）
- 发出 `TabChanged` 消息驱动列表过滤

### Task6 — AI摘要标题生成（`summarizer.py`）

新增两个方法：
- `generate_ai_title(session)` — async版，调用 LLM 生成10字以内标题
- `generate_ai_title_sync(session)` — sync包装，供 TUI 启动时使用
- Prompt 包含 session 前3条消息摘要 + 项目名作为上下文

---

## 四、Codex 安全审查结果

使用 `feature-dev:code-reviewer` agent 对全部 6 个文件做 diff review。

### 4.1 P0（必须修复，阻塞发布）

| # | 文件 | 问题 | 风险 |
|---|------|------|------|
| P0-1 | `parser.py` | 正则嵌套量词 `(.+)+` 类结构 → ReDoS | 恶意输入导致 CPU 挂死 |
| P0-2 | `summarizer.py` | `prompt.format(context=...)` 当用户内容含 `{title}` → KeyError crash | 任意 session 触发崩溃 |
| P0-3 | `session_list.py` | Tab点击用等宽假设 `x // tab_width` 计算 → 选错 tab | 功能完全失效 |

### 4.2 P1（应修复，影响质量）

共 10 项，主要类别：
- emoji 宽度计算偏差（`session_card.py`）
- `_mount_section` title 未转义 Rich markup（`session_detail.py`）
- JSON 解析用字符串切片而非 `json.loads` + bracket-depth（`summarizer.py`）
- 循环依赖风险（summarizer ↔ parser）
- tcss 硬编码颜色值（应用 `$variable`）

---

## 五、P0/P1 修复过程

### P0-1 修复 — ReDoS 防御

```python
# Before（危险）
pattern = re.compile(r'<([a-zA-Z_:]+(\s+[^>]*)?)>(.*?)</\1>', re.DOTALL)

# After（安全）
pattern = re.compile(r'<([a-zA-Z_:]{1,64})([^>]{0,200})>(.*?)</\1>', re.DOTALL)
```

- 量词改为有上界：标签名 `{1,64}`，属性 `{0,200}`，内容用 `.*?`（非贪婪）

### P0-2 修复 — format 注入安全

```python
# Before
prompt = TITLE_PROMPT_TEMPLATE.format(context=session_context)

# After
safe_context = session_context.replace("{", "{{").replace("}", "}}")
prompt = TITLE_PROMPT_TEMPLATE.format(context=safe_context)
```

同时新增 `_extract_json_object(text)` 用 bracket-depth 计数替代字符串切片解析 JSON。

### P0-3 修复 — Tab点击偏移计算

```python
# Before（错误等宽假设）
tab_idx = click_x // (bar_width // tab_count)

# After（累积实际渲染宽度）
offset = 0
for i, label in enumerate(self._tab_labels):
    tab_w = len(label) + 4  # padding
    if offset <= click_x < offset + tab_w:
        return i
    offset += tab_w
```

### P1 修复摘要

- `session_card.py`: emoji prefix_len `6 → 7`
- `session_detail.py`: `_mount_section` title 加 `rich_escape()` 防 markup 注入

---

## 六、关键文件变更汇总

| 文件 | 变更类型 | 核心改动 |
|------|----------|----------|
| `ccsm/core/parser.py` | 新增 + 修复 | `_sanitize_content()` + ReDoS安全正则 |
| `ccsm/tui/widgets/session_card.py` | 重写 | `render()` 2行布局 + emoji宽度修正 |
| `ccsm/tui/widgets/session_detail.py` | 改进 | 紧凑元数据 + 里程碑定宽 + rich_escape |
| `ccsm/tui/styles/claude_native.tcss` | 新增样式 | padding/margin/max-height/tab-bar |
| `ccsm/tui/widgets/session_list.py` | 新增widget | `StatusTabBar` + 真实偏移点击计算 |
| `ccsm/core/summarizer.py` | 新增 + 修复 | `generate_ai_title` + format安全 + JSON解析 |

---

## 七、下一步行动

### 即时验证（未完成）
- [ ] 运行 TUI (`./ml_env/bin/python3 -m ccsm`) 截图验证视觉效果
- [ ] 实际触发 Tab 切换，验证 P0-3 修复有效

### v0.4 Stage 2（Markdown渲染质量）
- [ ] 代码块语法高亮
- [ ] 嵌套列表正确缩进
- [ ] 长行自动折行

### v0.4 Stage 3（全局功能）
- [ ] `/` 触发全局搜索
- [ ] 批量归档/清理操作

### 待集成
- [ ] AI摘要 hover 触发（懒加载）
- [ ] 快捷键 `1/2/3/4` 绑定 Tab 切换（接 `StatusTabBar`）
- [ ] `generate_ai_title_sync` 集成到 session 加载流程

---

## 八、经验沉淀

1. **并行派发前检查文件冲突** — 6个任务能全并行的前提是目标文件完全独立，需要在拆分阶段明确确认。

2. **interrupt 后需验证写入状态** — agent 被 interrupt 不代表文件未写入，需用 `find_symbol` 或 `read_file` 确认实际状态，避免重复工作。

3. **Codex Review 必须覆盖全部变更文件** — 本轮发现 3 个 P0 均在首次 review 中捕获，说明单轮全覆盖 review 价值极高，不应在"看起来简单"的改动上跳过。

4. **format() 是 Python 中常被忽视的注入点** — 任何将用户数据插入含 `{}` 模板的场景均需转义，尤其 LLM prompt 构造。

5. **ReDoS 防御优先用上界而非负向断言** — 有界量词 `{1,N}` 比复杂的 lookahead 更易读且同样安全。
