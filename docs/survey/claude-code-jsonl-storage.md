# Claude Code JSONL 历史记录存储机制深度分析

> 基于 Claude Code 源码快照 (2026-03-31, fork from instructkr/claude-code)
> 分析日期: 2026-04-02

## 1. 概述

Claude Code 使用 **JSONL (JSON Lines)** 格式作为会话历史的持久化存储格式。每个会话对应一个独立的 `.jsonl` 文件，每一行是一个独立的 JSON 对象，代表一条消息或元数据条目。

这种设计的核心优势：
- **追加写入 (append-only)**：只需 `appendFileSync`，无需读取-修改-写入
- **崩溃安全**：即使进程中断，已写入的行仍然完整
- **流式读取**：可以逐行解析，无需将整个文件加载到内存

## 2. 存储路径规则

### 2.1 目录结构

```
~/.claude/
├── projects/                          # 所有项目的会话目录
│   ├── -home-user-my-project/         # 项目路径经 sanitizePath 转换
│   │   ├── {session-uuid}.jsonl       # 主会话文件
│   │   ├── {session-uuid}/            # 会话子目录
│   │   │   ├── subagents/             # 子 Agent 的 transcript
│   │   │   │   ├── agent-{id}.jsonl
│   │   │   │   └── agent-{id}.meta.json
│   │   │   └── remote-agents/         # 远程 Agent 元数据
│   │   │       └── remote-agent-{taskId}.meta.json
│   │   └── ...
│   └── -Users-foo-another-project/
│       └── ...
└── history.jsonl                      # 全局输入历史 (跨项目共享)
```

### 2.2 路径生成逻辑

**项目目录名** 由 `sanitizePath()` 函数生成：

```typescript
// src/utils/sessionStoragePortable.ts
export function sanitizePath(name: string): string {
  const sanitized = name.replace(/[^a-zA-Z0-9]/g, '-')
  if (sanitized.length <= MAX_SANITIZED_LENGTH) {  // MAX = 200
    return sanitized
  }
  // 超长路径加 hash 后缀保证唯一性
  const hash = typeof Bun !== 'undefined' 
    ? Bun.hash(name).toString(36) 
    : simpleHash(name)
  return `${sanitized.slice(0, MAX_SANITIZED_LENGTH)}-${hash}`
}
```

**关键设计细节**：
- 路径中的所有非字母数字字符被替换为 `-`
- 超过 200 字符的路径附加 DJB2 哈希后缀
- Bun 运行时和 Node.js 运行时使用不同的哈希算法，`findProjectDir()` 通过前缀匹配兼容两者

**会话文件路径**：

```typescript
// src/utils/sessionStorage.ts
export function getTranscriptPath(): string {
  const projectDir = getSessionProjectDir() ?? getProjectDir(getOriginalCwd())
  return join(projectDir, `${getSessionId()}.jsonl`)
}
```

- Session ID 是标准 UUID v4 格式：`xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx`
- 文件名即为 `{session-id}.jsonl`

## 3. JSONL 行的数据结构

每一行是一个 JSON 对象，其 `type` 字段决定了条目类型。所有类型定义在 `src/types/logs.ts` 的 `Entry` 联合类型中。

### 3.1 消息类型 (TranscriptMessage)

对话消息是 JSONL 的主体内容：

```typescript
export type TranscriptMessage = SerializedMessage & {
  uuid: UUID               // 消息唯一标识
  parentUuid: UUID | null  // 父消息 UUID（形成链式结构）
  isSidechain: boolean     // 是否为分支对话
  agentId?: string         // 子 Agent ID
  teamName?: string        // 团队名称
  agentName?: string       // Agent 自定义名称
  promptId?: string        // 关联 OTel prompt.id
}

export type SerializedMessage = Message & {
  cwd: string              // 工作目录
  userType: string         // 用户类型
  entrypoint?: string      // 入口点 (cli/sdk-ts/sdk-py 等)
  sessionId: string        // 会话 ID
  timestamp: string        // ISO 8601 时间戳
  version: string          // Claude Code 版本号
  gitBranch?: string       // Git 分支名
  slug?: string            // 会话 slug (用于 plan 文件关联)
}
```

消息的 `type` 可以是：
- `user` — 用户消息
- `assistant` — Claude 回复
- `attachment` — 附件（文件、目录、技能等）
- `system` — 系统消息（compact boundary、turn duration 等）

**注意**：`progress` 类型消息**不参与** JSONL 持久化链（PR #24099 之后），它们是临时 UI 状态。

### 3.2 元数据类型

除了对话消息，JSONL 中还混合存储了多种元数据条目：

| 类型 | 字段 | 用途 |
|------|------|------|
| `custom-title` | `customTitle` | 用户手动重命名的标题 |
| `ai-title` | `aiTitle` | AI 自动生成的标题 |
| `last-prompt` | `lastPrompt` | 用户最近的输入（用于列表显示） |
| `tag` | `tag` | 会话标签（可搜索） |
| `agent-name` | `agentName` | Agent 显示名称 |
| `agent-color` | `agentColor` | Agent 颜色标识 |
| `agent-setting` | `agentSetting` | Agent 定义类型 |
| `mode` | `mode` | 会话模式 (coordinator/normal) |
| `worktree-state` | `worktreeSession` | Worktree 会话状态 |
| `pr-link` | `prNumber/prUrl/prRepository` | GitHub PR 关联 |
| `summary` | `summary` | 对话摘要 |
| `task-summary` | `summary` | 周期性任务摘要（供 `claude ps`） |
| `file-history-snapshot` | `snapshot` | 文件历史快照 |
| `attribution-snapshot` | — | 归因跟踪快照 |
| `content-replacement` | `replacements` | 内容替换记录 |
| `marble-origami-commit` | — | Context collapse 提交记录 |
| `marble-origami-snapshot` | — | Context collapse 快照 |

### 3.3 实际 JSONL 文件示例

```jsonl
{"type":"user","message":{"role":"user","content":"帮我写一个排序函数"},"uuid":"abc-123","parentUuid":null,"isSidechain":false,"cwd":"/home/user/project","sessionId":"def-456","timestamp":"2026-04-02T08:00:00.000Z","version":"2.1.0","gitBranch":"main"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"好的..."}]},"uuid":"ghi-789","parentUuid":"abc-123","isSidechain":false,"cwd":"/home/user/project","sessionId":"def-456","timestamp":"2026-04-02T08:00:05.000Z","version":"2.1.0"}
{"type":"custom-title","customTitle":"排序函数开发","sessionId":"def-456"}
{"type":"agent-name","agentName":"排序函数开发","sessionId":"def-456"}
```

## 4. 写入机制

### 4.1 消息写入流程

消息写入由 `Project` 类管理，采用**异步队列 + 定期刷新**的机制：

```
用户输入/Claude 回复
    ↓
insertMessageChain()        # 公开 API
    ↓
appendEntry()               # 添加到写队列
    ↓
enqueueWrite()              # 入队
    ↓
scheduleFlush()             # 启动 100ms 定时器
    ↓
drainWriteQueue()           # 批量写入磁盘
    ↓
fsAppendFile()              # 实际文件追加
```

**关键参数**：
- `FLUSH_INTERVAL_MS = 100`：队列刷新间隔 100ms
- `MAX_CHUNK_BYTES = 100MB`：单次写入最大字节数
- 文件权限 `0o600`（仅所有者可读写）

### 4.2 延迟实体化 (Lazy Materialization)

Session 文件**不在启动时创建**，而是延迟到第一条用户/助手消息时：

```typescript
// src/utils/sessionStorage.ts - insertMessageChain()
if (this.sessionFile === null &&
    messages.some(m => m.type === 'user' || m.type === 'assistant')) {
  await this.materializeSessionFile()
}
```

这意味着：
1. 启动后如果用户直接退出，不会产生空的 session 文件
2. Hook 的 progress/attachment 消息会先缓存在 `pendingEntries` 中
3. `materializeSessionFile()` 被调用时，先写入缓存的元数据（mode、agentSetting 等），再刷新待写入条目

### 4.3 元数据写入 — 同步追加

元数据（customTitle、tag 等）使用**同步写入**：

```typescript
function appendEntryToFile(fullPath: string, entry: Record<string, unknown>): void {
  const fs = getFsImplementation()
  const line = jsonStringify(entry) + '\n'
  try {
    fs.appendFileSync(fullPath, line, { mode: 0o600 })
  } catch {
    fs.mkdirSync(dirname(fullPath), { mode: 0o700 })
    fs.appendFileSync(fullPath, line, { mode: 0o600 })
  }
}
```

同步写入确保元数据立即持久化，不受进程崩溃影响。

### 4.4 退出时的 Cleanup

进程退出时执行关键清理：

```typescript
registerCleanup(async () => {
  // 1. 刷新队列中未写入的消息
  await project?.flush()
  // 2. 重新追加元数据到文件末尾
  project?.reAppendSessionMetadata()
})
```

`reAppendSessionMetadata()` 是一个精巧的设计：将 customTitle、tag 等元数据**重新追加到文件末尾**。原因是：
- 读取器（如 `readLiteMetadata`）只读文件的**头部 64KB + 尾部 64KB**
- 如果大量消息追加后，原来的 title 条目可能被推出尾部窗口
- 重新追加确保元数据始终在尾部可见

## 5. 全局输入历史 (history.jsonl)

除了会话 transcript，还有一个独立的**全局输入历史**系统：

```
~/.claude/history.jsonl
```

### 5.1 数据结构

```typescript
type LogEntry = {
  display: string                              // 显示文本
  pastedContents: Record<number, StoredPastedContent>  // 粘贴内容
  timestamp: number                            // 时间戳 (ms)
  project: string                              // 项目路径
  sessionId?: string                           // 会话 ID
}
```

### 5.2 写入时机

每次用户提交输入时通过 `addToHistory()` 写入：
- 采用**异步缓冲 + 批量刷新**策略
- 使用文件锁 (`lockfile`) 避免多进程并发写入冲突
- 大粘贴内容（>1024 字符）通过 hash 存储到独立的 paste store，JSONL 只记录 hash 引用

### 5.3 读取逻辑

`getHistory()` 返回当前项目的历史，当前 session 优先排序：

```typescript
export async function* getHistory(): AsyncGenerator<HistoryEntry> {
  // 先返回当前 session 的条目
  // 再返回其他 session 的条目
  // 窗口大小: MAX_HISTORY_ITEMS = 100
}
```

支持 `removeLastFromHistory()` 用于撤销自动恢复的中断输入。

## 6. 大文件优化

### 6.1 Pre-compact Skip

对于超过 5MB 的大 session 文件，使用 `readTranscriptForLoad()` 进行优化：

```typescript
const SKIP_PRECOMPACT_THRESHOLD = 5 * 1024 * 1024  // 5MB
```

- 找到最后一个 `compact_boundary` 标记
- 只加载 boundary 之后的内容
- 通过 `scanPreBoundaryMetadata()` 从 boundary 之前恢复必要的元数据

### 6.2 Head/Tail 快速读取

`readHeadAndTail()` 只读文件的首尾各 64KB：

```typescript
export const LITE_READ_BUF_SIZE = 65536  // 64KB
```

- Head: 提取 firstPrompt、sessionId、cwd、createdAt
- Tail: 提取 customTitle、tag、lastPrompt、agentName

### 6.3 Tombstone 移除

删除消息时使用**就地修改**而非全文重写：
- 先读取尾部 64KB 定位目标行
- 通过 `ftruncate` + `fwrite` 原地移除
- 超过 50MB 的文件不执行全文重写，避免 OOM

## 7. 总结

Claude Code 的 JSONL 存储是一个**高度优化的 append-only 日志系统**：

1. **写入路径**：异步队列 → 100ms 批量刷新 → `appendFileSync`
2. **读取路径**：Head/Tail 快速扫描 + compact boundary 跳过
3. **消息 + 元数据混合存储**：同一个文件，通过 `type` 字段区分
4. **延迟实体化**：避免产生空文件
5. **退出时 re-append**：确保元数据始终在尾部窗口内
