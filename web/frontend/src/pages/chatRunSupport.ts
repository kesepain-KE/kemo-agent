import { randomUUID } from '../randomId'
import type { ChatItem, InputAttachment, RunEvent } from '../types/api'
import type { PendingUploadedFile } from '../store/chatDrafts'

export function createSessionId() {
  return `web_${randomUUID()}`
}

export function eventId(prefix: string) {
  return `${prefix}_${randomUUID()}`
}

export const EMPTY_CHAT_ITEMS: ChatItem[] = []
export const HISTORY_PAGE_SIZE = 20
export type RunRetryNotice = {
  failedAttempt: number
  nextAttempt: number
  maxAttempts: number
}

export type RunErrorNotice = {
  id: string
  message: string
}

export function shouldShowLongTaskBubble(status: string) {
  return ['running', 'pausing', 'paused', 'cancelling'].includes(status)
}

export function removeSubmittedUploads(
  current: PendingUploadedFile[],
  submitted: PendingUploadedFile[],
) {
  if (!submitted.length) return current
  const submittedPaths = new Set(submitted.map((file) => file.path))
  return current.filter((file) => !submittedPaths.has(file.path))
}

export function pendingInputAttachment(file: PendingUploadedFile): InputAttachment {
  const mimeType = file.mimeType || 'application/octet-stream'
  const mediaKind = file.mediaKind
    || (mimeType.startsWith('image/') ? 'image'
      : mimeType.startsWith('audio/') ? 'audio'
        : mimeType.startsWith('video/') ? 'video'
          : 'file')
  return {
    asset_id: `pending:${file.path}`,
    name: file.name,
    media_kind: mediaKind,
    mime_type: mimeType,
    size: file.size,
    checksum_sha256: file.checksumSha256 || '',
    scope: 'file_upload',
    relative_path: file.path,
    available: true,
  }
}

export function isSuccessfulRunCompletion(event: RunEvent) {
  if (event.type !== 'done' || event.metadata?.committed === false) return false
  if (event.metadata?.long_task === true && event.metadata?.terminal === false) return false
  const status = String(event.metadata?.status || '').toLowerCase()
  return status === 'completed'
}

const NON_FAILURE_RUN_STATUSES = new Set([
  'cancelled',
  'cancelling',
  'paused',
  'pausing',
  'limited',
  'interrupted',
  'stopped',
  'aborted',
])

export function isFailedRunCompletion(event: RunEvent) {
  const metadata = event.metadata || {}
  if (metadata.retryable === true && metadata.committed === false) return false
  if (metadata.cancelled === true || event.error?.cancelled === true) return false
  if (metadata.long_task === true && metadata.terminal === false) return false
  const nestedState = metadata.long_task_state
  const nestedStatus = nestedState && typeof nestedState === 'object'
    ? String((nestedState as Record<string, unknown>).status || '').toLowerCase()
    : ''
  const status = String(metadata.status || nestedStatus || '').toLowerCase()
  if (NON_FAILURE_RUN_STATUSES.has(status)) return false
  if (event.type === 'done') return status === 'failed' || status === 'error'
  if (event.type !== 'error') return false
  if (status === 'failed' || status === 'error') return true
  // The runtime can emit a terminal error without a status field when an
  // exception escapes the provider adapter.  It is still a failed run unless
  // explicitly marked as a non-terminal long-task update.
  return Boolean(event.error) && metadata.terminal !== false
}
