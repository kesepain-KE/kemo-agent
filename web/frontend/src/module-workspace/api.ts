import { requestJson } from '../api/transport'
import type { ModulePanelResponse, ModulePanelValue } from '../types/api'
import type { ImportPreview, ModuleKind, Scope, WebInstance, WorkspaceInventory, WorkspaceItem } from './types'

const segment = encodeURIComponent
export const inventoryPath = (user: string, kind: ModuleKind) => `/api/users/${segment(user)}/module-workspaces/${kind}`
export const modulePath = (user: string, item: WorkspaceItem) => `${inventoryPath(user, item.kind)}/${item.scope}/${segment(item.module)}`
const json = (method: string, body: unknown): RequestInit => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
export const inventory = (user: string, kind: ModuleKind, signal?: AbortSignal) => requestJson<WorkspaceInventory>(inventoryPath(user, kind), { signal })
export const panel = (user: string, item: WorkspaceItem, signal?: AbortSignal) => requestJson<ModulePanelResponse>(`${modulePath(user, item)}/panel`, { signal })
export const savePanel = (user: string, item: WorkspaceItem, values: Record<string, ModulePanelValue>, clear_secrets: string[], config_revision?: string) =>
  requestJson<ModulePanelResponse>(`${modulePath(user, item)}/panel`, json('PUT', { values, clear_secrets, config_revision }))
export const documentPage = (user: string, item: WorkspaceItem, page: string, signal?: AbortSignal) =>
  requestJson<{ content: string }>(`${modulePath(user, item)}/document/${segment(page)}`, { signal })
export const permissions = (user: string, item: WorkspaceItem, granted: string[]) =>
  requestJson(`${modulePath(user, item)}/permissions`, json('PUT', { permissions: granted, authorization_revision: item.authorization_revision }))
export const invokeAction = (user: string, item: WorkspaceItem, action: string, params: unknown) =>
  requestJson(`${modulePath(user, item)}/action`, json('POST', { action, params }))
export const openInstance = (user: string, item: WorkspaceItem, page: string) =>
  requestJson<WebInstance>(`${modulePath(user, item)}/instances`, json('POST', { page }))
export const closeInstance = (nonce: string) => requestJson(`/api/module-ui/instances/${segment(nonce)}`, { method: 'DELETE', keepalive: true })
export const rpc = async (nonce: string, method: string, params: unknown, signal?: AbortSignal) => {
  const response = await requestJson<{ result: unknown }>(`/api/module-ui/instances/${segment(nonce)}/rpc`, { ...json('POST', { method, params }), signal })
  return response.result
}
export const previewImport = (user: string, kind: ModuleKind, scope: Scope, file: File) => {
  const form = new FormData()
  form.append('file', file)
  return requestJson<ImportPreview>(`${inventoryPath(user, kind)}/import/preview?scope=${scope}`, { method: 'POST', body: form })
}
export const installImport = (user: string, preview: ImportPreview) =>
  requestJson(`/api/users/${segment(user)}/module-workspaces/install/${segment(preview.import_id)}`, { method: 'POST' })
