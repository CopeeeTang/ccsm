# CCSM — Claude Code Session Manager

Always response in Chinese

## 环境配置

虚拟环境（共享 GUI 项目的 ml_env）:
```bash
source /home/v-tangxin/GUI/ml_env/bin/activate
```
使用 `python3`，不要用 `python`

GPU: NVIDIA A100 80GB PCIe
CUDA驱动版本: 13.0 (Driver 580.95.05)
PyTorch CUDA版本: 12.8 (完全兼容)

## 基础设施

- GPT-4o via Azure proxy: `http://52.151.57.21:9999`（NEVER call official API endpoints directly）
- Haiku API 用于 AI 标题/摘要生成（通过 ccsm/core/summarizer.py）
- 数据存储: `~/.ccsm/`（meta + summaries）
- Claude 会话数据: `~/.claude/projects/` (只读，CCSM 不修改)

## 常用命令

### 运行 TUI
```bash
cd /home/v-tangxin/GUI/projects/ccsm
PYTHONPATH=.:$PYTHONPATH python3 -m ccsm
```

### 运行测试
```bash
cd /home/v-tangxin/GUI/projects/ccsm
python3 -m pytest tests/ -v
```

### 单个测试文件
```bash
python3 -m pytest tests/test_e2e_pipeline.py -v
python3 -m pytest tests/test_lineage.py -v
```

### 安装为包（可选）
```bash
pip install -e /home/v-tangxin/GUI/projects/ccsm
ccsm
```

## 项目结构

```
ccsm/
├── core/           # 后端逻辑（无 UI 依赖）
│   ├── discovery   # 扫描 ~/.claude/projects/ 发现会话
│   ├── parser      # JSONL 解析 + XML 清洗
│   ├── lineage     # Fork/Compact/Duplicate 血缘检测
│   ├── index       # 全文模糊搜索索引
│   ├── status      # 会话状态分类 ACTIVE/BACK/IDEA/DONE
│   ├── milestones  # 规则+LLM 里程碑提取
│   ├── summarizer  # 双模式摘要器（规则/LLM）
│   ├── workflow    # 工作流聚类
│   ├── cluster     # AI 聚类命名
│   └── meta        # Sidecar 元数据读写
├── models/         # 数据类定义
├── tui/            # Textual TUI 界面
├── mcp/            # MCP Server
└── cli/            # Click CLI
tests/              # 48 个测试用例
docs/               # 历史记录 + 设计文档 + 调研
```

## 知识库（docs/ 目录）

新会话开始时，如需恢复上下文，优先查阅以下文档：

### 会话历史（docs/history/）
已有 8 轮开发历史，按时间顺序记录了从 v1 到当前的完整演进：
- `round_1.md` ~ `round_8.md` — 每轮的行动路线、关键决策、实验结果

### 设计文档（docs/plans/）
- `2026-04-01-ccsm-design.md` — 初始架构设计
- `2026-04-02-ccsm-workflow-swimlane-v2.md` — 工作流泳道图设计
- `2026-04-02-ccsm-v2-tasks-4-7.md` — v2 任务 4-7 实施计划
- `2026-04-02-ccsm-rule-audit-fixes.md` — 规则审计修复
- `2026-04-02-ccsm-resume-painpoints.md` — Resume 痛点分析

### 调研文档（docs/survey/）
- `ccsm-architecture-overview.md` — 架构总览
- `ccsm-tui-pipeline.md` — TUI 数据管线
- `ccsm-lineage-detection.md` — 血缘检测机制
- `ccsm-sidecar-metadata.md` — Sidecar 元数据设计
- `ccsm-search-index.md` — 搜索索引实现
- `claude-code-jsonl-storage.md` — Claude Code JSONL 存储格式
- `claude-code-resume-mechanism.md` — Claude Code Resume 机制
- `claude-code-rename-mechanism.md` — Claude Code Rename 机制

### 产品评审
- `ccsm-product-review-guide.md` — 产品评审指南

## 会话历史保存

当对话内容丰富（多轮探索、重要决策、实验结果）且 context 使用较多时，主动提醒用户运行 /save-session 保存行动路线摘要。
如果用户即将结束会话或切换话题，也应建议保存。
保存目录: docs/history/round_{N}.md（接续现有 round_8 之后）

## 代码规范

- 后端 core/ 模块不允许引入 TUI 依赖（textual/rich widgets）
- 所有用户内容必须 rich_escape() 防注入
- Session ID 用正则 `^[a-zA-Z0-9_-]+$` 验证防路径穿越
- 测试修改后必须通过全部 48 个测试
