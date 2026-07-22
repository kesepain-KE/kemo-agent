import { z } from 'zod'
import type {
  AgentDeleteResponse,
  AgentsResponse,
  ApiErrorPayload,
  AuthStatusResponse,
  AvatarUploadResponse,
  ConfigFullResponse,
  ExpandsResponse,
  ExpandScope,
  FileDeleteResponse,
  FileMutationResponse,
  HistoryResponse,
  ImportantMemoryResponse,
  KnowledgeDocumentResponse,
  KnowledgeResponse,
  MemoryItemResponse,
  MemorySummaryResponse,
  MessageCheckResponse,
  MessageDeleteResponse,
  MessageStatusResponse,
  OverviewResponse,
  ActiveSessionResponse,
  PreferencesResponse,
  PromptDiagnosticsResponse,
  RuntimeStatusResponse,
  RunEvent,
  SenseResponse,
  SessionDeleteAllResponse,
  SessionDeleteResponse,
  SessionCompressResponse,
  SessionCloseResponse,
  SessionMemoryExtractionResponse,
  SessionUndoLastRoundResponse,
  SessionRenameResponse,
  SessionsResponse,
  SettingsResponse,
  SkillsResponse,
  SkillCategory,
  SkillDocumentResponse,
  SoulResponse,
  TasksResponse,
  TmpFilesResponse,
  TmpFilesDeleteResponse,
  UserFilesResponse,
  UsersResponse,
} from '../types/api'

const apiBase = (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, '') ?? ''

export const AUTH_REQUIRED_EVENT = 'kemo-auth-required'
export const AVATAR_UPDATED_EVENT = 'kemo-avatar-updated'

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'request_failed',
  ) {
    super(message)
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${apiBase}${path}`, {
    credentials: 'same-origin',
    ...init,
  })
  if (!response.ok) {
    let payload: ApiErrorPayload | undefined
    try {
      payload = (await response.json()) as ApiErrorPayload
    } catch {
      payload = undefined
    }
    const error = new ApiError(
      payload?.error?.message || `请求失败（${response.status}）`,
      response.status,
      payload?.error?.code,
    )
    if (
      response.status === 401
      && error.code === 'authentication_required'
      && typeof window !== 'undefined'
    ) {
      window.dispatchEvent(new Event(AUTH_REQUIRED_EVENT))
    }
    throw error
  }
  return (await response.json()) as T
}

const runEventSchema = z
  .object({
    type: z.enum([
      'text_delta',
      'reasoning_delta',
      'tool_call_start',
      'tool_call_result',
      'guidance_applied',
      'usage',
      'error',
      'done',
    ]),
    content: z.string().optional(),
    tool_call_id: z.string().optional(),
    tool_name: z.string().optional(),
    arguments: z.record(z.string(), z.unknown()).optional(),
    result: z.unknown().optional(),
    usage: z.record(z.string(), z.unknown()).optional(),
    error: z.record(z.string(), z.unknown()).optional(),
    metadata: z.record(z.string(), z.unknown()).optional(),
  })
  .passthrough()

export async function getHealth(): Promise<{ status: string; service: string; version: number }> {
  return requestJson('/api/health')
}

export async function getUsers(): Promise<UsersResponse> {
  return requestJson('/api/users')
}

export async function getSessions(user: string, query = ''): Promise<SessionsResponse> {
  const suffix = query.trim() ? `?query=${encodeURIComponent(query.trim())}` : ''
  return requestJson(`/api/users/${encodeURIComponent(user)}/sessions${suffix}`)
}

export async function restartSystem(port: number): Promise<{ ok: boolean; port: number; helper_pid?: number; already_requested?: boolean }> {
  return requestJson('/api/system/restart', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ port }),
  })
}

export async function getActiveSession(user: string, clientId = ''): Promise<ActiveSessionResponse> {
  const suffix = clientId ? `?client_id=${encodeURIComponent(clientId)}` : ''
  return requestJson(`/api/users/${encodeURIComponent(user)}/sessions/active${suffix}`)
}

export async function createSession(user: string, clientId = ''): Promise<ActiveSessionResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/sessions`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ client_id: clientId }),
  })
}

export async function closeSession(
  user: string,
  sessionId: string,
  clientId = '',
): Promise<SessionCloseResponse> {
  const suffix = clientId ? `?client_id=${encodeURIComponent(clientId)}` : ''
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/close${suffix}`,
    { method: 'POST' },
  )
}

export async function touchSessionLease(user: string, sessionId: string, clientId: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/lease`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId }),
    },
  )
}

export async function releaseSessionLease(
  user: string,
  sessionId: string,
  clientId: string,
  keepalive = false,
): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/lease/release`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ client_id: clientId }),
      keepalive,
    },
  )
}

export async function getHistory(user: string, sessionId: string): Promise<HistoryResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/history`,
  )
}

export async function renameSession(
  user: string,
  sessionId: string,
  title: string,
): Promise<SessionRenameResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    },
  )
}

export async function deleteSession(
  user: string,
  sessionId: string,
  clientId = '',
): Promise<SessionDeleteResponse> {
  const suffix = clientId ? `?client_id=${encodeURIComponent(clientId)}` : ''
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}${suffix}`,
    { method: 'DELETE' },
  )
}

export async function compressSession(
  user: string,
  sessionId: string,
): Promise<SessionCompressResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/compress`,
    { method: 'POST' },
  )
}

export async function extractSessionMemory(
  user: string,
  sessionId: string,
): Promise<SessionMemoryExtractionResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/extract-memory`,
    { method: 'POST' },
  )
}

export async function undoLastRound(
  user: string,
  sessionId: string,
  expectedRound: number,
  prompt: string,
): Promise<SessionUndoLastRoundResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions/${encodeURIComponent(sessionId)}/undo-last-round`,
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ expected_round: expectedRound, prompt }),
    },
  )
}

export async function deleteAllSessions(user: string): Promise<SessionDeleteAllResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/sessions`,
    { method: 'DELETE' },
  )
}

export async function getAuthStatus(): Promise<AuthStatusResponse> {
  return requestJson('/api/auth/status')
}

export async function bootstrapAuth(token: string): Promise<AuthStatusResponse> {
  return requestJson(`/api/auth/status?token=${encodeURIComponent(token)}`)
}

export async function loginAuth(
  username: string,
  password: string,
): Promise<AuthStatusResponse> {
  return requestJson('/api/auth/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username, password }),
  })
}

export async function logoutAuth(): Promise<{ authenticated: boolean }> {
  return requestJson('/api/auth/logout', { method: 'POST' })
}

export async function getOverview(user: string, sessionId = ''): Promise<OverviewResponse> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return requestJson(`/api/users/${encodeURIComponent(user)}/overview${query}`)
}

export async function getRuntimeStatus(user: string, sessionId = ''): Promise<RuntimeStatusResponse> {
  const query = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : ''
  return requestJson(`/api/users/${encodeURIComponent(user)}/runtime/status${query}`)
}

export async function getTasks(user: string): Promise<TasksResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks`)
}

export async function createPlan(user: string, plan: Record<string, unknown>): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/plans`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan),
  })
}

export async function updatePlan(user: string, planId: string, plan: Record<string, unknown>): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/plans/${encodeURIComponent(planId)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(plan),
  })
}

export async function commandPlan(
  user: string,
  planId: string,
  action: 'pause' | 'cancel',
): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/plans/${encodeURIComponent(planId)}/actions/${action}`, {
    method: 'POST',
  })
}

export async function deletePlan(user: string, planId: string): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/plans/${encodeURIComponent(planId)}`, { method: 'DELETE' })
}

export async function createCron(user: string, task: Record<string, unknown>): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/crons`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(task),
  })
}

export async function updateCron(user: string, taskId: string, task: Record<string, unknown>): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/crons/${encodeURIComponent(taskId)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(task),
  })
}

export async function deleteCron(user: string, taskId: string): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/tasks/crons/${encodeURIComponent(taskId)}`, { method: 'DELETE' })
}

export async function getKnowledge(user: string): Promise<KnowledgeResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/knowledge`)
}

export async function getKnowledgeDocument(user: string, scope: string, path: string): Promise<KnowledgeDocumentResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/knowledge/${encodeURIComponent(scope)}/document?path=${encodeURIComponent(path)}`)
}

export async function putKnowledgeDocument(user: string, scope: string, path: string, content: string): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/knowledge/${encodeURIComponent(scope)}/document?path=${encodeURIComponent(path)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
  })
}

export async function deleteKnowledgeDocument(user: string, scope: string, path: string): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/knowledge/${encodeURIComponent(scope)}/document?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
}

export async function moveKnowledgeDocument(user: string, scope: string, path: string, newPath: string): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/knowledge/${encodeURIComponent(scope)}/document?path=${encodeURIComponent(path)}&new_path=${encodeURIComponent(newPath)}`, { method: 'PATCH' })
}

export async function getSkills(user: string): Promise<SkillsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/skills`)
}

export async function getSkillDocument(user: string, category: SkillCategory, name: string): Promise<SkillDocumentResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/skills/${category}/document?name=${encodeURIComponent(name)}`)
}

export async function putSkillDocument(user: string, category: SkillCategory, name: string, content: string): Promise<SkillDocumentResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/skills/${category}/document?name=${encodeURIComponent(name)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
  })
}

export async function deleteSkill(user: string, category: SkillCategory, name: string): Promise<{ user: string; category: SkillCategory; name: string; path: string; deleted: boolean }> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/skills/${category}?name=${encodeURIComponent(name)}`, { method: 'DELETE' })
}

export async function setSkillEnabled(user: string, category: SkillCategory, name: string, enabled: boolean): Promise<{ enabled: boolean }> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/skills/${category}/enabled?name=${encodeURIComponent(name)}`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }),
  })
}

export function getSkillDownloadUrl(user: string, category: SkillCategory, name: string): string {
  return `${apiBase}/api/users/${encodeURIComponent(user)}/skills/${category}/download?name=${encodeURIComponent(name)}`
}

export async function getSense(user: string): Promise<SenseResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/sense`)
}

export async function refreshSenseModule(user: string, moduleName: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/sense/${encodeURIComponent(moduleName)}/refresh`,
    { method: 'POST' },
  )
}

export async function setSenseModuleEnabled(
  user: string,
  moduleName: string,
  enabled: boolean,
): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/sense/${encodeURIComponent(moduleName)}/enabled`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    },
  )
}

export async function deleteSenseModule(user: string, moduleName: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/sense/${encodeURIComponent(moduleName)}`,
    { method: 'DELETE' },
  )
}

export async function getSettings(user: string): Promise<SettingsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/settings`)
}

export async function getUserConfig(user: string): Promise<ConfigFullResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/config/full`)
}

export async function patchUserConfig(user: string, changes: Record<string, unknown>): Promise<ConfigFullResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/config`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ changes }),
  })
}

export async function getGlobalConfig(): Promise<{ scope: string; config: Record<string, unknown>; redacted_paths: string[] }> {
  return requestJson('/api/global-config')
}

export async function patchGlobalConfig(changes: Record<string, unknown>): Promise<Record<string, unknown>> {
  return requestJson('/api/global-config', {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ changes }),
  })
}

export async function getPreferences(user: string): Promise<PreferencesResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/preferences`)
}

export async function patchPreferences(user: string, changes: Partial<PreferencesResponse['appearance']>): Promise<PreferencesResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/preferences`, {
    method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
  })
}

export async function getPromptDiagnostics(user: string): Promise<PromptDiagnosticsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/prompt/sections`)
}

export async function getMemorySummary(user: string): Promise<MemorySummaryResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/summary`)
}

export async function getMemoryItem(user: string, tier: string, filename: string): Promise<MemoryItemResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/item?tier=${encodeURIComponent(tier)}&filename=${encodeURIComponent(filename)}`)
}

export async function putMemory(user: string, filename: string, content: string, tier?: string): Promise<MemoryItemResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/item?filename=${encodeURIComponent(filename)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content, tier }),
  })
}

export async function deleteMemory(user: string, tier: string, filename: string): Promise<Record<string, unknown>> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/item?tier=${encodeURIComponent(tier)}&filename=${encodeURIComponent(filename)}`, { method: 'DELETE' })
}

export async function getImportantMemory(user: string): Promise<ImportantMemoryResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/important`)
}

export async function updateImportantMemory(user: string, content: string): Promise<ImportantMemoryResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/memory/important`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
  })
}

export async function getUserFiles(
  user: string,
  scope: 'file_upload' | 'download',
): Promise<UserFilesResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}`)
}

export function getUserFileDownloadUrl(
  user: string,
  scope: 'file_upload' | 'download',
  path: string,
): string {
  return `${apiBase}/api/users/${encodeURIComponent(user)}/files/${scope}/download?path=${encodeURIComponent(path)}`
}

export async function deleteUserFile(
  user: string,
  scope: 'file_upload' | 'download',
  path: string,
): Promise<FileDeleteResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/files/${scope}?path=${encodeURIComponent(path)}`,
    { method: 'DELETE' },
  )
}

export async function deleteUserFiles(
  user: string,
  scope: 'file_upload' | 'download',
  paths: string[],
): Promise<TmpFilesDeleteResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/delete-many`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  })
}

export async function deleteAllUserFiles(
  user: string,
  scope: 'file_upload' | 'download',
): Promise<TmpFilesDeleteResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/all`, { method: 'DELETE' })
}

export async function uploadUserFile(user: string, scope: 'file_upload' | 'download', path: string, file: File): Promise<FileMutationResponse> {
  const body = new FormData()
  body.append('file', file)
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/upload?path=${encodeURIComponent(path)}`, { method: 'POST', body })
}

export async function writeUserFileText(user: string, scope: 'file_upload' | 'download', path: string, content: string): Promise<FileMutationResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/text?path=${encodeURIComponent(path)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
  })
}

export async function getUserFileText(user: string, scope: 'file_upload' | 'download', path: string): Promise<{ path: string; content: string; size: number }> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/text?path=${encodeURIComponent(path)}`)
}

export async function moveUserFile(user: string, scope: 'file_upload' | 'download', path: string, newPath: string): Promise<FileMutationResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/move?path=${encodeURIComponent(path)}&new_path=${encodeURIComponent(newPath)}`, { method: 'PATCH' })
}

export async function createUserDirectory(user: string, scope: 'file_upload' | 'download', path: string): Promise<FileMutationResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/files/${scope}/directory?path=${encodeURIComponent(path)}`, { method: 'POST' })
}

export async function getTmpFiles(): Promise<TmpFilesResponse> {
  return requestJson('/api/tmp')
}

export async function deleteTmpFile(path: string): Promise<FileDeleteResponse> {
  return requestJson(`/api/tmp?path=${encodeURIComponent(path)}`, { method: 'DELETE' })
}

export async function deleteTmpFiles(paths: string[]): Promise<TmpFilesDeleteResponse> {
  return requestJson('/api/tmp/delete-many', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ paths }),
  })
}

export async function deleteAllTmpFiles(): Promise<TmpFilesDeleteResponse> {
  return requestJson('/api/tmp/all', { method: 'DELETE' })
}

export async function uploadTmpFile(path: string, file: File): Promise<FileMutationResponse> {
  const body = new FormData()
  body.append('file', file)
  return requestJson(`/api/tmp/upload?path=${encodeURIComponent(path)}`, { method: 'POST', body })
}

export async function writeTmpText(path: string, content: string): Promise<FileMutationResponse> {
  return requestJson(`/api/tmp/text?path=${encodeURIComponent(path)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ content }),
  })
}

export async function getTmpText(path: string): Promise<{ path: string; content: string; size: number }> {
  return requestJson(`/api/tmp/text?path=${encodeURIComponent(path)}`)
}

export async function moveTmpFile(path: string, newPath: string): Promise<FileMutationResponse> {
  return requestJson(`/api/tmp/move?path=${encodeURIComponent(path)}&new_path=${encodeURIComponent(newPath)}`, { method: 'PATCH' })
}

export async function createTmpDirectory(path: string): Promise<FileMutationResponse> {
  return requestJson(`/api/tmp/directory?path=${encodeURIComponent(path)}`, { method: 'POST' })
}

export function getUserAvatarUrl(user: string, revision?: string | number): string {
  const suffix = revision === undefined ? '' : `?v=${encodeURIComponent(String(revision))}`
  return `${apiBase}/api/users/${encodeURIComponent(user)}/avatar${suffix}`
}

export async function uploadUserAvatar(user: string, file: File): Promise<AvatarUploadResponse> {
  const body = new FormData()
  body.append('file', file)
  const result = await requestJson<AvatarUploadResponse>(
    `/api/users/${encodeURIComponent(user)}/avatar`,
    { method: 'POST', body },
  )
  if (typeof window !== 'undefined') {
    window.dispatchEvent(new CustomEvent(AVATAR_UPDATED_EVENT, { detail: { user } }))
  }
  return result
}

export async function getAgents(user: string): Promise<AgentsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/agents`)
}

export async function deleteUserAgent(user: string, agent: string): Promise<AgentDeleteResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/agents/${encodeURIComponent(agent)}`,
    { method: 'DELETE' },
  )
}

export async function getMessageStatus(user: string): Promise<MessageStatusResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/message/status`)
}

export async function checkMessageModule(user: string, moduleName: string): Promise<MessageCheckResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/message/modules/${encodeURIComponent(moduleName)}/check`,
    { method: 'POST' },
  )
}

export async function deleteMessageModule(user: string, moduleName: string): Promise<MessageDeleteResponse> {
  return requestJson(
    `/api/users/${encodeURIComponent(user)}/message/modules/${encodeURIComponent(moduleName)}`,
    { method: 'DELETE' },
  )
}

export async function getUserSoul(user: string): Promise<SoulResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/soul`)
}

export async function updateUserSoul(user: string, content: string): Promise<SoulResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/soul`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export async function getGlobalSoul(): Promise<SoulResponse> {
  return requestJson('/api/global-soul')
}

export async function updateGlobalSoul(content: string): Promise<SoulResponse> {
  return requestJson('/api/global-soul', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ content }),
  })
}

export function getLogoUrl(): string {
  return `${apiBase}/api/logo`
}

export async function getExpands(user: string): Promise<ExpandsResponse> {
  return requestJson(`/api/users/${encodeURIComponent(user)}/expand`)
}

export async function refreshExpandModule(user: string, scope: ExpandScope, moduleName: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/expand/${scope}/${encodeURIComponent(moduleName)}/refresh`,
    { method: 'POST' },
  )
}

export async function setExpandModuleEnabled(
  user: string,
  scope: ExpandScope,
  moduleName: string,
  enabled: boolean,
): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/expand/${scope}/${encodeURIComponent(moduleName)}/enabled`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled }),
    },
  )
}

export async function deleteExpandModule(user: string, moduleName: string): Promise<void> {
  await requestJson(
    `/api/users/${encodeURIComponent(user)}/expand/user/${encodeURIComponent(moduleName)}`,
    { method: 'DELETE' },
  )
}

export interface StreamChatOptions {
  user: string
  sessionId: string
  clientId?: string
  prompt: string
  content?: Array<Record<string, unknown>>
  runId: string
  planId?: string
  signal?: AbortSignal
  onEvent: (event: RunEvent) => void
}

export async function submitGuidance(
  user: string,
  runId: string,
  guidance: string,
): Promise<{ run_id: string; status: string; queued: number }> {
  return requestJson(`/api/runs/${encodeURIComponent(runId)}/guidance`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ user, guidance }),
  })
}

export interface SseFrame {
  event?: string
  data: string
}

export function parseSseFrames(buffer: string): { frames: SseFrame[]; rest: string } {
  const normalized = buffer.replace(/\r\n/g, '\n')
  const chunks = normalized.split('\n\n')
  const rest = chunks.pop() ?? ''
  const frames = chunks.flatMap((chunk) => {
    let event: string | undefined
    const data: string[] = []
    for (const line of chunk.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim()
      else if (line.startsWith('data:')) data.push(line.slice(5).trimStart())
    }
    return data.length ? [{ event, data: data.join('\n') }] : []
  })
  return { frames, rest }
}

export async function streamChat(options: StreamChatOptions): Promise<void> {
  const response = await fetch(`${apiBase}/api/chat`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'text/event-stream' },
    body: JSON.stringify({
      user: options.user,
      session_id: options.sessionId,
      prompt: options.prompt,
      content: options.content ?? [],
      run_id: options.runId,
      plan_id: options.planId ?? '',
      client_id: options.clientId ?? '',
    }),
    signal: options.signal,
  })
  if (!response.ok) {
    const payload = (await response.json().catch(() => undefined)) as ApiErrorPayload | undefined
    throw new ApiError(
      payload?.error?.message || `聊天请求失败（${response.status}）`,
      response.status,
      payload?.error?.code,
    )
  }
  if (!response.body) throw new ApiError('浏览器没有提供流式响应正文', 0)

  const reader = response.body.getReader()
  const decoder = new TextDecoder()
  let buffer = ''
  let terminal = false
  while (true) {
    const { done, value } = await reader.read()
    buffer += decoder.decode(value, { stream: !done })
    const parsed = parseSseFrames(buffer)
    buffer = parsed.rest
    for (const frame of parsed.frames) {
      const event = runEventSchema.parse(JSON.parse(frame.data)) as RunEvent
      if (frame.event && frame.event !== event.type) {
        throw new ApiError('SSE 事件名称与数据类型不一致', 0, 'invalid_sse')
      }
      options.onEvent(event)
      if (event.type === 'error' || event.type === 'done') terminal = true
    }
    if (done) break
  }
  if (buffer.trim()) {
    const parsed = parseSseFrames(`${buffer}\n\n`)
    for (const frame of parsed.frames) {
      const event = runEventSchema.parse(JSON.parse(frame.data)) as RunEvent
      options.onEvent(event)
      if (event.type === 'error' || event.type === 'done') terminal = true
    }
  }
  if (!terminal) throw new ApiError('聊天流在终态事件前结束', 0, 'missing_terminal')
}
