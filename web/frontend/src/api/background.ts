import { apiBase, requestJson } from './transport'

export interface BackgroundSettings {
  enabled: boolean
  overlay_opacity: number
  blur: number
  ui_opacity: number
  ui_blur: number
}

export interface BackgroundStatus {
  user: string
  available: boolean
  filename: string
  media_kind: 'image' | 'video' | ''
  mime_type: string
  size: number
  revision: string
  error: string
  settings: BackgroundSettings
}

export const defaultBackground: BackgroundSettings = {
  enabled: false, overlay_opacity: 0.65, blur: 0, ui_opacity: 0.94, ui_blur: 0,
}
export const backgroundImageMaxBytes = 100 * 1024 * 1024
export const backgroundVideoMaxBytes = 400 * 1024 * 1024

function normalizeStatus<T extends BackgroundStatus>(status: T): T {
  return { ...status, settings: { ...defaultBackground, ...status.settings } }
}

export function backgroundUrl(user: string, revision: string) {
  return `${apiBase}/api/users/${encodeURIComponent(user)}/background?v=${encodeURIComponent(revision)}`
}

export async function getBackground(user: string): Promise<BackgroundStatus> {
  return normalizeStatus(await requestJson<BackgroundStatus>(`/api/users/${encodeURIComponent(user)}/background/status`))
}

export async function uploadBackground(user: string, file: File): Promise<BackgroundStatus> {
  const body = new FormData()
  body.append('file', file)
  return normalizeStatus(await requestJson<BackgroundStatus>(`/api/users/${encodeURIComponent(user)}/background`, { method: 'POST', body }))
}

export async function deleteBackground(user: string): Promise<BackgroundStatus & { deleted: boolean }> {
  return normalizeStatus(await requestJson<BackgroundStatus & { deleted: boolean }>(`/api/users/${encodeURIComponent(user)}/background`, { method: 'DELETE' }))
}
