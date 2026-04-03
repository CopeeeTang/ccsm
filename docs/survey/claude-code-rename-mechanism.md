# Claude Code Rename (会话重命名) 机制深度分析

> 基于 Claude Code 源码快照 (2026-03-31, fork from instructkr/claude-code)
> 分析日期: 2026-04-02

## 1. 概述

Claude Code 的 Rename 机制允许用户为会话设置自定义标题，支持手动命名和 AI 自动生成。整个系统围绕**追加式元数据条目**设计，标题信息直接写入 JSONL transcript 文件中。

**核心文件**：
- `src/commands/rename/rename.ts` — `/rename` 命令实现
- `src/commands/rename/generateSessionName.ts` — AI 标题生成
- `src/utils/sessionStorage.ts` — `saveCustomTitle()`, `saveAiGeneratedTitle()`, `reAppendSessionMetadata()`
- `src/utils/listSessionsImpl.ts` — 标题读取与显示优先级

## 2. /rename 命令

### 2.1 命令定义

```typescript
// src/commands/rename/index.ts
const rename = {
  type: 'local-jsx',
  name: 'rename',
  description: 'Rename the current conversation',
  immediate: true,            // 立即执行，不等待 Claude 回复
  argumentHint: '[name]',     // 可选参数
  load: () => import('./rename.js'),
}
```

### 2.2 执行逻辑

```typescript
// src/commands/rename/rename.ts
export async function call(onDone, context, args): Promise<null> {
  // 1. 团队成员不能重命名（名称由 team leader 设置）
  if (isTeammate()) {
    onDone('Cannot rename: teammate names are set by the team leader.')
    return null
  }

  // 2. 确定新名称
  let newName: string
  if (!args || args.trim() === '') {
    // 无参数 → AI 自动生成
    const generated = await generateSessionName(
      getMessagesAfterCompactBoundary(context.messages),
      context.abortController.signal,
    )
    if (!generated) {
      onDone('Could not generate a name: no conversation context yet.')
      return null
    }
    newName = generated
  } else {
    newName = args.trim()  // 使用用户提供的名称
  }

  // 3. 保存自定义标题
  await saveCustomTitle(sessionId, newName, fullPath)

  // 4. 同步到 Bridge（claude.ai/code 网页端）
  if (bridgeSessionId) {
    void updateBridgeSessionTitle(bridgeSessionId, newName, { ... })
  }

  // 5. 同时保存为 Agent Name（prompt bar 显示）
  await saveAgentName(sessionId, newName, fullPath)
  
  // 6. 更新应用状态
  context.setAppState(prev => ({
    ...prev,
    standaloneAgentContext: { ...prev.standaloneAgentContext, name: newName }
  }))

  onDone(`Session renamed to: ${newName}`)
}
```

**关键行为**：
- `/rename` 不带参数：调用 Haiku 模型自动生成 kebab-case 标题
- `/rename my-title`：使用用户提供的标题
- 标题同时存储在两个位置：`custom-title` 和 `agent-name` 条目

## 3. AI 自动生成标题

### 3.1 生成逻辑

```typescript
// src/commands/rename/generateSessionName.ts
export async function generateSessionName(
  messages: Message[],
  signal: AbortSignal,
): Promise<string | null> {
  // 1. 提取对话文本
  const conversationText = extractConversationText(messages)
  if (!conversationText) return null

  // 2. 调用 Haiku 模型
  const result = await queryHaiku({
    systemPrompt: asSystemPrompt([
      'Generate a short kebab-case name (2-4 words) that captures ' +
      'the main topic of this conversation. Use lowercase words ' +
      'separated by hyphens. Examples: "fix-login-bug", ' +
      '"add-auth-feature", "refactor-api-client". ' +
      'Return JSON with a "name" field.',
    ]),
    userPrompt: conversationText,
    outputFormat: {
      type: 'json_schema',
      schema: {
        type: 'object',
        properties: { name: { type: 'string' } },
        required: ['name'],
        additionalProperties: false,
      },
    },
    signal,
    options: { querySource: 'rename_generate_name', ... },
  })

  // 3. 解析 JSON 响应
  const response = safeParseJSON(extractTextContent(result.message.content))
  if (response?.name && typeof response.name === 'string') {
    return response.name
  }
  return null
}
```

**设计特点**：
- 使用 **Haiku 模型**（最快的 Claude 模型），最小化延迟
- 输出格式为 **kebab-case**：`fix-login-bug`、`add-auth-feature`
- 错误只记录 debug 日志，不抛出异常（网络/速率限制是预期的操作性故障）

### 3.2 自动触发时机

AI 标题生成不仅限于 `/rename` 命令。在 `initReplBridge.ts` 中，每第 3 条 bridge 消息时自动触发：

```typescript
// initReplBridge.ts（推断自 generateSessionName.ts 注释）
// 每 3 条 bridge 消息自动调用一次
void generateSessionName(messages, signal)
```

## 4. 标题的持久化存储

### 4.1 两种标题类型

| 条目类型 | JSONL `type` | 字段 | 优先级 |
|---------|-------------|------|--------|
| 用户标题 | `custom-title` | `customTitle` | **最高** — 用户设置始终优先 |
| AI 标题 | `ai-title` | `aiTitle` | 次优先 — AI 可以覆盖自己的旧标题 |

### 4.2 saveCustomTitle()

```typescript
export async function saveCustomTitle(
  sessionId: UUID,
  customTitle: string,
  fullPath?: string,
  source: 'user' | 'auto' = 'user',
) {
  const resolvedPath = fullPath ?? getTranscriptPathForSession(sessionId)
  
  // 追加 JSONL 条目
  appendEntryToFile(resolvedPath, {
    type: 'custom-title',
    customTitle,
    sessionId,
  })
  
  // 更新内存缓存
  if (sessionId === getSessionId()) {
    getProject().currentSessionTitle = customTitle
  }
  
  // 记录分析事件
  logEvent('tengu_session_renamed', { source })
}
```

**写入到 JSONL 的实际内容**：

```jsonl
{"type":"custom-title","customTitle":"fix-login-bug","sessionId":"abc-def-123"}
```

### 4.3 saveAiGeneratedTitle()

```typescript
export function saveAiGeneratedTitle(sessionId: UUID, aiTitle: string): void {
  appendEntryToFile(getTranscriptPathForSession(sessionId), {
    type: 'ai-title',
    aiTitle,
    sessionId,
  })
  // 注意：不更新内存缓存！不记录 renamed 事件！
}
```

**与 custom-title 的关键区别**：

1. **不更新内存缓存**：AI 标题不影响当前进程的 `currentSessionTitle`
2. **不被 reAppend**：退出时 `reAppendSessionMetadata()` 不会重新追加 AI 标题
3. **不触发分析事件**：`tengu_session_renamed` 只记录用户操作
4. **CAS 语义**：VS Code 的 `onlyIfNoCustomTitle` 检查只匹配 `customTitle` 字段，AI 可以覆盖自己的旧 AI 标题，但不能覆盖用户标题

### 4.4 saveAgentName()

```typescript
export async function saveAgentName(
  sessionId: UUID,
  agentName: string,
  fullPath?: string,
) {
  appendEntryToFile(resolvedPath, { type: 'agent-name', agentName, sessionId })
  if (sessionId === getSessionId()) {
    getProject().currentSessionAgentName = agentName
    void updateSessionName(agentName)  // 更新并发会话名称
  }
}
```

`/rename` 同时写入 `custom-title` 和 `agent-name`，因为：
- `custom-title` 用于会话列表和 resume 标题
- `agent-name` 用于 prompt bar 中的 Agent 名称显示

## 5. 标题读取的优先级

### 5.1 会话列表中的标题解析

```typescript
// src/utils/listSessionsImpl.ts - parseSessionInfoFromLite()
const customTitle =
  extractLastJsonStringField(tail, 'customTitle') ||   // Tail 中的用户标题
  extractLastJsonStringField(head, 'customTitle') ||   // Head 中的用户标题
  extractLastJsonStringField(tail, 'aiTitle') ||       // Tail 中的 AI 标题
  extractLastJsonStringField(head, 'aiTitle') ||       // Head 中的 AI 标题
  undefined
```

**优先级链**：

```
customTitle (tail) > customTitle (head) > aiTitle (tail) > aiTitle (head)
```

- 用户标题 (customTitle) **始终优先于** AI 标题 (aiTitle)
- 同一类型中，tail（文件末尾）**优先于** head（文件头部），因为后写入的更新

### 5.2 会话摘要的 fallback 链

```typescript
const summary =
  customTitle ||                                           // 1. 自定义标题
  extractLastJsonStringField(tail, 'lastPrompt') ||       // 2. 最近的用户输入
  extractLastJsonStringField(tail, 'summary') ||          // 3. 旧格式摘要
  firstPrompt                                             // 4. 第一条用户消息
```

### 5.3 Head/Tail 64KB 窗口的影响

读取器只扫描文件的**头部 64KB + 尾部 64KB**。当大量消息追加后，标题条目可能被推出尾部窗口。这就是 `reAppendSessionMetadata()` 存在的原因：

```typescript
// 退出时重新追加元数据到文件末尾
reAppendSessionMetadata(): void {
  // 1. 同步读取尾部，刷新外部写入的值
  const tail = readFileTailSync(this.sessionFile)
  
  // 2. 从 tail 中检查是否有更新的 customTitle
  const tailTitle = extractLastJsonStringField(titleLine, 'customTitle')
  if (tailTitle !== undefined) {
    this.currentSessionTitle = tailTitle || undefined
  }
  
  // 3. 重新追加（即使值已在 tail 中）
  if (this.currentSessionTitle) {
    appendEntryToFile(this.sessionFile, {
      type: 'custom-title',
      customTitle: this.currentSessionTitle,
      sessionId,
    })
  }
  // ... 同样处理 tag, agentName, agentColor, mode 等
}
```

**为什么无条件重新追加？**

> 在 compaction 期间，一个距离 EOF 40KB 的 title 目前在 tail 窗口内，但 compaction 后的新会话增长后会滑出。跳过 re-append 会让这个功能失效。

## 6. Rename 后 Resume 的行为

### 6.1 标题在 Resume 中的恢复

```typescript
// conversationRecovery.ts - loadConversationForResume()
return {
  messages,
  customTitle: log?.customTitle,  // 从 loadTranscriptFile 提取
  agentName: log?.agentName,
  // ...
}
```

`loadTranscriptFile()` 解析 JSONL 时构建 `customTitles` Map：

```typescript
export async function loadTranscriptFile(filePath: string): Promise<{
  customTitles: Map<UUID, string>     // sessionId → customTitle
  agentNames: Map<UUID, string>       // sessionId → agentName
  // ...
}>
```

只收集 `custom-title` 条目（不收集 `ai-title`），确保：
- 用户 rename 始终被恢复
- AI 标题不会在 resume 后被 reAppend（避免覆盖用户标题）

### 6.2 cacheSessionTitle()

启动时通过 `--name` 设置的标题只做内存缓存：

```typescript
export function cacheSessionTitle(customTitle: string): void {
  getProject().currentSessionTitle = customTitle
}
```

直到 `materializeSessionFile()` 调用时才写入磁盘。

## 7. Bridge 同步 (claude.ai/code)

`/rename` 还会同步到 claude.ai 的 bridge session：

```typescript
// rename.ts
const bridgeSessionId = appState.replBridgeSessionId
if (bridgeSessionId) {
  void updateBridgeSessionTitle(bridgeSessionId, newName, {
    baseUrl: getBridgeBaseUrlOverride(),
    getAccessToken: tokenOverride ? () => tokenOverride : undefined,
  }).catch(() => {})  // best-effort, 不阻塞
}
```

- **非阻塞**：使用 `void ... .catch()` 模式
- 仅在有 bridge session 时触发（即通过 claude.ai/code 使用时）

## 8. 并发安全

### 8.1 外部写入者检测

VS Code 扩展和 CLI 可以同时写入同一个 session 文件。`reAppendSessionMetadata()` 在重新追加前会从 tail 刷新值：

```typescript
// 如果 SDK 在我们打开 session 期间写入了新标题，
// 我们的缓存是过时的 — tail 值是权威的
const tailTitle = extractLastJsonStringField(titleLine, 'customTitle')
if (tailTitle !== undefined) {
  this.currentSessionTitle = tailTitle || undefined
}
```

### 8.2 空标题清除

SDK 的 `renameSession(id, null)` 写入 `customTitle:""`：

```typescript
// renameSession rejects empty titles, but the CLI is defensive:
// an external writer with customTitle:"" should clear the cache
if (tailTitle !== undefined) {
  this.currentSessionTitle = tailTitle || undefined  // "" → undefined
}
```

## 9. Session 的完整生命周期

```
1. 创建
   └── 用户启动 `claude` → 生成 UUID v4 sessionId
   └── sessionFile = null (延迟实体化)
   
2. 命名（可能发生多次）
   ├── 自动：AI 每 3 条消息尝试生成 → saveAiGeneratedTitle()
   ├── 手动：/rename [name] → saveCustomTitle() + saveAgentName()
   └── 启动时：--name "title" → cacheSessionTitle()
   
3. 使用
   ├── 第一条用户消息 → materializeSessionFile() 创建 JSONL
   ├── 后续消息 → insertMessageChain() 追加
   ├── compact → 插入 compact_boundary
   └── 元数据变更 → appendEntryToFile() 同步追加

4. 退出
   ├── flush() 刷新队列
   └── reAppendSessionMetadata() 重写元数据到 EOF

5. 恢复
   ├── --resume / --continue
   ├── loadConversationForResume() 加载
   └── 恢复 customTitle, agentName, tag 等元数据

6. 列表展示
   ├── /resume 命令列出可用 session
   ├── parseSessionInfoFromLite() 从 head/tail 提取
   └── 优先级: customTitle > lastPrompt > summary > firstPrompt
```

## 10. 关键设计总结

| 设计决策 | 原因 |
|---------|------|
| 标题写入 JSONL 而非独立文件 | 避免文件散落，原子性更好 |
| custom-title 与 ai-title 分离 | 用户标题始终优先，AI 可自我覆盖 |
| 退出时 reAppend 元数据 | 防止标题滑出 64KB 尾部窗口 |
| 同时写入 custom-title 和 agent-name | 前者用于列表显示，后者用于 prompt bar |
| AI 标题不被 reAppend | 防止 resume 后 AI 旧标题覆盖用户新标题 |
| Haiku 模型生成标题 | 速度最快，延迟最低 |
| kebab-case 格式 | 简洁、CLI 友好、易读 |
| Bridge 同步 best-effort | 不阻塞本地操作，网络失败静默 |
