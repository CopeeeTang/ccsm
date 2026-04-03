# Claude Code Resume (会话恢复) 机制深度分析

> 基于 Claude Code 源码快照 (2026-03-31, fork from instructkr/claude-code)
> 分析日期: 2026-04-02

## 1. 概述

Claude Code 的 Resume 机制允许用户恢复之前的会话，继续未完成的对话。核心实现涉及：

- **CLI 参数**：`--resume`、`--continue`
- **斜杠命令**：`/resume`、`/continue`
- **核心函数**：`loadConversationForResume()` (src/utils/conversationRecovery.ts)
- **存储层**：`sessionStorage.ts` 和 `sessionStoragePortable.ts`

## 2. Resume 与 Continue 的区别

### 2.1 `--continue` (别名: `/continue`)

```
claude --continue
```

- 恢复**最近的会话**（自动选择）
- 跳过活跃的后台 daemon 会话
- 不需要指定 session ID

**实现逻辑**：

```typescript
// src/utils/conversationRecovery.ts - loadConversationForResume()
if (source === undefined) {
  // --continue: most recent session, skipping live --bg/daemon sessions
  const logs = await loadMessageLogs()
  let skip = new Set<string>()
  // 排除活跃的后台会话
  const live = await listAllLiveSessions()
  skip = new Set(
    live.flatMap(s => s.kind && s.kind !== 'interactive' && s.sessionId
      ? [s.sessionId] : [])
  )
  log = logs.find(l => {
    const id = getSessionIdFromLog(l)
    return !id || !skip.has(id)
  }) ?? null
}
```

### 2.2 `--resume [session-id | .jsonl path]`

```
claude --resume abc-def-123
claude --resume /path/to/session.jsonl
```

- 恢复**指定的会话**
- 支持 UUID 格式的 session ID
- 支持直接传入 `.jsonl` 文件路径（跨目录恢复）

**路由逻辑** (src/cli/print.ts)：

```typescript
// 判断 resume 参数是 UUID 还是 .jsonl 路径
typeof options.resume === 'string' &&
  (Boolean(validateUuid(options.resume)) || options.resume.endsWith('.jsonl'))
```

## 3. Session 查找流程

### 3.1 resolveSessionFilePath()

这是定位 session 文件的核心函数：

```
resolveSessionFilePath(sessionId, dir?)
    ↓
    ├── 有 dir 参数？
    │   ├── canonicalizePath(dir) → 标准化路径
    │   ├── findProjectDir(canonical) → 找项目目录
    │   ├── 检查 {projectDir}/{sessionId}.jsonl 是否存在
    │   │   ├── 存在且 size > 0 → 返回
    │   │   └── 不存在 → worktree fallback
    │   └── 遍历 git worktree 路径，在每个 worktree 的项目目录中查找
    └── 无 dir 参数？
        └── 扫描 ~/.claude/projects/ 下所有项目目录
            └── 在每个目录中查找 {sessionId}.jsonl
```

**关键设计**：
- 支持 **worktree fallback**：会话可能在不同的 git worktree 下创建，通过遍历所有 worktree 路径查找
- **零字节文件跳过**：`size > 0` 检查，避免找到被截断的副本
- **hash 容错**：`findProjectDir()` 对超长路径使用前缀匹配，兼容 Bun/Node 不同的哈希算法

### 3.2 loadMessageLogs()

加载当前项目所有可用会话：

```typescript
export async function loadMessageLogs(limit?: number): Promise<LogOption[]> {
  const sessionLogs = await fetchLogs(limit)
  // stat-only 快速加载 → 元数据丰富 → 排序
  const { logs: enriched } = await enrichLogs(sessionLogs, 0, sessionLogs.length)
  return sortLogs(enriched)  // 按修改时间降序
}
```

**渐进式加载**：
1. `getSessionFilesLite()` — 只做 stat（1 次系统调用/文件）
2. `enrichLogs()` — 读取 head/tail 各 64KB 提取元数据
3. 排序后按需全量加载

## 4. 消息加载与反序列化

### 4.1 loadConversationForResume()

这是 resume 的核心入口，返回完整的恢复数据：

```typescript
export async function loadConversationForResume(
  source: string | LogOption | undefined,
  sourceJsonlFile: string | undefined,
): Promise<{
  messages: Message[]                    // 反序列化后的消息列表
  turnInterruptionState: TurnInterruptionState  // 中断检测结果
  fileHistorySnapshots?: FileHistorySnapshot[]
  attributionSnapshots?: AttributionSnapshotMessage[]
  contentReplacements?: ContentReplacementRecord[]
  sessionId: UUID | undefined
  // 会话元数据
  agentName?: string
  agentColor?: string
  agentSetting?: string
  customTitle?: string
  tag?: string
  mode?: 'coordinator' | 'normal'
  worktreeSession?: PersistedWorktreeSession | null
  prNumber?: number
  prUrl?: string
  fullPath?: string
} | null>
```

### 4.2 从 JSONL 到内存消息的转换流程

```
JSONL 文件
    ↓
loadTranscriptFile()        # 解析 JSONL → Map<UUID, TranscriptMessage>
    ↓                       # 同时提取 customTitles, tags, agentNames 等
buildConversationChain()    # parentUuid 链式遍历 → 有序消息数组
    ↓
removeExtraFields()         # 移除 parentUuid, isSidechain 等链式字段
    ↓
loadFullLog()               # 组装完整的 LogOption
    ↓
deserializeMessagesWithInterruptDetection()
    ├── migrateLegacyAttachmentTypes()   # 兼容旧版附件类型
    ├── filterUnresolvedToolUses()       # 过滤未完成的工具调用
    ├── filterOrphanedThinkingOnlyMessages()  # 过滤孤立的 thinking 消息
    ├── filterWhitespaceOnlyAssistantMessages()  # 过滤空白助手消息
    ├── detectTurnInterruption()         # 检测中断状态
    └── 追加合成消息（如果需要继续）
```

### 4.3 ParentUUID 链式结构

JSONL 中的消息通过 `parentUuid` 形成**有向无环图 (DAG)**：

```
消息 A (parentUuid: null)     ← 对话起点
  ↓
消息 B (parentUuid: A.uuid)
  ↓
消息 C (parentUuid: B.uuid)
  ↓
compact_boundary (parentUuid: null)  ← 压缩边界，断链
  ↓
消息 D (parentUuid: null, logicalParentUuid: C.uuid)
```

**buildConversationChain()** 从最新的叶节点反向遍历到根节点，重建线性对话：

```typescript
export function buildConversationChain(
  byUuid: Map<UUID, TranscriptMessage>,
  leaf: TranscriptMessage
): TranscriptMessage[] {
  // 从 leaf 沿 parentUuid 链向上遍历
  // 遇到 compact_boundary 时使用 logicalParentUuid 跨越边界
  // 结果反转得到正序
}
```

### 4.4 Compact Boundary 处理

对于大会话（>5MB），`readTranscriptForLoad()` 使用流式分块读取：

```typescript
const TRANSCRIPT_READ_CHUNK_SIZE = 1024 * 1024  // 1MB 分块

export async function readTranscriptForLoad(
  filePath: string, fileSize: number
): Promise<{
  boundaryStartOffset: number    // 最后一个 boundary 的位置
  postBoundaryBuf: Buffer        // boundary 之后的内容
  hasPreservedSegment: boolean   // 是否有保留段
}>
```

**优化效果**：一个 151MB 的 session 文件（84% 是旧的 attr-snapshot），只需分配 ~32MB 而非 ~159MB。

## 5. 中断检测 (Turn Interruption Detection)

Resume 时会检测会话是否在中途被中断：

### 5.1 三种中断状态

```typescript
type TurnInterruptionState =
  | { kind: 'none' }                          // 正常结束
  | { kind: 'interrupted_prompt'; message }   // 用户发了消息但 Claude 没回复
  // 内部还有 interrupted_turn (工具调用中断)，但会被转换为 interrupted_prompt
```

### 5.2 检测逻辑

```typescript
function detectTurnInterruption(messages): InternalInterruptionState {
  const lastMessage = messages.findLast(m => 
    m.type !== 'system' && m.type !== 'progress' &&
    !(m.type === 'assistant' && m.isApiErrorMessage)
  )

  if (lastMessage.type === 'assistant') return { kind: 'none' }  // 正常完成
  
  if (lastMessage.type === 'user') {
    if (lastMessage.isMeta || lastMessage.isCompactSummary) return { kind: 'none' }
    if (isToolUseResultMessage(lastMessage)) {
      // 检查是否是 Brief 模式的正常结束
      if (isTerminalToolResult(...)) return { kind: 'none' }
      return { kind: 'interrupted_turn' }  // 工具调用中断
    }
    return { kind: 'interrupted_prompt', message: lastMessage }  // 纯文本中断
  }
  
  if (lastMessage.type === 'attachment') return { kind: 'interrupted_turn' }
}
```

### 5.3 中断恢复处理

当检测到 `interrupted_turn` 时，自动注入继续消息：

```typescript
if (internalState.kind === 'interrupted_turn') {
  const continuationMessage = createUserMessage({
    content: 'Continue from where you left off.',
    isMeta: true,
  })
  filteredMessages.push(continuationMessage)
}
```

## 6. 状态恢复

### 6.1 Skill 状态恢复

```typescript
export function restoreSkillStateFromMessages(messages: Message[]): void {
  for (const message of messages) {
    if (message.attachment?.type === 'invoked_skills') {
      for (const skill of message.attachment.skills) {
        addInvokedSkill(skill.name, skill.path, skill.content, null)
      }
    }
    if (message.attachment?.type === 'skill_listing') {
      suppressNextSkillListing()  // 避免重复发送 skill 列表
    }
  }
}
```

### 6.2 Agent 设置恢复

```typescript
// src/cli/print.ts
if (!options.agent && !getMainThreadAgentType() && resumedAgentSetting) {
  const { agentDefinition: restoredAgent } = restoreAgentFromSession(
    resumedAgentSetting, undefined, { activeAgents: agents, allAgents: agents }
  )
  if (restoredAgent) {
    setAppState(prev => ({ ...prev, agent: restoredAgent.agentType }))
    saveAgentSetting(restoredAgent.agentType)
  }
}
```

### 6.3 Session 元数据恢复

Resume 时从 `LogOption` 中恢复：
- `customTitle` — 自定义标题
- `tag` — 标签
- `agentName/agentColor` — Agent 标识
- `agentSetting` — Agent 定义
- `mode` — coordinator/normal 模式
- `worktreeSession` — worktree 状态
- `prNumber/prUrl/prRepository` — PR 关联

### 6.4 Plan 和 File History 恢复

```typescript
// 复制 plan 文件以便 resume 后可继续
await copyPlanForResume(log, asSessionId(sessionId))

// 复制 file history 快照
void copyFileHistoryForResume(log)
```

### 6.5 Session Start Hooks

Resume 时执行 `resume` 类型的 session start hooks：

```typescript
const hookMessages = await processSessionStartHooks('resume', { sessionId })
messages.push(...hookMessages)
```

## 7. 一致性检查

`checkResumeConsistency()` 验证写入→加载的往返一致性：

```typescript
export function checkResumeConsistency(chain: Message[]): void {
  // 找到最后一个 turn_duration 检查点
  // 比较其 messageCount 与链中实际位置
  // delta > 0: resume 加载了比会话中更多的消息
  // delta < 0: resume 丢失了消息（链截断）
  // delta = 0: 完全一致
  logEvent('tengu_resume_consistency_delta', { expected, actual, delta })
}
```

## 8. 跨目录 Resume

### 8.1 JSONL 路径直接传入

```bash
claude --resume /other/project/.claude/projects/-path/session-id.jsonl
```

`loadMessagesFromJsonlPath()` 直接从文件加载，不依赖当前项目目录：

```typescript
export async function loadMessagesFromJsonlPath(path: string): Promise<{
  messages: SerializedMessage[]
  sessionId: UUID | undefined
}> {
  const { messages: byUuid, leafUuids } = await loadTranscriptFile(path)
  // 找到最新的非 sidechain 叶节点
  // 构建对话链
  // 返回消息和 sessionId
}
```

### 8.2 Worktree 跨目录搜索

```typescript
// resolveSessionFilePath 中的 worktree fallback
const worktreePaths = await getWorktreePathsPortable(canonical)
for (const wt of worktreePaths) {
  if (wt === canonical) continue
  const wtProjectDir = await findProjectDir(wt)
  // 在每个 worktree 的项目目录中查找
}
```

## 9. Resume 后的消息追加

Resume 后新消息正常追加到**同一个 JSONL 文件**：

1. `switchSession()` 切换到恢复的 session ID
2. `insertMessageChain()` 继续追加消息
3. 新消息的 `parentUuid` 指向恢复后的最后一条消息
4. 相当于在 DAG 上继续延伸当前分支

## 10. 完整 Resume 时序图

```
用户输入 "claude --resume abc-123"
    ↓
cli/print.ts: loadInitialMessages()
    ↓
conversationRecovery.ts: loadConversationForResume("abc-123")
    ↓
sessionStorage.ts: getLastSessionLog("abc-123")
    ↓
resolveSessionFilePath("abc-123", cwd)
    ├── findProjectDir(cwd) → projectDir
    └── stat(projectDir/abc-123.jsonl) → 文件存在
    ↓
loadFullLog(log)
    ├── loadTranscriptFile(filePath)  # 解析 JSONL
    │   ├── readTranscriptForLoad()   # >5MB: 流式读取 + boundary 跳过
    │   └── parseJSONL()              # <5MB: 全量解析
    ├── buildConversationChain()      # parentUuid 链重建
    └── removeExtraFields()           # 清理链式字段
    ↓
copyPlanForResume()                   # 复制 plan 文件
copyFileHistoryForResume()            # 复制 file history
restoreSkillStateFromMessages()       # 恢复 skill 状态
    ↓
deserializeMessagesWithInterruptDetection()
    ├── 过滤无效消息
    ├── detectTurnInterruption()      # 检测中断
    └── 注入合成消息（如需继续）
    ↓
processSessionStartHooks('resume')    # 执行 resume hooks
    ↓
返回 messages + metadata → REPL 渲染
```
