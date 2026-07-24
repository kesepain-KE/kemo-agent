---
type: component
project: kemo-agent
domain: web
module: frontend-client
layer: L2
scope: project
status: active
summary: web/frontend/src/api/client.ts — API 客户端（新增 compressSession 和 getRuntimeStatus）
source: "web/frontend-client.md"
updated: 2026-07-22
verified: true
tags: [kemo-agent, web, frontend, API, SSE, compress, runtime_status, undo_last_round, memory_ref]
---
# web/frontend/src/api/client.ts — API 客户端

`E:\code\kemo-agent\web\frontend\src\api\client.ts`

## 全局事件

| 事件名 | 说明 |
|--------|--------|
| `AUTH_REQUIRED_EVENT` | 认证失效时广播 |
| `AVATAR_UPDATED_EVENT` | 头像上传成功后广播 |

## REST 函数总览

### 基础与 Observer

getHealth / getUsers / getSessions(user, query) / getHistory / getOverview / getTasks / getKnowledge / getSkills / getSense / getSettings / getPromptDiagnostics / getMemorySummary

### 会话管理

renameSession / deleteSession / deleteAllSessions

### 文件空间

getUserFiles / deleteUserFile / uploadUserFile / writeUserFileText / getUserFileText / moveUserFile / createUserDirectory
getTmpFiles / deleteTmpFile / uploadTmpFile / writeTmpText / readTmpText / makeTmpDirectory / moveTmpFile

### 新增 tmp 批量操作

**deleteTmpFiles** / **deleteAllTmpFiles**

### 头像

getUserAvatarUrl / uploadUserAvatar

### 人格编辑

getUserSoul / updateUserSoul / getGlobalSoul / updateGlobalSoul

### 运行模块与品牌

getAgents / getMessageStatus / getExpands / getLogoUrl

### 新增会话压缩和运行状态（2026-07-21）

| 函数 | 说明 |
|------|------|
| **compressSession** | 手动触发上下文压缩 |
| **getRuntimeStatus** | 获取完整运行状态（Prompt 构成、Token 统计、组件健康、记忆、任务、Cron、消息路由） |

### 新增撤销上一轮和记忆 CRUD 层级化（2026-07-22）

| 函数 | 说明 |
|------|------|
| **undoLastRound** | POST `/api/users/{user}/sessions/{id}/undo-last-round`，传 expected_round + prompt |
| **getMemoryItem** | 新增 `tier` 参数，GET `/api/users/{user}/memory/item?tier=...&filename=...` |
| **deleteMemory** | 新增 `tier` 参数，DELETE `/api/users/{user}/memory/item?tier=...&filename=...` |

### 任务计划与 Cron CRUD

createPlan / updatePlan / deletePlan / createCron / updateCron / deleteCron

### 知识正文 CRUD

getKnowledgeDocument / putKnowledgeDocument / deleteKnowledgeDocument / moveKnowledgeDocument

### 记忆 CRUD

getMemoryItem / putMemory / deleteMemory / getImportantMemory / updateImportantMemory / deleteImportantMemory

### 配置与偏好

patchUserConfig / getGlobalConfig / patchGlobalConfig / getPreferences / patchPreferences

## SSE 函数

streamChat / parseSseFrames / submitGuidance

## 相关笔记

- [[web-总览]]
- [[web-app]]
- [[frontend-modules]]