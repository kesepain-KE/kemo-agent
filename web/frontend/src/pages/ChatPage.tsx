import { Fragment, Suspense, lazy, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BrainCircuit,
  Check,
  ChevronDown,
  ListChecks,
  Copy,
  Download,
  File as FileIcon,
  FileX2,
  Image as ImageIcon,
  Music2,
  Pencil,
  RotateCcw,
  Save,
  Shapes,
  TimerReset,
  Trash2,
  UserRound,
  Video,
  Workflow,
  X,
  Zap,
} from 'lucide-react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { ApiError, cancelRun, cancelSessionLongTask, closeSession, commandPlan, compressSession, deleteSession, getExpands, getHistory, getKnowledge, getSense, getSessionLongTask, getSkills, getTasks, getUserArtifactUrl, getUserAttachmentThumbnailUrl, getUserFileDownloadUrl, getUserFilePreviewUrl, retryPlanStep, setSessionLongTask, streamChat, submitGuidance, undoLastRound, uploadUserFile } from '../api/client'
import { AgentComposer } from '../components/AgentComposer'
import { buildCapabilityReferenceItems, capabilityReferenceLine, capabilityReferenceMarker } from '../components/capabilityReferences'
import { CONVERSATION_COMMAND_EVENT, chatRunKey, type ChatItemsUpdater, type ConversationCommandAction, type PendingNextTurnMessage, type ShellOutletContext } from '../components/AppShell'
import { PlainTextMessage } from '../components/Chat/PlainTextMessage'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from '../components/CapabilityReferenceDrawer'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { LongTaskBubble } from '../components/LongTaskBubble'
import { formatBytes, formatDateTime, statusLabel } from '../components/ModuleUi'
import { RecentActivityCard, type ScheduledTaskItem, type SenseDataItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import { UserMessageNavigator, type UserMessageMarker } from '../components/UserMessageNavigator'
import type { ChatItem, CronTaskSummary, HistoryResponse, InputAttachment, KnowledgeDocumentSummary, LongTaskResponse, LongTaskState, MediaArtifact, PlanSummary, RunEvent, SenseSourceSummary } from '../types/api'
import { copyText } from '../utils/clipboard'
import { playUserCompletionSound, playUserFailureSound } from '../utils/completionSound'
import { randomUUID } from '../randomId'
import { chatDraftKey, EMPTY_CHAT_DRAFT, useChatDraftStore, type PendingUploadedFile } from '../store/chatDrafts'

export type { PendingUploadedFile } from '../store/chatDrafts'

const MarkdownMessage = lazy(async () => ({
  default: (await import('../components/Chat/MarkdownMessage')).MarkdownMessage,
}))

function createSessionId() {
  return `web_${randomUUID()}`
}

function eventId(prefix: string) {
  return `${prefix}_${randomUUID()}`
}

const EMPTY_CHAT_ITEMS: ChatItem[] = []
const PLAN_EXECUTION_PROMPT_PREFIX = '【任务计划连续执行】'
const HISTORY_PAGE_SIZE = 20
type RunRetryNotice = {
  failedAttempt: number
  nextAttempt: number
  maxAttempts: number
}

type RunErrorNotice = {
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

function pendingInputAttachment(file: PendingUploadedFile): InputAttachment {
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

function UserMessageAvatar({ avatarUrl }: { avatarUrl?: string }) {
  const [avatarFailed, setAvatarFailed] = useState(false)

  useEffect(() => setAvatarFailed(false), [avatarUrl])

  return (
    <div className="msg-avatar user-message-avatar">
      {avatarUrl && !avatarFailed
        ? <img src={avatarUrl} alt="" onError={() => setAvatarFailed(true)} />
        : <UserRound size={17} />}
    </div>
  )
}

export function mediaArtifactUrl(user: string, artifact: MediaArtifact) {
  return /^[a-f0-9]{64}$/i.test(artifact.checksum_sha256) && artifact.size > 0
    ? getUserArtifactUrl(user, artifact.checksum_sha256.toLowerCase(), artifact.path, artifact.size)
    : getUserFileDownloadUrl(user, 'download', artifact.path)
}

function MediaArtifactCard({ user, artifact }: { user: string; artifact: MediaArtifact }) {
  const url = mediaArtifactUrl(user, artifact)
  return (
    <article className={`media-artifact media-artifact-${artifact.type}`}>
      {artifact.type === 'image' ? <img src={url} alt={artifact.name} loading="lazy" /> : null}
      {artifact.type === 'audio' ? <audio src={url} controls preload="metadata" /> : null}
      {artifact.type === 'video' ? <video src={url} controls preload="metadata" /> : null}
      <div className="media-artifact-meta">
        <span><strong>{artifact.name}</strong><small>{artifact.mime_type} · {formatBytes(artifact.size)}</small></span>
        <a href={url} download={artifact.name}><Download size={14} />下载</a>
      </div>
    </article>
  )
}

function UserAttachmentCard({ user, attachment }: { user: string; attachment: InputAttachment }) {
  const [unavailable, setUnavailable] = useState(!attachment.available)
  const [thumbnailFailed, setThumbnailFailed] = useState(false)
  const downloadable = attachment.scope === 'file_upload' && Boolean(attachment.relative_path) && !unavailable
  const url = downloadable
    ? getUserFileDownloadUrl(user, 'file_upload', attachment.relative_path)
    : ''
  const image = attachment.media_kind === 'image'
  const thumbnailUrl = image && attachment.checksum_sha256
    ? getUserAttachmentThumbnailUrl(user, attachment.checksum_sha256, attachment.relative_path)
    : ''

  useEffect(() => {
    setUnavailable(!attachment.available)
    setThumbnailFailed(false)
  }, [attachment.asset_id, attachment.available, attachment.relative_path, attachment.checksum_sha256])

  const download = async () => {
    if (!url) return
    try {
      const response = await fetch(url, { credentials: 'same-origin' })
      if (!response.ok) {
        setUnavailable(true)
        return
      }
      const blobUrl = URL.createObjectURL(await response.blob())
      const anchor = document.createElement('a')
      anchor.href = blobUrl
      anchor.download = attachment.name
      anchor.click()
      window.setTimeout(() => URL.revokeObjectURL(blobUrl), 0)
    } catch {
      setUnavailable(true)
    }
  }

  const typeIcon = attachment.media_kind === 'audio'
    ? <Music2 size={20} />
    : attachment.media_kind === 'video'
      ? <Video size={20} />
      : image
        ? <ImageIcon size={20} />
        : <FileIcon size={20} />
  return (
    <article className={`user-attachment-card ${attachment.media_kind}${unavailable ? ' unavailable' : ''}`} aria-label={`${unavailable ? '已清理附件' : '附件'}：${attachment.name}`}>
      {thumbnailUrl && !thumbnailFailed ? (
        <span className="user-attachment-preview">
          <img src={thumbnailUrl} alt={attachment.name} loading="lazy" onError={() => setThumbnailFailed(true)} />
        </span>
      ) : (
        <span role="img" className={`user-attachment-icon ${attachment.media_kind}`} aria-label={`${attachment.media_kind === 'audio' ? '音频' : attachment.media_kind === 'video' ? '视频' : image ? '图片' : '文件'}缩略图`}>
          {unavailable && !image ? <FileX2 size={20} /> : typeIcon}
        </span>
      )}
      <span className="user-attachment-copy">
        <strong title={attachment.name}>{attachment.name}</strong>
        <small>{unavailable ? `源文件已清理 · ${attachment.mime_type}` : `${attachment.mime_type} · ${formatBytes(attachment.size)}`}</small>
      </span>
      {downloadable ? <button type="button" onClick={() => { void download() }} aria-label={`下载附件 ${attachment.name}`}><Download size={14} />下载</button> : null}
    </article>
  )
}

function PendingAttachmentCard({
  user,
  file,
  onRemove,
}: {
  user: string
  file: PendingUploadedFile
  onRemove: () => void
}) {
  const [previewFailed, setPreviewFailed] = useState(false)
  const image = file.mediaKind === 'image' || file.mimeType?.startsWith('image/')
  const mediaKind = file.mediaKind || (image ? 'image' : 'file')
  const previewUrl = image ? getUserFilePreviewUrl(user, 'file_upload', file.path) : ''
  const thumbnailUrl = image && file.checksumSha256
    ? getUserAttachmentThumbnailUrl(user, file.checksumSha256, file.path)
    : previewUrl
  const typeIcon = mediaKind === 'audio'
    ? <Music2 size={22} />
    : mediaKind === 'video'
      ? <Video size={22} />
      : image
        ? <ImageIcon size={22} />
        : <FileIcon size={22} />

  useEffect(() => setPreviewFailed(false), [file.path])

  return (
    <article className={`pending-attachment-card${image ? ' image' : ' file'}`} role="listitem" aria-label={`待发送附件：${file.name}`}>
      <span className="pending-attachment-status">已上传 {file.name}</span>
      {image && !previewFailed ? (
        <a className="pending-attachment-preview" href={previewUrl} target="_blank" rel="noreferrer" aria-label={`预览图片 ${file.name}`}>
          <img src={thumbnailUrl} alt={file.name} onError={() => setPreviewFailed(true)} />
        </a>
      ) : (
        <span role="img" className={`pending-attachment-file-icon ${mediaKind}`} aria-label={`${mediaKind === 'audio' ? '音频' : mediaKind === 'video' ? '视频' : image ? '图片' : '文件'}缩略图`}>
          {typeIcon}
        </span>
      )}
      <span className="pending-attachment-copy">
        <strong title={file.name}>{file.name}</strong>
        <small>{file.mimeType || '文件'} · {formatBytes(file.size)}</small>
      </span>
      <button type="button" className="pending-attachment-remove" onClick={onRemove} aria-label={`取消引用 ${file.name}`} title="取消本次引用">
        <X size={15} />
      </button>
    </article>
  )
}

function PendingAttachmentTray({
  user,
  files,
  onRemove,
}: {
  user: string
  files: PendingUploadedFile[]
  onRemove: (index: number) => void
}) {
  return (
    <div className="pending-attachment-tray" role="list" aria-label="待发送附件">
      {files.map((file, index) => (
        <PendingAttachmentCard
          key={`${file.path}:${index}`}
          user={user}
          file={file}
          onRemove={() => onRemove(index)}
        />
      ))}
    </div>
  )
}

export function isNearScrollBottom(
  metrics: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold = 96,
) {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}

export type ConversationBlock =
  | { id: string; kind: 'user'; item: Extract<ChatItem, { kind: 'message' }> }
  | { id: string; kind: 'assistant'; items: ChatItem[] }

export function groupConversationItems(items: ChatItem[]): ConversationBlock[] {
  const blocks: ConversationBlock[] = []
  let activeAssistant: Extract<ConversationBlock, { kind: 'assistant' }> | null = null
  let currentUserId = 'opening'
  let assistantSequence = 0

  const flushAssistant = () => {
    if (!activeAssistant?.items.length) return
    blocks.push(activeAssistant)
    activeAssistant = null
  }

  for (const item of items) {
    if (item.kind === 'long_task_boundary') {
      flushAssistant()
      currentUserId = item.id
      assistantSequence += 1
      activeAssistant = {
        id: `assistant_turn_${currentUserId}_${assistantSequence}`,
        kind: 'assistant',
        items: [item],
      }
      continue
    }
    if (item.kind === 'execution_marker') {
      flushAssistant()
      currentUserId = item.id
      continue
    }
    if (item.kind === 'message' && item.role === 'user') {
      flushAssistant()
      currentUserId = item.id
      blocks.push({ id: item.id, kind: 'user', item })
      continue
    }
    if (!activeAssistant) {
      assistantSequence += 1
      activeAssistant = {
        id: `assistant_turn_${currentUserId}_${assistantSequence}`,
        kind: 'assistant',
        items: [],
      }
    }
    activeAssistant.items.push(item)
  }
  flushAssistant()
  return blocks
}

export function buildUserMessageMarkers(items: ChatItem[], firstRound = 1): UserMessageMarker[] {
  const markers: UserMessageMarker[] = []
  let nextRound = Math.max(1, Math.floor(firstRound))
  for (const item of items) {
    if (item.kind === 'execution_marker') {
      const executionRound = /^history_execution_(\d+)$/.exec(item.id)?.[1]
      if (executionRound) nextRound = Math.max(nextRound, Number(executionRound) + 1)
      continue
    }
    if (item.kind === 'long_task_boundary') {
      const continuationRound = /^history_long_task_(\d+)$/.exec(item.id)?.[1]
      if (continuationRound) nextRound = Math.max(nextRound, Number(continuationRound) + 1)
      continue
    }
    if (item.kind !== 'message' || item.role !== 'user') continue
    const historicalRound = /^history_(\d+)_user$/.exec(item.id)?.[1]
    const round = historicalRound ? Number(historicalRound) : nextRound
    markers.push({
      id: item.id,
      content: item.content || item.attachments?.map((attachment) => `[附件] ${attachment.name}`).join('；') || '附件消息',
      round,
    })
    nextRound = Math.max(nextRound, round + 1)
  }
  return markers
}

function currentRoundStartIndex(items: ChatItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (isConversationBoundary(items[index])) return index + 1
  }
  return 0
}

export function resetCurrentRoundItemsForRetry(items: ChatItem[]) {
  return items.slice(0, currentRoundStartIndex(items))
}

function isProvisionalRunError(event: RunEvent) {
  return event.type === 'error'
    && event.metadata?.retryable === true
    && event.metadata?.committed === false
}

export function isRetryAttemptProgress(event: RunEvent) {
  return [
    'text_delta',
    'reasoning_delta',
    'tool_call_start',
    'tool_call_result',
    'media_output',
    'guidance_applied',
    'usage',
  ].includes(event.type)
}

function findLastCurrentRoundItemIndex(
  items: ChatItem[],
  predicate: (candidate: ChatItem) => boolean,
) {
  const roundStart = currentRoundStartIndex(items)
  for (let index = items.length - 1; index >= roundStart; index -= 1) {
    if (predicate(items[index])) return index
  }
  return -1
}

export function finalizeCurrentRoundItems(
  items: ChatItem[],
  toolError: Record<string, unknown>,
) {
  const roundStart = currentRoundStartIndex(items)
  return items.map((item, index) => {
    if (index < roundStart) return item
    if (item.kind === 'message' && item.role === 'assistant' && item.streaming) {
      return { ...item, streaming: false }
    }
    if (item.kind === 'reasoning' && item.streaming) {
      return { ...item, streaming: false }
    }
    if (item.kind === 'tool' && item.status === 'running') {
      return { ...item, status: 'error' as const, result: { ok: false, error: toolError } }
    }
    if (item.kind === 'guidance') {
      return {
        ...item,
        status: item.status === 'queued'
          ? 'not_applied' as const
          : item.status === 'accepted'
            ? 'completed' as const
            : item.status,
        finalized: true,
      }
    }
    return item
  })
}

export function prepareRunUserMessage(
  items: ChatItem[],
  userItem: Extract<ChatItem, { kind: 'message' }>,
  replaceExistingRound = false,
) {
  const existingIndex = items.findIndex((item) => item.id === userItem.id)
  if (existingIndex < 0) return [...items, userItem]
  if (!replaceExistingRound) return items
  const hasLaterBoundary = items
    .slice(existingIndex + 1)
    .some(isConversationBoundary)
  return hasLaterBoundary ? items : items.slice(0, existingIndex + 1)
}

function insertCurrentRoundItem(
  items: ChatItem[],
  item: ChatItem,
  insertBefore: (candidate: ChatItem) => boolean,
) {
  const roundStart = currentRoundStartIndex(items)
  const relativeIndex = items.slice(roundStart).findIndex(insertBefore)
  const insertionIndex = relativeIndex < 0 ? items.length : roundStart + relativeIndex
  return [...items.slice(0, insertionIndex), item, ...items.slice(insertionIndex)]
}

export function reduceRunEvent(items: ChatItem[], event: RunEvent): ChatItem[] {
  if (event.type === 'context_compression') {
    const runId = String(event.metadata?.run_id || '')
    const rawStatus = String(event.metadata?.status || 'started')
    const status = rawStatus === 'ready' || rawStatus === 'failed' ? rawStatus : 'started'
    const item: ChatItem = {
      id: `context_compression_${runId || 'active'}`,
      kind: 'context_compression',
      runId,
      status,
      trigger: String(event.metadata?.trigger || ''),
      roundsBefore: Math.max(0, Number(event.metadata?.rounds_before || 0)),
      roundsRemoved: Math.max(0, Number(event.metadata?.rounds_removed || 0)),
      roundsRemaining: Math.max(0, Number(event.metadata?.rounds_remaining || 0)),
      memoryMode: String(event.metadata?.memory_mode || ''),
      memoryStatus: String(event.metadata?.memory_status || ''),
      content: String(event.content || ''),
    }
    const index = items.findIndex((candidate) => candidate.kind === 'context_compression' && candidate.runId === runId)
    return index < 0
      ? [...items, item]
      : items.map((candidate, position) => position === index ? item : candidate)
  }
  if (event.type === 'text_delta') {
    const index = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'message' && item.role === 'assistant' && Boolean(item.streaming),
    )
    if (index >= 0) {
      return items.map((item, position) => position === index && item.kind === 'message' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return [...items, { id: eventId('assistant'), kind: 'message', role: 'assistant', content: event.content || '', streaming: true }]
  }
  if (event.type === 'reasoning_delta') {
    const index = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'reasoning' && Boolean(item.streaming),
    )
    if (index >= 0) {
      return items.map((item, position) => position === index && item.kind === 'reasoning' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return insertCurrentRoundItem(
      items,
      { id: eventId('reasoning'), kind: 'reasoning', content: event.content || '', streaming: true },
      (candidate) => candidate.kind !== 'reasoning',
    )
  }
  if (event.type === 'tool_call_start') {
    return insertCurrentRoundItem(
      items,
      {
        id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'),
        name: event.tool_name || '未知工具', arguments: event.arguments, status: 'running',
      },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant'
        || candidate.kind === 'usage'
        || candidate.kind === 'error',
    )
  }
  if (event.type === 'tool_call_result') {
    const result = event.result && typeof event.result === 'object' ? event.result as Record<string, unknown> : undefined
    const backendStatus = String(event.metadata?.status || '')
    const failed = Boolean(event.error) || backendStatus === 'failed' || result?.ok === false
    const toolStatus: 'error' | 'success' = failed ? 'error' : 'success'
    const elapsedMs = event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms)
    const toolIndex = findLastCurrentRoundItemIndex(
      items,
      (item) => item.kind === 'tool' && item.callId === event.tool_call_id,
    )
    const withTool = toolIndex >= 0 ? items.map((item, index) => index === toolIndex && item.kind === 'tool'
      ? { ...item, name: event.tool_name || item.name, result: event.result, status: toolStatus, elapsedMs }
      : item) : insertCurrentRoundItem(
        items,
        { id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'), name: event.tool_name || '未知工具', result: event.result, status: toolStatus, elapsedMs },
        (candidate) => candidate.kind === 'message' && candidate.role === 'assistant' || candidate.kind === 'usage' || candidate.kind === 'error',
      )
    const plan = extractPlanSummary(event.result)
    if (!plan) return withTool
    if (withTool.some((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id)) {
      return withTool.map((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id ? { ...item, plan } : item)
    }
    return insertCurrentRoundItem(
      withTool,
      { id: `task_plan_${plan.plan_id}`, kind: 'task_plan', plan },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant' || candidate.kind === 'usage' || candidate.kind === 'error',
    )
  }
  if (event.type === 'media_output') {
    const value = event.result && typeof event.result === 'object'
      ? event.result as Partial<MediaArtifact>
      : event.metadata?.artifact && typeof event.metadata.artifact === 'object'
        ? event.metadata.artifact as Partial<MediaArtifact>
        : null
    if (!value?.asset_id || !value.path || !value.name || value.scope !== 'download') return items
    const artifact = value as MediaArtifact
    if (items.some((item) => item.kind === 'media' && item.artifact.asset_id === artifact.asset_id && item.artifact.path === artifact.path)) return items
    return insertCurrentRoundItem(
      items,
      { id: eventId('media'), kind: 'media', artifact },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant'
        || candidate.kind === 'usage'
        || candidate.kind === 'error',
    )
  }
  if (event.type === 'guidance_applied') {
    const pending = Array.isArray(event.metadata?.guidance)
      ? event.metadata.guidance.map((value) => String(value))
      : []
    const details = Array.isArray(event.metadata?.guidance_details)
      ? event.metadata.guidance_details
        .filter((value): value is Record<string, unknown> => Boolean(value) && typeof value === 'object')
      : []
    return items.map((item) => {
      if (item.kind !== 'guidance' || item.status !== 'queued') return item
      const detail = item.guidanceId
        ? details.find((value) => String(value.id || '') === item.guidanceId)
        : undefined
      if (detail) {
        return {
          ...item,
          status: 'accepted' as const,
          attachments: Array.isArray(detail.uploaded_files)
            ? detail.uploaded_files as InputAttachment[]
            : item.attachments,
        }
      }
      if (item.guidanceId && details.length) return item
      const matched = pending.indexOf(item.content)
      if (matched < 0) return item
      pending.splice(matched, 1)
      return { ...item, status: 'accepted' as const }
    })
  }
  if (event.type === 'long_task_update') {
    const continuation = Math.max(1, Number(event.metadata?.continuation || 0) || Number(event.metadata?.long_task_state && typeof event.metadata.long_task_state === 'object'
      ? (event.metadata.long_task_state as Record<string, unknown>).continuation_count
      : 0))
    const taskId = String(event.metadata?.long_task_state && typeof event.metadata.long_task_state === 'object'
      ? (event.metadata.long_task_state as Record<string, unknown>).task_id || ''
      : event.metadata?.long_task_id || '')
    const completed = items.map((item) => {
      if (item.kind === 'message' || item.kind === 'reasoning') return { ...item, streaming: false }
      if (item.kind === 'tool' && item.status === 'running') {
        return {
          ...item,
          status: 'error' as const,
          result: { ok: false, error: { message: '工具调用因当前 Run 达到工具上限而未执行，将在下一 Run 继续', exception_type: 'LongTaskRunBoundary' } },
        }
      }
      return item
    })
    const id = `long_task_boundary_${taskId || 'active'}_${continuation}`
    if (completed.some((item) => item.id === id)) return completed
    return [...completed, { id, kind: 'long_task_boundary', taskId, continuation }]
  }
  if (event.type === 'error') {
    const message = String(event.error?.message || '聊天执行失败')
    const exceptionType = String(event.error?.exception_type || 'ProviderRunInterrupted')
    return [
      ...finalizeCurrentRoundItems(items, { message, exception_type: exceptionType }),
      { id: eventId('error'), kind: 'error', content: message },
    ]
  }
  if (event.type === 'done') {
    let guidanceRemaining = Number(event.metadata?.guidance_count || 0)
    const terminalStatus = String(event.metadata?.status || '').toLowerCase()
    const cancelled = terminalStatus === 'cancelled' || event.metadata?.cancelled === true
    const limited = terminalStatus === 'limited'
    const controlledStop = cancelled || limited
    const stopReason = String(event.metadata?.stop_reason || '')
    const limitedToolError = stopReason === 'tool_context_limit'
      ? { message: '工具调用因本轮达到上下文保护上限而未执行', exception_type: 'ToolContextLimitExceeded' }
      : stopReason === 'tool_loop_incomplete'
        ? { message: '工具调用因本轮工具循环未能正常收束而未执行', exception_type: 'ToolLoopIncomplete' }
        : { message: '工具调用因本轮工具循环达到最大次数而未执行', exception_type: 'ToolLoopLimitExceeded' }
    const terminalText = String(event.metadata?.text || (cancelled
      ? '[本轮已由用户紧急停止]'
      : '[本轮因运行保护限制而停止]'))
    const completed = items.map((item) => {
      if (item.kind === 'message') {
        return {
          ...item,
          content: controlledStop && item.role === 'assistant' && item.streaming
            ? terminalText
            : item.content,
          streaming: false,
        }
      }
      if (item.kind === 'reasoning') return { ...item, streaming: false }
      if (controlledStop && item.kind === 'tool' && item.status === 'running') {
        return {
          ...item,
          status: 'error' as const,
          result: {
            ok: false,
            error: cancelled
              ? { message: '工具调用因用户紧急停止而取消', cancelled: true }
              : limitedToolError,
          },
        }
      }
      if (item.kind === 'guidance') {
        if (item.status === 'accepted') {
          if (guidanceRemaining > 0) guidanceRemaining -= 1
          return { ...item, status: 'completed' as const, finalized: true }
        }
        if (item.status === 'queued') {
          if (guidanceRemaining > 0) {
            guidanceRemaining -= 1
            return { ...item, status: 'completed' as const, finalized: true }
          }
          return { ...item, status: 'not_applied' as const, finalized: true }
        }
        return { ...item, finalized: true }
      }
      return item
    })
    const withTerminalText = controlledStop && !completed.some((item) => item.kind === 'message' && item.role === 'assistant')
      ? [...completed, { id: eventId('assistant'), kind: 'message' as const, role: 'assistant' as const, content: terminalText }]
      : completed
    return event.usage ? [...withTerminalText, {
      id: eventId('usage'), kind: 'usage', usage: event.usage,
      elapsedMs: event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms),
      toolCalls: event.metadata?.tool_calls === undefined ? undefined : Number(event.metadata.tool_calls),
      providerRequestCount: event.usage.provider_request_count === undefined ? undefined : Number(event.usage.provider_request_count),
    }] : withTerminalText
  }
  return items
}

function historyToolStatus(status: string): 'running' | 'success' | 'error' {
  if (status === 'running') return 'running'
  if (status === 'completed' || status === 'success' || status === 'duplicate_reused') return 'success'
  return 'error'
}

export function buildHistoryItems(history: HistoryResponse | undefined): ChatItem[] {
  const metrics = new Map((history?.round_metrics || []).map((item) => [item.round, item]))
  const traces = new Map((history?.round_traces || []).map((item) => [item.round, item]))
  const result: ChatItem[] = []
  let round = Math.max(0, Number(history?.pagination?.first_round || 1) - 1)
  let messagePosition = 0
  const renderedArtifacts = new Set<string>()
  const appendArtifacts = (artifacts: MediaArtifact[] | undefined, prefix: string) => {
    for (const artifact of artifacts || []) {
      const key = `${artifact.asset_id}\0${artifact.path}`
      if (renderedArtifacts.has(key)) continue
      renderedArtifacts.add(key)
      result.push({ id: `${prefix}_${renderedArtifacts.size}`, kind: 'media', artifact })
    }
  }

  for (const message of history?.messages ?? []) {
    if (message.role !== 'user' && message.role !== 'assistant') continue
    if (message.role === 'user') {
      round += 1
      messagePosition = 0
      if (message.metadata?.synthetic === true && message.metadata.origin === 'long_task_continuation') {
        result.push({
          id: `history_long_task_${round}`,
          kind: 'long_task_boundary',
          taskId: String(message.metadata.long_task_id || ''),
          continuation: Math.max(1, Number(message.metadata.continuation || 1)),
        })
        continue
      }
      if (message.content.startsWith(PLAN_EXECUTION_PROMPT_PREFIX)) {
        result.push({ id: `history_execution_${round}`, kind: 'execution_marker', planId: message.content.split('\n')[1]?.replace('计划 ID：', '').trim() || '' })
        continue
      }
      result.push({ id: `history_${round}_user`, kind: 'message', role: 'user', content: message.content, attachments: message.attachments })
      continue
    }

    messagePosition += 1
    const trace = traces.get(round)
    if (trace?.reasoning) {
      result.push({
        id: `history_reasoning_${round}`,
        kind: 'reasoning',
        content: trace.reasoning,
        streaming: false,
      })
    }
    trace?.tools.forEach((tool, toolIndex) => {
      result.push({ id: `history_tool_${round}_${toolIndex}`, kind: 'tool', callId: tool.call_id || `history-call-${round}-${toolIndex + 1}`, name: tool.name, status: historyToolStatus(tool.status), elapsedMs: tool.elapsed_ms, argumentsText: tool.arguments_text, argumentsTruncated: tool.arguments_truncated, resultText: tool.result_text, resultTruncated: tool.result_truncated })
      appendArtifacts(tool.artifacts, `history_tool_media_${round}_${toolIndex}`)
      if (!tool.result_truncated) {
        try {
          const plan = extractPlanSummary(JSON.parse(tool.result_text))
          if (plan) result.push({ id: `history_task_plan_${plan.plan_id}_${round}`, kind: 'task_plan', plan })
        } catch { /* historical tool output need not be JSON */ }
      }
    })
    result.push({ id: `history_${round}_assistant_${messagePosition}`, kind: 'message', role: 'assistant', content: message.content })

    const selected = metrics.get(round)
    if (selected) {
      appendArtifacts(selected.artifacts, `history_media_${round}`)
      const guidanceDetails = selected.guidance_details ?? []
      if (guidanceDetails.length) {
        guidanceDetails.forEach((detail, guidanceIndex) => result.push({
          id: `history_guidance_${round}_${guidanceIndex}`,
          kind: 'guidance',
          guidanceId: detail.id,
          content: detail.display_text || detail.text || '附件引导',
          attachments: detail.uploaded_files,
          status: 'completed',
          finalized: true,
        }))
      } else {
        selected.guidance.forEach((content, guidanceIndex) => result.push({ id: `history_guidance_${round}_${guidanceIndex}`, kind: 'guidance', content, status: 'completed', finalized: true }))
      }
      result.push({
        id: `history_usage_${round}`, kind: 'usage', usage: selected.usage,
        elapsedMs: selected.elapsed_ms, toolCalls: selected.tool_calls, round,
        providerRequestCount: selected.usage.provider_request_count === undefined
          ? undefined
          : Number(selected.usage.provider_request_count),
      })
    }
  }
  return result
}

export function mergeHistoryPages(pages: HistoryResponse[] | undefined): HistoryResponse | undefined {
  if (!pages?.length) return undefined
  if (pages.length === 1) return pages[0]
  const ordered = [...pages].reverse()
  const earliest = ordered[0]
  const latest = pages[0]
  return {
    ...latest,
    messages: ordered.flatMap((page) => page.messages),
    round_metrics: ordered.flatMap((page) => page.round_metrics),
    round_traces: ordered.flatMap((page) => page.round_traces),
    pagination: {
      limit: latest.pagination?.limit ?? HISTORY_PAGE_SIZE,
      total_rounds: latest.pagination?.total_rounds
        ?? ordered.reduce((total, page) => total + page.messages.filter((message) => message.role === 'user').length, 0),
      first_round: earliest.pagination?.first_round ?? 1,
      last_round: latest.pagination?.last_round
        ?? latest.pagination?.total_rounds
        ?? ordered.reduce((total, page) => total + page.messages.filter((message) => message.role === 'user').length, 0),
      has_more_before: earliest.pagination?.has_more_before ?? false,
      next_before: earliest.pagination?.next_before ?? null,
    },
  }
}

const quickStartCards = [
  { prompt: '查询 kemo-agent 当前感知情况', icon: BrainCircuit, title: '查询感知情况', desc: '查看感知来源、采集数据与当前注入状态', tone: 'sense' },
  { prompt: '查询 kemo-agent 当前拓展情况', icon: Shapes, title: '查询拓展情况', desc: '查看拓展模块、采集能力与注入状态', tone: 'expand' },
  { prompt: '查询 kemo-agent 当前运行状态', icon: Activity, title: '查询运行状态', desc: '汇总核心模块、Provider 与外接服务状态', tone: 'status' },
  { prompt: '为当前用户创建一个定时任务', icon: TimerReset, title: '创建定时任务', desc: '通过对话描述时间、内容与执行目标', tone: 'timer' },
]

function greetingLabel() {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

export function compactPlanAssistantText(content: string, hasPlanBubble: boolean) {
  if (!hasPlanBubble) return content
  const markers = ['以下是计划详情', '以下是计划的详细信息', '新计划已生成', '任务计划已生成', '计划已生成', '计划包含', '计划 ID', '## 任务计划', '📋']
  const cut = markers.map((marker) => content.indexOf(marker)).filter((index) => index >= 0).sort((left, right) => left - right)[0]
  return cut === undefined ? content : '任务计划已创建，请在发送框上方查看并确认。'
}

export function dropLastLiveRound(items: ChatItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (item.kind === 'message' && item.role === 'user') return items.slice(0, index)
  }
  return items
}

export function createDeltaEventBatcher(
  apply: (events: RunEvent[]) => void,
  delayMs = 80,
) {
  let pending: RunEvent[] = []
  let timer: ReturnType<typeof setTimeout> | null = null
  const flush = () => {
    if (timer !== null) {
      clearTimeout(timer)
      timer = null
    }
    if (!pending.length) return
    const events = pending
    pending = []
    apply(events)
  }
  const push = (event: RunEvent) => {
    pending.push(event)
    if (timer === null) timer = setTimeout(flush, Math.max(0, delayMs))
  }
  const dispose = () => {
    flush()
  }
  return { push, flush, dispose }
}

export function resolveHistoryUserMessages(
  activeSession: string,
  currentSession: string,
  persistedUserMessages: number,
  explicitHistoryUserMessages?: number,
  editingSource?: { sessionId: string; remainingRounds: number } | null,
  undoneBaseline?: { sessionId: string; remainingRounds: number } | null,
) {
  if (explicitHistoryUserMessages !== undefined) return explicitHistoryUserMessages
  if (editingSource?.sessionId === activeSession) return editingSource.remainingRounds
  if (undoneBaseline?.sessionId === activeSession) return undoneBaseline.remainingRounds
  return activeSession === currentSession ? persistedUserMessages : 0
}

export async function executeStopRequest(
  request: () => Promise<unknown>,
  onFailure: (error: unknown) => void,
) {
  try {
    await request()
    return true
  } catch (error) {
    onFailure(error)
    return false
  }
}

const terminalPlanStatuses = new Set(['completed', 'failed', 'rejected', 'cancelled'])

export function selectDockedPlan(plans: PlanSummary[]) {
  return [...plans].reverse().find((plan) => !terminalPlanStatuses.has(plan.status))
}

function isConversationBoundary(item: ChatItem) {
  return item.kind === 'execution_marker'
    || item.kind === 'long_task_boundary'
    || item.kind === 'message' && item.role === 'user'
}

function containingBoundaryId(items: ChatItem[], itemIndex: number) {
  for (let index = itemIndex; index >= 0; index -= 1) {
    if (isConversationBoundary(items[index])) return items[index].id
  }
  return null
}

function boundaryEndIndex(items: ChatItem[], boundaryId: string | null) {
  const start = boundaryId === null
    ? -1
    : items.findIndex((item) => item.id === boundaryId)
  for (let index = start + 1; index < items.length; index += 1) {
    if (isConversationBoundary(items[index])) return index
  }
  return items.length
}

export function archiveTerminalPlansInConversation(
  items: ChatItem[],
  plans: PlanSummary[],
) {
  let result = [...items]
  const latestPlans = new Map<string, PlanSummary>()
  for (const plan of plans) {
    const current = latestPlans.get(plan.plan_id)
    if (!current || plan.revision >= current.revision) latestPlans.set(plan.plan_id, plan)
  }
  for (const plan of latestPlans.values()) {
    if (!terminalPlanStatuses.has(plan.status)) continue
    const matchingIndexes = result
      .map((item, index) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id ? index : -1)
      .filter((index) => index >= 0)
    const executionMarker = [...result]
      .reverse()
      .find((item) => item.kind === 'execution_marker' && item.planId === plan.plan_id)
    const boundaryId = executionMarker?.id
      ?? (matchingIndexes.length ? containingBoundaryId(result, matchingIndexes.at(-1)!) : undefined)
    if (boundaryId === undefined) continue

    result = result.map((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id
      ? { ...item, plan, presentation: 'reference' as const }
      : item)
    const insertionIndex = boundaryEndIndex(result, boundaryId)
    result.splice(insertionIndex, 0, {
      id: `terminal_task_plan_${plan.plan_id}_${boundaryId ?? 'opening'}`,
      kind: 'task_plan',
      plan,
      presentation: 'record',
    })
  }
  return result
}

type AssistantMessageItem = Extract<ChatItem, { kind: 'message' }>
type UsageItem = Extract<ChatItem, { kind: 'usage' }>
type TaskPlanItem = Extract<ChatItem, { kind: 'task_plan' }>

export function partitionAssistantTurnItems(items: ChatItem[]) {
  return {
    assistantMessages: items.filter(
      (item): item is AssistantMessageItem => item.kind === 'message' && item.role === 'assistant',
    ),
    usageItems: items.filter((item): item is UsageItem => item.kind === 'usage'),
    planItems: items.filter(
      (item): item is TaskPlanItem => item.kind === 'task_plan' && item.presentation !== 'reference',
    ),
    finalizedGuidance: items.filter(
      (item): item is GuidanceItem => item.kind === 'guidance' && Boolean(item.finalized),
    ),
  }
}

function TaskPlanRecord({ plan, docked, onOpen }: { plan: PlanSummary; docked: boolean; onOpen: () => void }) {
  return (
    <article className="task-plan-record" aria-label={`已创建任务计划：${plan.title}`}>
      <span className="task-plan-record-icon"><ListChecks size={17} /></span>
      <span className="task-plan-record-copy">
        <small>已创建任务计划</small>
        <strong>{plan.title}</strong>
      </span>
      <span className={`task-plan-record-status status-${plan.status}`}>{statusLabel(plan.status)}</span>
      <span className="task-plan-record-progress">{plan.progress.completed}/{plan.progress.total}</span>
      <button type="button" onClick={onOpen}>{docked ? '查看当前计划' : '任务中枢'}</button>
    </article>
  )
}

function cronScheduleLabel(task: CronTaskSummary) {
  if (task.type === 'daily') return `每天 ${task.time || '—'}`
  if (task.type === 'once') return `单次 · ${formatDateTime(task.next_run_at)}`
  if (task.type === 'recurring') {
    const seconds = Number(task.interval_seconds || 0)
    return seconds >= 3600 ? `每 ${Math.round(seconds / 3600)} 小时` : `每 ${Math.max(1, Math.round(seconds / 60))} 分钟`
  }
  return '未配置调度'
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function extractPlanSummary(value: unknown): PlanSummary | null {
  let payload = objectValue(value)
  const wrapped = objectValue(payload?.result)
  if (payload?.ok === true && wrapped) payload = wrapped
  const raw = objectValue(payload?.plan) || (payload?.plan_id ? payload : null)
  if (!raw || typeof raw.plan_id !== 'string' || typeof raw.title !== 'string' || !Array.isArray(raw.steps)) return null
  const steps = raw.steps.map((value) => objectValue(value)).filter((step): step is Record<string, unknown> => Boolean(step)).map((step) => ({
    step_id: String(step.step_id || ''), title: String(step.title || ''), description: String(step.description || ''), status: String(step.status || 'pending'),
    depends_on: Array.isArray(step.depends_on) ? step.depends_on.map(String) : [], critical: Boolean(step.critical ?? true), tool_name: String(step.tool_name || ''), started_at: String(step.started_at || ''), finished_at: String(step.finished_at || ''),
  }))
  const completed = steps.filter((step) => step.status === 'completed' || step.status === 'skipped').length
  return { plan_id: raw.plan_id, title: raw.title, description: String(raw.description || ''), status: String(raw.status || 'pending'), auto_accept: Boolean(raw.auto_accept), reminder: String(raw.reminder || ''), source: String(raw.source || ''), session_id: String(raw.session_id || ''), current_step: String(raw.current_step || ''), revision: Number(raw.revision || 1), created_at: String(raw.created_at || ''), updated_at: String(raw.updated_at || ''), progress: { completed, total: steps.length, percent: steps.length ? Math.round(completed * 100 / steps.length) : 0 }, steps }
}

function senseIconFor(source: SenseSourceSummary): SenseDataItem['icon'] {
  const text = `${source.name} ${source.display_name}`.toLowerCase()
  if (text.includes('温度') || text.includes('temperature')) return 'temperature'
  if (text.includes('湿度') || text.includes('humidity')) return 'humidity'
  if (text.includes('天气') || text.includes('weather')) return 'weather'
  return 'radio'
}

type GuidanceItem = Extract<ChatItem, { kind: 'guidance' }>
type GuidanceDisplayItem = GuidanceItem | {
  id: string
  kind: 'guidance'
  content: string
  guidanceId?: string
  attachments?: InputAttachment[]
  status: 'next_turn' | 'next_turn_error'
}

function GuidanceMessage({ user, item, placement, onRetry, onCancel }: { user: string; item: GuidanceDisplayItem; placement: 'current' | 'completed'; onRetry?: () => void; onCancel?: () => void }) {
  const title = item.status === 'queued'
    ? '正在引导'
    : item.status === 'next_turn'
      ? '已排队到下一轮'
      : item.status === 'next_turn_error'
        ? '自动发送失败'
    : item.status === 'not_applied'
      ? '本轮未生效'
      : item.status === 'error'
        ? '引导失败'
        : '引导成功'
  const detail = item.status === 'queued'
    ? '等待智能体到达下一个安全边界'
    : item.status === 'next_turn'
      ? '本轮结束后将自动作为新的用户消息发送'
      : item.status === 'next_turn_error'
        ? '消息仍已保留，可以重新发送'
    : item.status === 'accepted'
      ? '智能体已读取该引导并继续运行'
      : item.status === 'completed'
        ? '本轮运行已采用此引导'
        : item.status === 'not_applied'
          ? '本轮结束前未进入下一次模型请求'
          : '引导未能提交到当前运行'
  return <article className={`guidance-message guidance-${placement} ${item.status}`} data-guidance-status={item.status}>
    <span className="guidance-title"><i aria-hidden="true" />{title}</span>
    {item.content ? <strong>{item.content}</strong> : null}
    {item.attachments?.length ? <div className="user-attachment-list guidance-attachment-list">
      {item.attachments.map((attachment, index) => (
        <UserAttachmentCard key={attachment.asset_id || `${attachment.name}_${index}`} user={user} attachment={attachment} />
      ))}
    </div> : null}
    <small>{detail}</small>
    {onRetry || onCancel ? <div className="guidance-actions">
      {onCancel ? <button type="button" className="guidance-cancel" onClick={onCancel}>取消</button> : null}
      {onRetry ? <button type="button" className="guidance-retry" onClick={onRetry}>重新发送</button> : null}
    </div> : null}
  </article>
}

export function ContextCompressionBubble({ item }: { item: Extract<ChatItem, { kind: 'context_compression' }> }) {
  const failed = item.status === 'failed'
  const ready = item.status === 'ready'
  const title = failed ? '对话压缩失败' : ready ? '对话上下文已压缩' : '正在压缩对话上下文'
  const rounds = item.roundsRemoved > 0
    ? `${item.roundsBefore} 轮 → 保留 ${item.roundsRemaining} 轮，裁剪 ${item.roundsRemoved} 轮`
    : '正在整理较早的完整对话轮次'
  const memory = ready && item.memoryStatus === 'queued_after_commit'
    ? '；裁剪内容将在本轮提交后进入后台记忆整理'
    : ''
  return (
    <article className={`context-compression-bubble ${item.status}`} role="status" aria-live="polite">
      <span className="context-compression-icon"><BrainCircuit size={16} /></span>
      <span><strong>{title}</strong><small>{rounds}{memory}</small></span>
      <i aria-hidden="true" />
    </article>
  )
}

export function buildScheduledTaskItems(tasks: CronTaskSummary[]): ScheduledTaskItem[] {
  const supportedStatuses = new Set<ScheduledTaskItem['status']>(['enabled', 'running', 'completed', 'paused', 'failed', 'cancelled', 'disabled'])
  return [...tasks]
    .filter((task) => task.user_defined)
    .sort((left, right) => (left.next_run_at || left.created_at).localeCompare(right.next_run_at || right.created_at))
    .map((task) => ({
      id: task.task_id,
      title: task.title,
      schedule: cronScheduleLabel(task),
      nextRun: formatDateTime(task.next_run_at),
      status: supportedStatuses.has(task.status as ScheduledTaskItem['status']) ? task.status as ScheduledTaskItem['status'] : 'disabled',
      icon: task.type === 'daily' ? 'calendar' : task.type === 'recurring' ? 'alarm' : 'clipboard',
    }))
}

export function formatSenseUpdateInterval(value: unknown) {
  const seconds = Number(value)
  if (!Number.isInteger(seconds) || seconds < 1) return ''
  if (seconds % 3600 === 0) return `每 ${seconds / 3600} 小时`
  if (seconds % 60 === 0) return `每 ${seconds / 60} 分钟`
  return `每 ${seconds} 秒`
}

export function buildSenseDataItems(sources: SenseSourceSummary[]): SenseDataItem[] {
  return [...sources]
    .filter((source) => source.active_for_main_agent && source.status === 'active' && source.injected_items > 0)
    .sort((left, right) => (right.updated_at || 0) - (left.updated_at || 0))
    .map((source) => ({
      id: source.id,
      name: source.display_name || source.name,
      value: source.value_preview,
      updateInterval: formatSenseUpdateInterval(source.update_interval_seconds) || source.update_interval,
      updatedAt: formatDateTime(source.recent_update || source.updated_at),
      injected: true,
      icon: senseIconFor(source),
    }))
}

export function ChatPage() {
  const { user, userAvatarUrl, sessionId, clientId, chatRunning: running, setChatRunning: setRunning, chatRunId: activeRunId, setChatRunId: setActiveRunId, setChatAbortController, abortChatRun, chatRuns, beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus, removeNextTurnMessage, finishChatRun, clearChatRun, setSessionId, detachSession, notifySessionDeleted, sessions, refreshSessions, createNewSession, overview, refreshOverview, openCommandPanel } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const draftKey = chatDraftKey(user, sessionId)
  const draftSnapshot = useChatDraftStore((state) => state.drafts[draftKey] ?? EMPTY_CHAT_DRAFT)
  const setDraftText = useChatDraftStore((state) => state.setText)
  const setDraftUploads = useChatDraftStore((state) => state.setPendingUploads)
  const setDraftUploadFeedback = useChatDraftStore((state) => state.setUploadFeedback)
  const setDraftUploading = useChatDraftStore((state) => state.setUploading)
  const moveDraft = useChatDraftStore((state) => state.moveDraft)
  const clearDraft = useChatDraftStore((state) => state.clearDraft)
  const { text: draft, uploadFeedback, pendingUploads, uploading } = draftSnapshot
  const setDraft = (value: string | ((current: string) => string)) => setDraftText(draftKey, value)
  const setUploadFeedback = (value: typeof uploadFeedback) => setDraftUploadFeedback(draftKey, value)
  const setPendingUploads = (value: PendingUploadedFile[] | ((current: PendingUploadedFile[]) => PendingUploadedFile[])) => setDraftUploads(draftKey, value)
  const [editingSource, setEditingSource] = useState<{
    id: string
    content: string
    sessionId: string
    remainingRounds: number
  } | null>(null)
  const [editedSources, setEditedSources] = useState<Set<string>>(() => new Set())
  const [copiedItem, setCopiedItem] = useState('')
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false)
  const [knowledgeDrawerOpen, setKnowledgeDrawerOpen] = useState(false)
  const [capabilityDrawerOpen, setCapabilityDrawerOpen] = useState(false)
  const [conversationBusy, setConversationBusy] = useState<'save' | 'clear' | 'compress' | 'retry' | 'edit' | ''>('')
  const [conversationFeedback, setConversationFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [longTaskBusy, setLongTaskBusy] = useState(false)
  const [activeTaskOpen, setActiveTaskOpen] = useState(false)
  const [collapsedPlans, setCollapsedPlans] = useState<Set<string>>(() => new Set())
  const [planOverrides, setPlanOverrides] = useState<Record<string, PlanSummary>>({})
  const [planMutationNotices, setPlanMutationNotices] = useState<Record<string, string>>({})
  const [showFollowOutput, setShowFollowOutput] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [runRetryNotice, setRunRetryNotice] = useState<RunRetryNotice | null>(null)
  const [runErrorNotice, setRunErrorNotice] = useState<RunErrorNotice | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const composerPlanDockRef = useRef<HTMLDivElement | null>(null)
  const followOutputRef = useRef(true)
  const loadingEarlierRef = useRef(false)
  const prependSnapshotRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null)
  const submittedUploadsRef = useRef(new Map<string, PendingUploadedFile[]>())
  const completionSoundRunIdsRef = useRef(new Set<string>())
  const failureSoundRunIdsRef = useRef(new Set<string>())
  const consumingNextTurnRef = useRef(false)
  const locallyCommittedSessionRef = useRef('')
  const undoneRoundBaselineRef = useRef<{
    sessionId: string
    remainingRounds: number
  } | null>(null)
  const lastAttemptSessionRef = useRef(sessionId)
  const conversationKeyRef = useRef(`${user}\u0000${sessionId}`)
  const liveSessionId = sessionId || lastAttemptSessionRef.current
  const liveRun = liveSessionId ? chatRuns[chatRunKey(user, liveSessionId)] : undefined
  const effectiveRunId = activeRunId || liveRun?.runId || ''
  const liveItems = liveRun?.items ?? EMPTY_CHAT_ITEMS

  const playCompletionSoundOnce = (completedRunId: string, event: RunEvent) => {
    if (!isSuccessfulRunCompletion(event)) return
    const played = completionSoundRunIdsRef.current
    if (played.has(completedRunId)) return
    if (played.size >= 100) played.clear()
    played.add(completedRunId)
    void playUserCompletionSound(user)
  }
  const playFailureSoundOnce = (failedRunId: string, event: RunEvent) => {
    if (!isFailedRunCompletion(event)) return
    const played = failureSoundRunIdsRef.current
    if (played.has(failedRunId)) return
    if (played.size >= 100) played.clear()
    played.add(failedRunId)
    void playUserFailureSound(user)
  }
  const activeCompression = [...liveItems].reverse().find(
    (item): item is Extract<ChatItem, { kind: 'context_compression' }> => item.kind === 'context_compression' && (!item.runId || item.runId === effectiveRunId),
  )
  const setLiveItems = (updater: ChatItemsUpdater) => {
    if (liveSessionId) updateChatRunItems(user, liveSessionId, updater)
  }
  const hasCommitted = useMemo(() => {
    if (!sessionId) return false
    return sessionId === locallyCommittedSessionRef.current
      || sessions.some((session) => session.session_id === sessionId)
  }, [sessionId, sessions])
  const historyQuery = useInfiniteQuery({
    queryKey: ['history', user, sessionId],
    queryFn: ({ pageParam }) => getHistory(user, sessionId, {
      limit: HISTORY_PAGE_SIZE,
      before: pageParam,
    }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.pagination?.has_more_before
      ? lastPage.pagination.next_before ?? undefined
      : undefined,
    enabled: Boolean(user && sessionId && hasCommitted),
    retry: false,
  })
  const longTaskQuery = useQuery({
    queryKey: ['long-task', user, sessionId],
    queryFn: () => getSessionLongTask(user, sessionId),
    enabled: Boolean(user && sessionId && hasCommitted),
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.long_task.status
      return ['running', 'pausing', 'cancelling'].includes(String(status || '')) ? 1000 : false
    },
  })
  const tasksQuery = useQuery({
    queryKey: ['tasks', user],
    queryFn: () => getTasks(user),
    enabled: Boolean(user),
    refetchInterval: (query) => query.state.data?.plans.some((plan) => ['approved', 'running'].includes(plan.status)) ? 1200 : false,
  })
  useEffect(() => {
    if (!sessionId || !tasksQuery.dataUpdatedAt) return
    void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
  }, [queryClient, sessionId, tasksQuery.dataUpdatedAt, user])
  const senseQuery = useQuery({
    queryKey: ['sense', user],
    queryFn: () => getSense(user),
    enabled: Boolean(user),
  })
  const knowledgeQuery = useQuery({
    queryKey: ['knowledge', user],
    queryFn: () => getKnowledge(user),
    enabled: Boolean(user && knowledgeDrawerOpen),
  })
  const expandsQuery = useQuery({
    queryKey: ['expands', user],
    queryFn: () => getExpands(user),
    enabled: Boolean(user && capabilityDrawerOpen),
  })
  const skillsQuery = useQuery({
    queryKey: ['skills', user],
    queryFn: () => getSkills(user),
    enabled: Boolean(user && capabilityDrawerOpen),
  })
  const capabilityItems = useMemo(
    () => buildCapabilityReferenceItems(expandsQuery.data, skillsQuery.data),
    [expandsQuery.data, skillsQuery.data],
  )

  const historyData = useMemo(
    () => mergeHistoryPages(historyQuery.data?.pages),
    [historyQuery.data?.pages],
  )
  const historyItems = useMemo<ChatItem[]>(() => buildHistoryItems(historyData), [historyData])
  const persistedUserMessages = historyData?.pagination?.total_rounds
    ?? historyData?.messages.filter((message) => message.role === 'user').length
    ?? 0
  const handoffReady = liveRun?.phase === 'awaiting_history' && persistedUserMessages > liveRun.historyUserMessages
  const visibleLiveItems = handoffReady ? [] : liveItems
  const items = [...historyItems, ...visibleLiveItems]
  useEffect(() => {
    const conversationKey = `${user}\u0000${sessionId}`
    if (conversationKeyRef.current === conversationKey) return
    conversationKeyRef.current = conversationKey
    lastAttemptSessionRef.current = sessionId
    followOutputRef.current = true
    loadingEarlierRef.current = false
    prependSnapshotRef.current = null
    setShowFollowOutput(false)
    setEditingSource(null)
    undoneRoundBaselineRef.current = null
    setEditedSources(new Set())
    setCopiedItem('')
    if (!running) setActiveRunId('')
    setKnowledgeDrawerOpen(false)
    setCapabilityDrawerOpen(false)
    setConversationBusy('')
    setConversationFeedback(null)
    setLongTaskBusy(false)
    setStopping(false)
    setPlanOverrides({})
    setPlanMutationNotices({})
    setRunRetryNotice(null)
    setRunErrorNotice(null)
    abortChatRun()
  }, [abortChatRun, user, sessionId])

  useEffect(() => {
    if (!runErrorNotice) return
    const timer = window.setTimeout(() => setRunErrorNotice(null), 10_000)
    return () => window.clearTimeout(timer)
  }, [runErrorNotice])

  useEffect(() => {
    if (!handoffReady || !liveSessionId) return
    clearChatRun(user, liveSessionId)
  }, [clearChatRun, handoffReady, liveSessionId, user])

  useEffect(() => {
    const prompt = searchParams.get('prompt')
    if (!prompt) return
    setDraft(prompt)
    const next = new URLSearchParams(searchParams)
    next.delete('prompt')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    const element = scrollRef.current
    if (!element || !followOutputRef.current || prependSnapshotRef.current) return
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: running ? 'auto' : 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [items.length, liveItems, running])

  useLayoutEffect(() => {
    const snapshot = prependSnapshotRef.current
    const element = scrollRef.current
    if (!snapshot || !element) return
    element.scrollTop = snapshot.scrollTop + (element.scrollHeight - snapshot.scrollHeight)
    prependSnapshotRef.current = null
    loadingEarlierRef.current = false
  }, [historyQuery.data?.pages.length])

  const send = async (
    promptOverride?: string,
    options: {
      sessionId?: string
      content?: Array<Record<string, unknown>>
      historyUserMessages?: number
      uploadedFiles?: PendingUploadedFile[]
      internalNextTurn?: boolean
      userMessageId?: string
    } = {},
  ) => {
    const prompt = (promptOverride ?? draft).trim()
    const uploadedFiles = options.uploadedFiles ?? []
    const hasContent = Boolean(options.content?.length)
    if ((!prompt && !hasContent && !uploadedFiles.length) || !user || (!options.internalNextTurn && (running || stopping)) || uploading) return false
    const activeSession = options.sessionId || sessionId || createSessionId()
    const submissionDraftKey = draftKey
    let finalDraftKey = submissionDraftKey
    lastAttemptSessionRef.current = activeSession
    const runId = `run_${randomUUID().replaceAll('-', '')}`
    if (uploadedFiles.length) {
      submittedUploadsRef.current.set(runId, uploadedFiles.map((file) => ({ ...file })))
    }
    const historyUserMessages = resolveHistoryUserMessages(
      activeSession,
      sessionId,
      persistedUserMessages,
      options.historyUserMessages,
      editingSource,
      undoneRoundBaselineRef.current,
    )
    beginChatRun(user, activeSession, runId, historyUserMessages)
    setRunRetryNotice(null)
    setRunErrorNotice(null)
    setDraft('')
    if (uploadedFiles.length) {
      setPendingUploads((current) => removeSubmittedUploads(current, uploadedFiles))
    }
    setRunning(true)
    setActiveRunId(runId)
    setConversationMenuOpen(false)
    followOutputRef.current = true
    setShowFollowOutput(false)
    if (editingSource) setEditedSources((current) => new Set(current).add(editingSource.id))
    const displayedPrompt = prompt
    const userMessageId = options.userMessageId || eventId('user')
    const userItem: Extract<ChatItem, { kind: 'message' }> = {
      id: userMessageId,
      kind: 'message',
      role: 'user',
      content: displayedPrompt,
      attachments: uploadedFiles.map(pendingInputAttachment),
      edited: Boolean(editingSource),
      originalContent: editingSource?.content,
    }
    updateChatRunItems(
      user,
      activeSession,
      (current) => prepareRunUserMessage(current, userItem, Boolean(options.internalNextTurn)),
    )
    setEditingSource(null)
    const controller = new AbortController()
    setChatAbortController(controller)
    let committed = false
    let successful = false
    let restoreDraftAfterFailure = false
    let terminalReceived = false
    const deltaBatcher = createDeltaEventBatcher((events) => {
      updateChatRunItems(user, activeSession, (current) => (
        events.reduce((next, event) => reduceRunEvent(next, event), current)
      ))
    })
    try {
      await streamChat({
        user,
        sessionId: activeSession,
        clientId,
        prompt: options.content?.length ? '' : prompt,
        content: options.content,
        uploadedFiles: uploadedFiles.map((file) => file.path),
        runId,
        signal: controller.signal,
        onEvent: (event) => {
          const eventState = event.metadata?.long_task_state
          if (eventState && typeof eventState === 'object') {
            queryClient.setQueryData<LongTaskResponse>(
              ['long-task', user, activeSession],
              { user, source: 'web', session_id: activeSession, long_task: eventState as LongTaskState },
            )
          }
          if (event.type === 'long_task_update') {
            const nextRunId = String(event.metadata?.next_run_id || '')
            if (nextRunId) {
              beginChatRun(user, activeSession, nextRunId, historyUserMessages)
              setActiveRunId(nextRunId)
            }
          }
          if (event.type === 'retrying') {
            const failedAttempt = Math.max(1, Number(event.metadata?.failed_attempt || 1))
            const nextAttempt = Math.max(failedAttempt + 1, Number(event.metadata?.next_attempt || failedAttempt + 1))
            const maxAttempts = Math.max(nextAttempt, Number(event.metadata?.max_attempts || 5))
            // Apply deltas from the failed attempt before removing that attempt.
            // Otherwise a pending batch can be flushed after the reset and mix
            // failed-attempt text with the next attempt.
            deltaBatcher.flush()
            setRunRetryNotice({ failedAttempt, nextAttempt, maxAttempts })
            setRunErrorNotice(null)
            updateChatRunItems(user, activeSession, resetCurrentRoundItemsForRetry)
            return
          }
          if (isProvisionalRunError(event)) return
          if (event.type === 'text_delta' || event.type === 'reasoning_delta') {
            setRunRetryNotice(null)
            deltaBatcher.push(event)
            return
          }
          if (isRetryAttemptProgress(event)) setRunRetryNotice(null)
          deltaBatcher.flush()
          if (event.type === 'done') {
            terminalReceived = true
            committed = event.metadata?.committed !== false
            successful = isSuccessfulRunCompletion(event)
            setRunRetryNotice(null)
            if (isFailedRunCompletion(event)) {
              const failure = event.metadata?.failure && typeof event.metadata.failure === 'object'
                ? event.metadata.failure as Record<string, unknown>
                : undefined
              setRunErrorNotice({
                id: eventId('run-error'),
                message: `最终错误：${String(event.error?.message || failure?.message || '智能体运行失败')}`,
              })
            } else {
              setRunErrorNotice(null)
            }
            playCompletionSoundOnce(runId, event)
            playFailureSoundOnce(runId, event)
            const terminalStatus = String(event.metadata?.status || 'completed').toLowerCase()
            restoreDraftAfterFailure = ['failed', 'error'].includes(terminalStatus)
            const submitted = submittedUploadsRef.current.get(runId) ?? []
            submittedUploadsRef.current.delete(runId)
            if (successful && submitted.length) {
              setDraftUploads(submissionDraftKey, (current) => removeSubmittedUploads(current, submitted))
            }
          } else if (event.type === 'error') {
            terminalReceived = true
            committed = event.metadata?.committed !== false
            restoreDraftAfterFailure = true
            setRunRetryNotice(null)
            setRunErrorNotice({
              id: eventId('run-error'),
              message: `最终错误：${String(event.error?.message || '智能体运行失败')}`,
            })
            playFailureSoundOnce(runId, event)
            submittedUploadsRef.current.delete(runId)
          }
          updateChatRunItems(user, activeSession, (current) => reduceRunEvent(current, event))
        },
      })
      deltaBatcher.flush()
      if (!terminalReceived) {
        terminalReceived = true
        restoreDraftAfterFailure = true
        const interruptionMessage = '响应流在终态事件到达前结束，未收到最终状态'
        const missingEvent: RunEvent = {
          type: 'error',
          error: { message: interruptionMessage, exception_type: 'MissingTerminalEvent' },
          metadata: { status: 'interrupted', terminal: false },
        }
        setRunRetryNotice(null)
        setRunErrorNotice({ id: eventId('run-error'), message: interruptionMessage })
        updateChatRunItems(user, activeSession, (current) => [
          ...finalizeCurrentRoundItems(current, {
            message: interruptionMessage,
            exception_type: 'MissingTerminalEvent',
          }),
          { id: eventId('error'), kind: 'error', content: interruptionMessage },
        ])
      }
      await refreshSessions()
      if (!sessionId) {
        locallyCommittedSessionRef.current = activeSession
        finalDraftKey = chatDraftKey(user, activeSession)
        moveDraft(submissionDraftKey, finalDraftKey)
        setSessionId(activeSession)
      }
      if (
        committed
        && undoneRoundBaselineRef.current?.sessionId === activeSession
      ) undoneRoundBaselineRef.current = null
      if (committed) await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
      refreshOverview()
    } catch (error) {
      deltaBatcher.flush()
      const aborted = (error as Error).name === 'AbortError'
      const missingTerminal = error instanceof ApiError && error.code === 'missing_terminal'
      const message = missingTerminal
        ? '响应流在终态事件到达前结束，未收到最终状态'
        : error instanceof Error ? error.message : '聊天失败'
      const interruptionMessage = missingTerminal
        ? message
        : '响应连接已中断，未收到最终状态'
      if (aborted) {
        setRunRetryNotice(null)
        setRunErrorNotice(null)
      }
      if (!terminalReceived) {
        updateChatRunItems(user, activeSession, (current) => {
          const finalized = finalizeCurrentRoundItems(current, {
            message: aborted
              ? '当前响应连接已中断'
              : interruptionMessage,
            exception_type: aborted
              ? 'ClientStreamAborted'
              : missingTerminal ? 'MissingTerminalEvent' : 'ClientStreamInterrupted',
            ...(aborted ? { cancelled: true } : {}),
          })
          return aborted
            ? finalized
            : [...finalized, { id: eventId('error'), kind: 'error', content: interruptionMessage }]
        })
      }
      if (!aborted && !terminalReceived) {
        setRunRetryNotice(null)
        setRunErrorNotice({ id: eventId('run-error'), message: interruptionMessage })
      }
      if (!terminalReceived && !aborted) {
        restoreDraftAfterFailure = true
      }
    } finally {
      deltaBatcher.dispose()
      submittedUploadsRef.current.delete(runId)
      if (restoreDraftAfterFailure && promptOverride === undefined && prompt) {
        setDraftText(finalDraftKey, (current) => current || prompt)
      }
      finishChatRun(user, activeSession, committed)
      setChatAbortController(null)
      setActiveRunId('')
      setRunning(false)
      setStopping(false)
      void queryClient.invalidateQueries({ queryKey: ['long-task', user, activeSession] })
    }
    return committed
  }

  useEffect(() => {
    const pending = liveRun?.nextTurnQueue[0]
    if (!pending || pending.status !== 'queued' || running || uploading || stopping || consumingNextTurnRef.current || !user || !liveSessionId) return
    consumingNextTurnRef.current = true
    setNextTurnMessageStatus(user, liveSessionId, pending.id, 'sending')
    void send(pending.content, {
      sessionId: liveSessionId,
      historyUserMessages: pending.historyUserMessages,
      uploadedFiles: pending.uploadedFiles,
      internalNextTurn: true,
      userMessageId: `next_turn_${pending.id}`,
    }).then((committed) => {
      if (committed) removeNextTurnMessage(user, liveSessionId, pending.id)
      else setNextTurnMessageStatus(user, liveSessionId, pending.id, 'error', '下一轮未成功提交')
    }).catch((error) => {
      setNextTurnMessageStatus(
        user,
        liveSessionId,
        pending.id,
        'error',
        error instanceof Error ? error.message : '下一轮自动发送失败',
      )
    }).finally(() => {
      consumingNextTurnRef.current = false
    })
  }, [liveRun?.nextTurnQueue, liveSessionId, removeNextTurnMessage, running, setNextTurnMessageStatus, stopping, uploading, user])

  const uploadFiles = async (files: File[]) => {
    if (!user || uploading || !files.length) return
    const remainingSlots = Math.max(0, 20 - pendingUploads.length)
    if (!remainingSlots) {
      setUploadFeedback({ tone: 'error', text: '每轮最多附加 20 个文件，请先移除部分附件' })
      return
    }
    const accepted = files.slice(0, remainingSlots)
    const skipped = files.length - accepted.length
    const batchDraftKey = draftKey
    const uploadUser = user
    const results: Array<PendingUploadedFile | Error | undefined> = new Array(accepted.length)
    let cursor = 0
    setDraftUploading(batchDraftKey, true)
    setDraftUploadFeedback(batchDraftKey, { tone: 'pending', text: `正在上传 ${accepted.length} 个文件…` })
    try {
      const worker = async () => {
        while (cursor < accepted.length) {
          const index = cursor
          cursor += 1
          const file = accepted[index]
          try {
            const result = await uploadUserFile(uploadUser, 'file_upload', file.name, file)
            const path = result.path || file.name
            results[index] = {
              path,
              name: path.split('/').at(-1) || file.name,
              size: result.size ?? file.size,
              mimeType: result.mime_type || file.type || 'application/octet-stream',
              mediaKind: result.media_kind || (file.type.startsWith('image/')
                ? 'image'
                : file.type.startsWith('audio/')
                  ? 'audio'
                  : file.type.startsWith('video/')
                    ? 'video'
                    : 'file'),
              checksumSha256: result.checksum_sha256,
            }
          } catch (error) {
            results[index] = error instanceof Error ? error : new Error('上传失败')
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(3, accepted.length) }, () => worker()))
      const uploaded = results.filter((item): item is PendingUploadedFile => Boolean(item) && !(item instanceof Error))
      const failures = results.filter((item): item is Error => item instanceof Error)
      if (uploaded.length) setDraftUploads(batchDraftKey, (current) => [...current, ...uploaded].slice(0, 20))
      const messages: string[] = []
      if (failures.length) messages.push(`${failures.length} 个文件上传失败：${failures[0].message}`)
      if (skipped) messages.push(`${skipped} 个文件因每轮 20 项限制未上传`)
      setDraftUploadFeedback(batchDraftKey, messages.length ? { tone: 'error', text: messages.join('；') } : null)
      if (uploaded.length) await queryClient.invalidateQueries({ queryKey: ['user-files', uploadUser, 'file_upload'] })
    } finally {
      setDraftUploading(batchDraftKey, false)
    }
  }
  const newConversation = async () => {
    const previousDraftKey = draftKey
    abortChatRun()
    await createNewSession()
    clearDraft(previousDraftKey)
    if (liveSessionId) clearChatRun(user, liveSessionId)
    setConversationMenuOpen(false)
  }

  const saveAndNewConversation = async () => {
    if (running || conversationBusy) return
    setConversationBusy('save')
    setConversationFeedback(null)
    const previousSessionId = sessionId
    let previousSessionClosed = false
    detachSession()
    try {
      if (previousSessionId) {
        await closeSession(user, previousSessionId, clientId)
        previousSessionClosed = true
      }
      await newConversation()
    } catch (error) {
      if (previousSessionId && !previousSessionClosed) setSessionId(previousSessionId)
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '保存当前对话失败' })
    } finally {
      setConversationBusy('')
    }
  }

  const clearConversation = async () => {
    if (running || conversationBusy) return
    if (sessionId && hasCommitted && !window.confirm('清空此对话将删除当前归档，并立即创建一个新对话。是否继续？')) return
    setConversationBusy('clear')
    setConversationFeedback(null)
    const previousSessionId = sessionId
    let previousSessionRemoved = false
    detachSession()
    try {
      if (previousSessionId && hasCommitted) {
        await deleteSession(user, previousSessionId, clientId)
        previousSessionRemoved = true
        notifySessionDeleted(previousSessionId)
        queryClient.removeQueries({ queryKey: ['history', user, previousSessionId] })
        if (locallyCommittedSessionRef.current === previousSessionId) locallyCommittedSessionRef.current = ''
        void refreshSessions()
        refreshOverview()
      } else if (previousSessionId) {
        await closeSession(user, previousSessionId, clientId)
        previousSessionRemoved = true
      }
      await newConversation()
    } catch (error) {
      if (previousSessionId && !previousSessionRemoved) setSessionId(previousSessionId)
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '清空当前对话失败' })
    } finally {
      setConversationBusy('')
    }
  }

  const compressCurrentConversation = async () => {
    if (running || conversationBusy) return
    if (!sessionId || !hasCommitted) {
      setConversationFeedback({ tone: 'error', text: '当前对话尚未归档，暂时无法压缩。' })
      return
    }
    setConversationBusy('compress')
    setConversationFeedback(null)
    try {
      const result = await compressSession(user, sessionId)
      const compressionText = result.compressed
        ? `上下文压缩完成，已整理 ${result.rounds_removed} 轮历史。`
        : '当前上下文较短，暂时无需压缩。'
      const memory = result.memory
      const memoryText = memory.status === 'queued'
        ? (Number(memory.pending_rounds || 0) > 0
            ? `记忆提取已转入后台，共有 ${Number(memory.pending_rounds)} 轮待处理。`
            : '记忆提取已转入后台。')
        : memory.status === 'completed'
          ? (memory.candidates > 0
            ? `已同步提取 ${memory.candidates} 条记忆候选。`
            : '记忆提取已完成，本次没有需要保存的新记忆。')
        : memory.status === 'skipped'
          ? (memory.reason === 'already_processed'
              ? '记忆已是最新状态。'
              : memory.reason === 'memory_extraction_disabled'
                ? '记忆提取已按配置关闭。'
                : '当前没有可提取的完整对话轮次。')
          : memory.error?.message
            ? `记忆提取任务登记失败：${memory.error.message}`
            : '记忆提取未完成，已保留待后台重试。'
      setConversationFeedback({
        tone: memory.status === 'failed' ? 'error' : 'success',
        text: `${compressionText}${memoryText}`,
      })
      refreshOverview()
    } catch (error) {
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '手动上下文压缩失败' })
    } finally {
      setConversationBusy('')
    }
  }

  const editAndResend = async (id: string, content: string) => {
    if (running || conversationBusy || !lastUserMessage || lastUserMessage.id !== id) return
    const targetSession = sessionId || lastAttemptSessionRef.current
    const persistedRounds = persistedUserMessages
    const liveRounds = visibleLiveItems.filter((item) => item.kind === 'message' && item.role === 'user').length
    const expectedRound = persistedRounds + liveRounds
    if (!targetSession || expectedRound < 1) return
    setConversationBusy('edit')
    setConversationFeedback(null)
    try {
      const undo = await undoLastRound(user, targetSession, expectedRound, content)
      undoneRoundBaselineRef.current = {
        sessionId: targetSession,
        remainingRounds: Math.max(0, undo.remaining_rounds),
      }
      setLiveItems((current) => dropLastLiveRound(current))
      if (sessionId) {
        void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] }).catch(() => undefined)
      }
      const editableContent = undo.prompt || content
      setDraft(editableContent)
      setEditingSource({
        id,
        content,
        sessionId: targetSession,
        remainingRounds: Math.max(0, undo.remaining_rounds),
      })
      setConversationFeedback({ tone: 'success', text: '最新一轮已撤销，原问题已放回输入框，可修改后重新发送。' })
      window.requestAnimationFrame(() => {
        const input = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')
        if (!input) return
        input.focus()
        input.setSelectionRange(input.value.length, input.value.length)
      })
    } catch (error) {
      setConversationFeedback({
        tone: 'error',
        text: error instanceof Error ? error.message : '撤销最新一轮并进入编辑状态失败',
      })
    } finally {
      setConversationBusy('')
    }
  }

  const cancelEditAndResend = () => {
    setEditingSource(null)
    setDraft('')
    setConversationFeedback({
      tone: 'success',
      text: '已取消编辑；被撤销的轮次不会恢复，下一条消息将直接作为新一轮发送。',
    })
    if (sessionId) {
      void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] }).catch(() => undefined)
    }
  }

  const copyMessage = async (id: string, content: string) => {
    await copyText(content)
    setCopiedItem(id)
    window.setTimeout(() => setCopiedItem((current) => current === id ? '' : current), 1200)
  }

  const sendGuidance = async () => {
    const guidance = draft.trim()
    const uploadedFiles = pendingUploads.map((file) => ({ ...file }))
    if ((!guidance && !uploadedFiles.length) || !user || !running || !effectiveRunId || uploading) return
    const id = eventId('guidance')
    const attachments = uploadedFiles.map(pendingInputAttachment)
    const displayText = guidance || `附件引导：${uploadedFiles.map((file) => file.name).join('、')}`
    const clearSubmittedInput = () => {
      setDraft('')
      if (uploadedFiles.length) {
        setDraftUploads(draftKey, (current) => removeSubmittedUploads(current, uploadedFiles))
      }
    }
    const queueForNextTurn = () => {
      if (!liveSessionId) return false
      setLiveItems((current) => current.filter((item) => item.id !== id))
      const message: PendingNextTurnMessage = {
        id,
        content: guidance,
        uploadedFiles,
        historyUserMessages: Math.max(
          persistedUserMessages,
          (liveRun?.historyUserMessages ?? persistedUserMessages) + 1,
        ),
        status: 'queued',
      }
      queueNextTurnMessage(user, liveSessionId, message)
      clearSubmittedInput()
      return true
    }
    if (stopping) {
      queueForNextTurn()
      return
    }
    setLiveItems((current) => [...current, {
      id,
      kind: 'guidance',
      guidanceId: id,
      content: displayText,
      attachments,
      status: 'queued',
    }])
    clearSubmittedInput()
    try {
      const result = await submitGuidance(user, effectiveRunId, guidance, {
        guidanceId: id,
        uploadedFiles: uploadedFiles.map((file) => file.path),
      })
      if (result.status === 'queued_next_turn') {
        queueForNextTurn()
      } else {
        clearSubmittedInput()
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        if (queueForNextTurn()) return
      }
      setLiveItems((current) => current.map((item) => item.kind === 'guidance' && item.id === id ? { ...item, status: 'error' } : item))
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '运行中引导提交失败' }])
    }
  }

  const activePlan = overview?.active_plan
  const lastUserMessage = [...items].reverse().find((item) => item.kind === 'message' && item.role === 'user')
  const latestRunningGuidance = running
    ? [...visibleLiveItems].reverse().find((item): item is GuidanceItem => item.kind === 'guidance' && !item.finalized)
    : undefined
  const pendingNextTurn = [...(liveRun?.nextTurnQueue ?? [])]
    .reverse()
    .find((item) => item.status === 'queued' || item.status === 'error')
  const guidancePreviewItem: GuidanceDisplayItem | undefined = pendingNextTurn
    ? {
      id: pendingNextTurn.id,
      kind: 'guidance',
      content: pendingNextTurn.content,
      guidanceId: pendingNextTurn.id,
      attachments: pendingNextTurn.uploadedFiles?.map(pendingInputAttachment),
      status: pendingNextTurn.status === 'error' ? 'next_turn_error' : 'next_turn',
    }
    : latestRunningGuidance
  const regenerateLastResponse = async () => {
    if (running || conversationBusy || !lastUserMessage || lastUserMessage.kind !== 'message') return
    const prompt = lastUserMessage.content
    const targetSession = sessionId || lastAttemptSessionRef.current
    const persistedRounds = persistedUserMessages
    const liveRounds = visibleLiveItems.filter((item) => item.kind === 'message' && item.role === 'user').length
    const expectedRound = persistedRounds + liveRounds
    if (expectedRound < 1) return
    setConversationBusy('retry')
    setConversationFeedback(null)
    try {
      const undo = targetSession
        ? await undoLastRound(user, targetSession, expectedRound, prompt)
        : null
      setLiveItems((current) => dropLastLiveRound(current))
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
      }
      await send(prompt, {
        sessionId: targetSession || undefined,
        content: undo?.content?.length ? undo.content : undefined,
        // 重新生成会先撤销一轮再补回一轮，最终历史轮数不会增长。
        // 接管基线必须使用撤销后的轮数，否则持久化历史与流式缓存会同时显示。
        historyUserMessages: Math.max(0, expectedRound - 1),
      })
    } catch (error) {
      setConversationFeedback({
        tone: 'error',
        text: error instanceof Error ? error.message : '撤销上一轮并重新发送失败',
      })
    } finally {
      setConversationBusy('')
    }
  }
  useEffect(() => {
    const handleConversationCommand = (event: Event) => {
      const action = (event as CustomEvent<ConversationCommandAction>).detail
      if (action === 'save') void saveAndNewConversation()
      else if (action === 'clear') void clearConversation()
      else if (action === 'compress') void compressCurrentConversation()
      else if (action === 'retry') void regenerateLastResponse()
    }
    window.addEventListener(CONVERSATION_COMMAND_EVENT, handleConversationCommand)
    return () => window.removeEventListener(CONVERSATION_COMMAND_EVENT, handleConversationCommand)
  }, [clearConversation, compressCurrentConversation, regenerateLastResponse, saveAndNewConversation])
  const recentTasks = useMemo(() => buildScheduledTaskItems(tasksQuery.data?.cron_tasks || []), [tasksQuery.data])
  const recentSenseData = useMemo(() => buildSenseDataItems(senseQuery.data?.sources || []), [senseQuery.data])
  const commandPlanStatus = async (plan: PlanSummary, action: 'pause' | 'cancel') => {
    try {
      const response = await commandPlan(user, plan.plan_id, action)
      const updated = extractPlanSummary(response.plan)
      if (updated) setPlanOverrides((current) => ({ ...current, [updated.plan_id]: updated }))
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    } catch (error) {
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划更新失败' }])
    }
  }

  const retryFailedPlanStep = async (plan: PlanSummary, stepId: string) => {
    try {
      const response = await retryPlanStep(user, plan.plan_id, stepId, plan.revision, plan.session_id)
      const updated = extractPlanSummary(response.plan)
      if (updated) setPlanOverrides((current) => ({ ...current, [updated.plan_id]: updated }))
      setPlanMutationNotices((current) => ({
        ...current,
        [plan.plan_id]: response.activated
          ? '失败步骤已重置，计划已自动恢复执行。'
          : response.reason === 'fix_incomplete'
            ? '当前步骤已重置，仍有其他失败步骤需要修正。'
            : '失败步骤已重置，计划等待继续。',
      }))
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    } catch (error) {
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划步骤重试失败' }])
    }
  }

  const executePlan = async (plan: PlanSummary) => {
    if (!user || running) return
    const activeSession = plan.session_id || sessionId || createSessionId()
    lastAttemptSessionRef.current = activeSession
    const runId = `run_${randomUUID().replaceAll('-', '')}`
    const historyUserMessages = activeSession === sessionId ? persistedUserMessages : 0
    beginChatRun(user, activeSession, runId, historyUserMessages)
    setRunRetryNotice(null)
    setRunErrorNotice(null)
    updateChatRunItems(user, activeSession, (current) => [
      ...current,
      { id: eventId('plan_execution'), kind: 'execution_marker', planId: plan.plan_id },
    ])
    setPlanOverrides((current) => ({
      ...current,
      [plan.plan_id]: { ...plan, status: 'running', revision: plan.revision + 1 },
    }))
    setRunning(true)
    setActiveRunId(runId)
    followOutputRef.current = true
    setShowFollowOutput(false)
    const controller = new AbortController()
    setChatAbortController(controller)
    let committed = false
    let refreshedRunningPlan = false
    let terminalReceived = false
    const deltaBatcher = createDeltaEventBatcher((events) => {
      updateChatRunItems(user, activeSession, (current) => (
        events.reduce((next, event) => reduceRunEvent(next, event), current)
      ))
    })
    try {
      await streamChat({
        user,
        sessionId: activeSession,
        clientId,
        prompt: '',
        planId: plan.plan_id,
        runId,
        signal: controller.signal,
        onEvent: (event) => {
          if (!refreshedRunningPlan) {
            refreshedRunningPlan = true
            void queryClient.invalidateQueries({ queryKey: ['tasks', user] })
          }
          if (event.type === 'retrying') {
            const failedAttempt = Math.max(1, Number(event.metadata?.failed_attempt || 1))
            const nextAttempt = Math.max(failedAttempt + 1, Number(event.metadata?.next_attempt || failedAttempt + 1))
            const maxAttempts = Math.max(nextAttempt, Number(event.metadata?.max_attempts || 5))
            // Keep the retry boundary ordered with the buffered failed-attempt
            // deltas so they cannot be applied after the reset.
            deltaBatcher.flush()
            setRunRetryNotice({ failedAttempt, nextAttempt, maxAttempts })
            setRunErrorNotice(null)
            updateChatRunItems(user, activeSession, resetCurrentRoundItemsForRetry)
            return
          }
          if (isProvisionalRunError(event)) return
          if (event.type === 'text_delta' || event.type === 'reasoning_delta') {
            setRunRetryNotice(null)
            deltaBatcher.push(event)
            return
          }
          if (isRetryAttemptProgress(event)) setRunRetryNotice(null)
          deltaBatcher.flush()
          if (event.type === 'done') {
            terminalReceived = true
            committed = event.metadata?.committed !== false
            setRunRetryNotice(null)
            if (isFailedRunCompletion(event)) {
              const failure = event.metadata?.failure && typeof event.metadata.failure === 'object'
                ? event.metadata.failure as Record<string, unknown>
                : undefined
              setRunErrorNotice({ id: eventId('run-error'), message: `最终错误：${String(event.error?.message || failure?.message || '任务计划执行失败')}` })
            } else {
              setRunErrorNotice(null)
            }
            playCompletionSoundOnce(runId, event)
            playFailureSoundOnce(runId, event)
          } else if (event.type === 'error') {
            terminalReceived = true
            committed = event.metadata?.committed !== false
            setRunRetryNotice(null)
            setRunErrorNotice({ id: eventId('run-error'), message: `最终错误：${String(event.error?.message || '任务计划执行失败')}` })
            playFailureSoundOnce(runId, event)
          }
          const updated = event.type === 'tool_call_result' ? extractPlanSummary(event.result) : null
          if (updated) setPlanOverrides((current) => ({ ...current, [updated.plan_id]: updated }))
          updateChatRunItems(user, activeSession, (current) => reduceRunEvent(current, event))
        },
      })
      deltaBatcher.flush()
      if (!terminalReceived) {
        terminalReceived = true
        const interruptionMessage = '任务计划响应流在终态事件到达前结束，未收到最终状态'
        const missingEvent: RunEvent = {
          type: 'error',
          error: { message: interruptionMessage, exception_type: 'MissingTerminalEvent' },
          metadata: { status: 'interrupted', terminal: false },
        }
        setRunRetryNotice(null)
        setRunErrorNotice({ id: eventId('run-error'), message: interruptionMessage })
        updateChatRunItems(user, activeSession, (current) => [
          ...finalizeCurrentRoundItems(current, {
            message: interruptionMessage,
            exception_type: 'MissingTerminalEvent',
          }),
          { id: eventId('error'), kind: 'error', content: interruptionMessage },
        ])
      }
      await refreshSessions()
      if (!sessionId) {
        locallyCommittedSessionRef.current = activeSession
        setSessionId(activeSession)
      }
      if (committed) await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
      refreshOverview()
    } catch (error) {
      deltaBatcher.flush()
      const aborted = (error as Error).name === 'AbortError'
      const missingTerminal = error instanceof ApiError && error.code === 'missing_terminal'
      const message = missingTerminal
        ? '任务计划响应流在终态事件到达前结束，未收到最终状态'
        : error instanceof Error ? error.message : '任务计划执行失败'
      const interruptionMessage = missingTerminal
        ? message
        : '任务计划响应连接已中断，未收到最终状态'
      if (aborted) {
        setRunRetryNotice(null)
        setRunErrorNotice(null)
      }
      if (!terminalReceived) {
        updateChatRunItems(user, activeSession, (current) => {
          const finalized = finalizeCurrentRoundItems(current, {
            message: aborted
              ? '任务计划响应连接已中断'
              : interruptionMessage,
            exception_type: aborted
              ? 'ClientStreamAborted'
              : missingTerminal ? 'MissingTerminalEvent' : 'ClientStreamInterrupted',
            ...(aborted ? { cancelled: true } : {}),
          })
          return aborted
            ? finalized
            : [...finalized, { id: eventId('error'), kind: 'error', content: interruptionMessage }]
        })
      }
      if (!aborted && !terminalReceived) {
        setRunRetryNotice(null)
        setRunErrorNotice({ id: eventId('run-error'), message: interruptionMessage })
      }
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    } finally {
      deltaBatcher.dispose()
      finishChatRun(user, activeSession, committed)
      setChatAbortController(null)
      setActiveRunId('')
      setRunning(false)
      setStopping(false)
    }
  }

  const planActions = (plan: PlanSummary) => ({
    onToggleCollapse: () => setCollapsedPlans((current) => { const next = new Set(current); if (next.has(plan.plan_id)) next.delete(plan.plan_id); else next.add(plan.plan_id); return next }),
    onReject: () => void commandPlanStatus(plan, 'cancel'),
    onModify: () => navigate(`/tasks?user=${encodeURIComponent(user)}`),
    onApprove: () => void executePlan(plan),
    onPause: () => void commandPlanStatus(plan, 'pause'),
    onRetry: () => void executePlan(plan),
    onRetryStep: (stepId: string) => void retryFailedPlanStep(plan, stepId),
    activationNotice: planMutationNotices[plan.plan_id],
  })
  const persistedPlans = tasksQuery.data?.plans || []
  const persistedPlanById = new Map(persistedPlans.map((plan) => [plan.plan_id, plan]))
  const resolvePlan = (plan: PlanSummary) => {
    const candidates = [plan, persistedPlanById.get(plan.plan_id), planOverrides[plan.plan_id]].filter((value): value is PlanSummary => Boolean(value))
    return candidates.reduce((latest, candidate) => candidate.revision > latest.revision ? candidate : latest)
  }
  const renderedPlanIds = new Set(items.filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan').map((item) => item.plan.plan_id))
  const persistedSessionPlans = persistedPlans.filter((plan) => plan.session_id === sessionId && !renderedPlanIds.has(plan.plan_id)).map(resolvePlan)
  const renderedSessionPlans = items
    .filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan')
    .map((item) => resolvePlan(item.plan))
  const dockedPlan = selectDockedPlan(renderedSessionPlans) ?? selectDockedPlan(persistedSessionPlans)
  const stopCurrentRun = async () => {
    if (dockedPlan?.status === 'running') {
      void commandPlanStatus(dockedPlan, 'pause')
    }
    if (!user || !effectiveRunId || stopping) return
    setStopping(true)
    const longTaskActive = ['running', 'pausing', 'cancelling'].includes(String(longTaskQuery.data?.long_task.status || ''))
    await executeStopRequest(
      () => longTaskActive && sessionId
        ? cancelSessionLongTask(user, sessionId)
        : cancelRun(user, effectiveRunId),
      (error) => {
        abortChatRun()
        const message = error instanceof Error
          ? `紧急停止请求失败：${error.message}`
          : '紧急停止请求失败，已断开当前响应'
        setLiveItems((current) => [
          ...finalizeCurrentRoundItems(current, {
            message: '紧急停止请求失败，当前响应已在前端断开',
            exception_type: 'StopRequestFailed',
            cancelled: true,
          }),
          { id: eventId('error'), kind: 'error', content: message },
        ])
      },
    )
  }
  const toggleLongTask = async () => {
    if (!user || !sessionId || longTaskBusy) return
    setLongTaskBusy(true)
    setConversationFeedback(null)
    try {
      const response = await setSessionLongTask(user, sessionId, !longTaskQuery.data?.long_task.enabled)
      queryClient.setQueryData(['long-task', user, sessionId], response)
    } catch (error) {
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '长任务开关更新失败' })
    } finally {
      setLongTaskBusy(false)
    }
  }
  const stopLongTask = async () => {
    if (!user || !sessionId || longTaskBusy) return
    setLongTaskBusy(true)
    try {
      const response = await cancelSessionLongTask(user, sessionId)
      queryClient.setQueryData(['long-task', user, sessionId], response)
      setStopping(true)
    } catch (error) {
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '停止长任务失败' })
    } finally {
      setLongTaskBusy(false)
    }
  }
  const revealPlan = (plan: PlanSummary) => {
    if (plan.plan_id !== dockedPlan?.plan_id) {
      navigate(`/tasks?user=${encodeURIComponent(user)}`)
      return
    }
    setCollapsedPlans((current) => {
      const next = new Set(current)
      next.delete(plan.plan_id)
      return next
    })
    window.requestAnimationFrame(() => composerPlanDockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }
  const userRoundCount = items.filter((item) => item.kind === 'message' && item.role === 'user').length
  const roundLimit = Math.max(1, Number(overview?.context.round_limit || 30))
  const reportedContextRound = Number(overview?.context.rounds)
  const currentRound = Math.max(
    1,
    Number.isFinite(reportedContextRound)
      ? reportedContextRound + (running ? 1 : 0)
      : Math.min(userRoundCount, roundLimit),
  )
  const totalRounds = Math.max(
    currentRound,
    Number(overview?.context.session_total_rounds || 0) + (running ? 1 : 0),
  )
  const loadEarlierHistory = async () => {
    const element = scrollRef.current
    if (!element || !historyQuery.hasNextPage || historyQuery.isFetchingNextPage || loadingEarlierRef.current) return
    loadingEarlierRef.current = true
    followOutputRef.current = false
    setShowFollowOutput(true)
    prependSnapshotRef.current = {
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    }
    const previousPageCount = historyQuery.data?.pages.length ?? 0
    try {
      const result = await historyQuery.fetchNextPage()
      if ((result.data?.pages.length ?? 0) === previousPageCount) {
        prependSnapshotRef.current = null
        loadingEarlierRef.current = false
      }
    } catch {
      prependSnapshotRef.current = null
      loadingEarlierRef.current = false
    }
  }
  const handleChatScroll = () => {
    const element = scrollRef.current
    if (!element) return
    if (element.scrollTop <= 120 && historyQuery.hasNextPage) {
      followOutputRef.current = false
      setShowFollowOutput(true)
      void loadEarlierHistory()
      return
    }
    const following = isNearScrollBottom(element)
    followOutputRef.current = following
    setShowFollowOutput(!following)
  }
  const jumpToUserMessage = (id: string) => {
    const element = scrollRef.current
    if (!element) return
    const target = Array.from(element.querySelectorAll<HTMLElement>('[data-user-message-id]'))
      .find((candidate) => candidate.dataset.userMessageId === id)
    if (!target) return
    followOutputRef.current = false
    setShowFollowOutput(true)
    const containerRect = element.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const top = targetRect.top - containerRect.top + element.scrollTop
      - Math.max(24, (element.clientHeight - targetRect.height) / 2)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    else element.scrollTop = Math.max(0, top)
  }
  const resumeFollowingOutput = () => {
    const element = scrollRef.current
    if (!element) return
    followOutputRef.current = true
    setShowFollowOutput(false)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
    else element.scrollTop = element.scrollHeight
  }
  const referenceKnowledge = (document: KnowledgeDocumentSummary) => {
    const referenceId = `${document.scope}:${document.relative_path}`
    const reference = `[知识库引用 ${referenceId}] ${document.title}`
    setDraft((current) => {
      if (current.includes(`[知识库引用 ${referenceId}]`)) return current
      const existing = current.trimEnd()
      return existing ? `${existing}\n${reference}` : reference
    })
    setKnowledgeDrawerOpen(false)
  }
  const referenceCapability = (item: CapabilityReferenceItem) => {
    const marker = capabilityReferenceMarker(item)
    const reference = capabilityReferenceLine(item)
    setDraft((current) => {
      if (current.includes(marker)) return current
      const existing = current.trimEnd()
      return existing ? `${existing}\n${reference}` : reference
    })
    setCapabilityDrawerOpen(false)
  }
  const conversationItems = archiveTerminalPlansInConversation(
    items.filter((item) => item.kind !== 'context_compression'),
    [
      ...persistedPlans
        .filter((plan) => plan.session_id === sessionId)
        .map(resolvePlan),
      ...renderedSessionPlans,
    ],
  )
  const conversationBlocks = groupConversationItems(conversationItems)
  const userMessageMarkers = buildUserMessageMarkers(conversationItems, historyData?.pagination?.first_round ?? 1)
  const showWelcome = items.length === 0 && !editingSource

  return (
    <div className={`view chat-view active${showWelcome ? ' welcome-mode' : ''}${conversationMenuOpen ? ' conversation-menu-open' : ''}`}>
      <div className="chat-scroll-stage">
        <div className="chat-scroll" ref={scrollRef} onScroll={handleChatScroll}>
        {showWelcome && (!sessionId || !historyQuery.isLoading) && (
          <section className="welcome">
            <div className="welcome-top">
              <article className="greeting-card">
                <div className="hero-logo"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent logo" /></div>
                <div className="greeting-copy">
                  <h1>{greetingLabel()}，{user || '用户'}</h1>
                  <p>当前用户的配置、历史、知识、任务与技能运行态已载入。今天需要处理什么？</p>
                  <span className="role-line">● 当前用户 · users/{user || '—'}</span>
                </div>
              </article>
              <article className="snapshot-card">
                <div className="snapshot-item"><strong>{overview?.counts.sessions ?? '—'} 个</strong><span>Web 会话</span></div>
                <div className="snapshot-item"><strong className="ok">{overview?.counts.knowledge_documents ?? '—'} 项</strong><span>文件知识</span></div>
                <div className="snapshot-item"><strong>{overview?.counts.enabled_tools ?? '—'} 个</strong><span>可用工具</span></div>
                <div className="snapshot-item"><strong>{overview?.counts.active_tasks ?? '—'} 个</strong><span>活动任务</span></div>
              </article>
            </div>
            {activePlan && !dockedPlan && <article className={`active-task-card ${activeTaskOpen ? 'open' : ''}`}>
              <div className="active-task-main">
                <span className="active-task-play"><ListChecks size={17} /></span>
                <span className="active-task-copy"><small>{statusLabel(activePlan.status)} · 当前用户 {user}</small><strong>{activePlan.title}</strong><span>{activePlan.description}</span></span>
                <span className="active-task-progress"><b>{activePlan.progress.percent}%</b><span className="progress-line"><i style={{ width: `${activePlan.progress.percent}%` }} /></span></span>
                <button className="task-inline-btn" onClick={() => setActiveTaskOpen((value) => !value)}>{activeTaskOpen ? '收起步骤' : '展开步骤'} <ChevronDown size={13} /></button>
                <button className="task-inline-btn primary" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}>任务中枢</button>
              </div>
              <div className="active-task-detail">{activePlan.steps.slice(0, 6).map((step, index) => <div className={`active-task-step ${step.status}`} key={step.step_id}><i>{step.status === 'completed' ? '✓' : index + 1}</i><span><strong>{step.title}</strong><small>{statusLabel(step.status)} · {step.description}</small></span></div>)}</div>
            </article>}
            <div className="quick-start">
              {quickStartCards.map(({ prompt, icon: Icon, title, desc, tone }) => (
                <button key={prompt} className={`quick-card quick-card-${tone}`} onClick={() => setDraft(prompt)}>
                  <span className="quick-icon"><Icon size={17} /></span>
                  <strong>{title}</strong>
                  <span>{desc}</span>
                </button>
              ))}
            </div>
            <RecentActivityCard
              className="welcome-recent-status"
              scheduledTasks={recentTasks}
              senseData={recentSenseData}
              onViewAllTasks={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}
              onViewAllSenseData={() => navigate(`/sense?user=${encodeURIComponent(user)}`)}
              onTaskClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}
              onSenseDataClick={() => navigate(`/sense?user=${encodeURIComponent(user)}`)}
            />
          </section>
        )}
        {historyQuery.isLoading && <div className="center-state">正在加载历史…</div>}
        {historyQuery.isError && sessionId && liveItems.length === 0 && <div className="center-state error">该会话尚无已提交历史，可以直接发送第一条消息。</div>}
        <div className={`messages ${items.length ? 'show' : ''}`}>
          {historyQuery.isFetchingNextPage ? <div className="history-page-status">正在加载更早对话…</div> : null}
          {historyQuery.hasNextPage && !historyQuery.isFetchingNextPage ? (
            <button className="history-page-button" type="button" onClick={() => { void loadEarlierHistory() }}>加载更早对话</button>
          ) : null}
          {!historyQuery.hasNextPage
            && (historyData?.pagination?.total_rounds ?? 0) > HISTORY_PAGE_SIZE
            ? <div className="history-page-status complete">已到达对话开头</div>
            : null}
          {items.length ? <div className="conversation-divider"><span>当前对话</span></div> : null}
          {conversationBlocks.map((block) => {
            if (block.kind === 'user') {
              const item = block.item
              return (
                <Fragment key={block.id}>
                  <article className="message user" data-user-message-id={item.id}>
                    <UserMessageAvatar avatarUrl={userAvatarUrl} />
                    <div className="message-body">
                      {item.content ? <div className="bubble"><PlainTextMessage content={item.content} /></div> : null}
                      {item.attachments?.length ? (
                        <div className="user-attachment-list">
                          {item.attachments.map((attachment, index) => (
                            <UserAttachmentCard key={attachment.asset_id || `${attachment.name}_${index}`} user={user} attachment={attachment} />
                          ))}
                        </div>
                      ) : null}
                      <div className="message-actions">
                        {item.edited ? <span className="edited-label">编辑后重发</span> : null}
                        {editedSources.has(item.id) ? <span className="edited-label">已用于重发</span> : null}
                        {!running && item.content && lastUserMessage?.id === item.id ? <button onClick={() => void editAndResend(item.id, item.content)} disabled={Boolean(conversationBusy)} aria-label="编辑后重发"><Pencil size={12} />{conversationBusy === 'edit' ? '正在撤销…' : '编辑重发'}</button> : null}
                        <button onClick={() => void copyMessage(item.id, item.content)} disabled={!item.content} aria-label="复制消息">{copiedItem === item.id ? <Check size={12} /> : <Copy size={12} />}{copiedItem === item.id ? '已复制' : '复制'}</button>
                      </div>
                    </div>
                  </article>
                </Fragment>
              )
            }

            const { assistantMessages, usageItems, planItems, finalizedGuidance } = partitionAssistantTurnItems(block.items)
            const assistantText = assistantMessages.map((item) => item.content).filter(Boolean).join('\n\n')
            const assistantCopyId = assistantMessages.at(-1)?.id || block.id
            const hasPlanBubble = block.items.some((item) => item.kind === 'task_plan')
            return (
              <article key={block.id} className="assistant-turn">
                <div className="msg-avatar assistant-turn-avatar"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent" /></div>
                <div className="assistant-turn-content">
                  {block.items.map((item) => {
                    if (item.kind === 'context_compression') return null
                    if (item.kind === 'long_task_boundary') return <div className="long-task-boundary" key={item.id}>长任务自动续跑 · 第 {item.continuation + 1} Run</div>
                    if (item.kind === 'reasoning') return <ReasoningTrace key={item.id} item={item} />
                    if (item.kind === 'execution_marker') return null
                    if (item.kind === 'tool') return <ToolCallCard key={item.id} item={item} />
                    if (item.kind === 'media') return <MediaArtifactCard key={item.id} user={user} artifact={item.artifact} />
                    if (item.kind === 'usage') return null
                    if (item.kind === 'task_plan') return null
                    if (item.kind === 'guidance') return null
                    if (item.kind === 'error') return <div key={item.id} className="chat-error">{item.content}</div>
                    if (item.role !== 'assistant') return null
                    return (
                      <div key={item.id} className="assistant-response">
                        <div className="bubble">
                          <Suspense fallback={<PlainTextMessage content={compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)} />}>
                            <MarkdownMessage
                              content={compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)}
                              streaming={Boolean(item.streaming)}
                            />
                          </Suspense>
                        </div>
                      </div>
                    )
                  })}
                  {(usageItems.length > 0 || assistantMessages.length > 0) && (
                    <div className="assistant-turn-footer">
                      <div className="assistant-turn-usage">{usageItems.map((item) => <UsageCard key={item.id} item={item} />)}</div>
                      {assistantMessages.length > 0 && (
                        <button className="assistant-turn-copy" onClick={() => void copyMessage(assistantCopyId, assistantText)} disabled={!assistantText} aria-label="复制智能体回复">
                          {copiedItem === assistantCopyId ? <Check size={13} /> : <Copy size={13} />}{copiedItem === assistantCopyId ? '已复制' : '复制'}
                        </button>
                      )}
                    </div>
                  )}
                  {planItems.map((item) => {
                    const plan = resolvePlan(item.plan)
                    return <TaskPlanRecord key={item.id} plan={plan} docked={plan.plan_id === dockedPlan?.plan_id} onOpen={() => revealPlan(plan)} />
                  })}
                  {finalizedGuidance.length > 0 && <div className="assistant-guidance-list">{finalizedGuidance.map((item) => <GuidanceMessage key={item.id} user={user} item={item} placement="completed" />)}</div>}
                </div>
              </article>
            )
          })}
        </div>
        {showFollowOutput && items.length > 0 ? <button className="chat-follow-output" type="button" onClick={resumeFollowingOutput}><ChevronDown size={15} />继续跟随最新回复</button> : null}
        </div>
        <UserMessageNavigator
          markers={userMessageMarkers}
          scrollContainerRef={scrollRef}
          totalRounds={Math.max(totalRounds, historyData?.pagination?.total_rounds ?? 0)}
          hasEarlierMessages={Boolean(historyQuery.hasNextPage)}
          loadingEarlierMessages={historyQuery.isFetchingNextPage}
          onLoadEarlierMessages={loadEarlierHistory}
          onNavigate={jumpToUserMessage}
        />
      </div>
      <div className="composer-zone">
        {runRetryNotice ? (
          <div className="run-retry-bubble" role="status" aria-live="polite">
            <span>运行出现问题，正在自动重试（第 {runRetryNotice.nextAttempt}/{runRetryNotice.maxAttempts} 次）</span>
          </div>
        ) : null}
        {runErrorNotice ? (
          <div className="run-error-bubble" role="alert">
            <span>{runErrorNotice.message}</span>
            <button type="button" onClick={() => setRunErrorNotice(null)} aria-label="关闭运行错误提示">×</button>
          </div>
        ) : null}
        {running && activeCompression ? <ContextCompressionBubble item={activeCompression} /> : null}
        {longTaskQuery.data?.long_task && shouldShowLongTaskBubble(longTaskQuery.data.long_task.status) ? (
          <LongTaskBubble state={longTaskQuery.data.long_task} stopping={longTaskBusy} onCancel={() => { void stopLongTask() }} />
        ) : null}
        {dockedPlan ? (
          <div className="composer-plan-dock" ref={composerPlanDockRef}>
            <TaskPlanBubble
              {...taskPlanFromSummary(dockedPlan)}
              collapsed={collapsedPlans.has(dockedPlan.plan_id)}
              {...planActions(dockedPlan)}
            />
          </div>
        ) : null}
        {guidancePreviewItem ? <div className="composer-guidance-preview" aria-live="polite"><GuidanceMessage user={user} item={guidancePreviewItem} placement="current" onCancel={pendingNextTurn?.status === 'error' && liveSessionId ? () => removeNextTurnMessage(user, liveSessionId, pendingNextTurn.id) : undefined} onRetry={pendingNextTurn?.status === 'error' && liveSessionId ? () => setNextTurnMessageStatus(user, liveSessionId, pendingNextTurn.id, 'queued') : undefined} /></div> : null}
        <AgentComposer
          value={draft}
          placeholder={user ? stopping ? '输入下一轮消息；将在当前任务停止后自动发送…' : running ? '输入运行中引导；将在下一个 Provider/工具边界生效…' : '给 kemo-agent 发送消息…' : '请先选择用户'}
          currentRound={currentRound}
          totalRounds={totalRounds}
          roundLimit={roundLimit}
          running={running}
          stopping={stopping}
          disabled={!user}
          conversationMenuOpen={conversationMenuOpen}
          pendingFileCount={pendingUploads.length}
          uploading={uploading}
          uploadFeedback={<>
            {uploadFeedback ? <div className={`upload-feedback ${uploadFeedback.tone}`} role="status"><span>{uploadFeedback.text}</span><button type="button" onClick={() => setUploadFeedback(null)} aria-label="关闭上传提示">×</button></div> : null}
            {pendingUploads.length ? <PendingAttachmentTray user={user} files={pendingUploads} onRemove={(index) => setPendingUploads((current) => current.filter((_, currentIndex) => currentIndex !== index))} /> : null}
          </>}
          notice={editingSource ? <div className="edit-resend-banner"><span>最新一轮已撤销；修改内容后发送将创建新的最新一轮。</span><button onClick={cancelEditAndResend}>取消编辑</button></div> : null}
          conversationMenu={conversationMenuOpen ? (
            <div className="conversation-menu show" role="menu">
              <div className="conversation-menu-head">对话操作</div>
              <button className="conversation-action" role="menuitem" disabled={running || Boolean(conversationBusy)} onClick={() => { void saveAndNewConversation() }}>
                <span className="conversation-action-icon"><Save size={16} /></span>
                  <span className="conversation-action-copy"><strong>保存此对话，创建新对话</strong><span>{conversationBusy === 'save' ? '正在保存归档并切换…' : '保留当前归档，记忆转入后台提取'}</span></span>
              </button>
              <button className="conversation-action danger" role="menuitem" disabled={running || Boolean(conversationBusy)} onClick={() => { void clearConversation() }}>
                <span className="conversation-action-icon"><Trash2 size={16} /></span>
                <span className="conversation-action-copy"><strong>清空此对话</strong><span>{conversationBusy === 'clear' ? '正在删除当前归档…' : '删除当前归档并创建新对话'}</span></span>
              </button>
              <button className="conversation-action compress" role="menuitem" disabled={running || Boolean(conversationBusy) || !sessionId || !hasCommitted} onClick={() => { void compressCurrentConversation() }}>
                <span className="conversation-action-icon"><Zap size={16} /></span>
                <span className="conversation-action-copy"><strong>手动进行一次上下文压缩</strong><span>{conversationBusy === 'compress' ? '正在压缩并提取记忆…' : '整理当前上下文并同步提取待处理记忆'}</span></span>
              </button>
              <button className="conversation-action long-task-action" role="menuitemcheckbox" aria-checked={Boolean(longTaskQuery.data?.long_task.enabled)} disabled={!sessionId || longTaskBusy || longTaskQuery.isLoading} onClick={() => { void toggleLongTask() }}>
                <span className="conversation-action-icon"><Workflow size={16} /></span>
                <span className="conversation-action-copy"><strong>长任务模式</strong><span>{longTaskQuery.data?.long_task.status === 'running' ? '正在跨 Run 执行；关闭后会在当前 Run 收束时停止续跑' : longTaskQuery.data?.long_task.enabled ? '已允许；达到单轮工具上限后自动续跑' : '允许当前对话达到单轮工具上限后自动续跑'}</span></span>
                <span className={`conversation-switch ${longTaskQuery.data?.long_task.enabled ? 'on' : ''}`} aria-hidden="true"><i /></span>
              </button>
              <button className="conversation-action" role="menuitem" disabled={running || Boolean(conversationBusy) || !lastUserMessage} onClick={() => void regenerateLastResponse()}>
                <span className="conversation-action-icon"><RotateCcw size={16} /></span>
                <span className="conversation-action-copy"><strong>重新发送一次消息</strong><span>撤销上一轮后重放原消息，不增加对话轮数</span></span>
              </button>
              {conversationFeedback ? <div className={`conversation-menu-status ${conversationFeedback.tone}`} role="status">{conversationFeedback.text}</div> : null}
              <div className="conversation-menu-foot">再次打开网页会恢复上次活跃对话；点击“保存并创建新对话”才会关闭并切换会话。</div>
            </div>
          ) : null}
          onChange={setDraft}
          onUploadFiles={uploadFiles}
          onOpenKnowledge={() => {
            setCapabilityDrawerOpen(false)
            setKnowledgeDrawerOpen(true)
          }}
          onOpenCapabilities={() => {
            setKnowledgeDrawerOpen(false)
            setCapabilityDrawerOpen(true)
          }}
          onOpenCommands={openCommandPanel}
          onToggleConversationMenu={() => setConversationMenuOpen((value) => !value)}
          onSubmit={() => { if (running) void sendGuidance(); else void send(undefined, { uploadedFiles: pendingUploads }) }}
          onStop={() => { void stopCurrentRun() }}
        />
      </div>
      <KnowledgeReferenceDrawer
        open={knowledgeDrawerOpen}
        documents={knowledgeQuery.data?.documents ?? []}
        loading={knowledgeQuery.isLoading || knowledgeQuery.isFetching}
        error={knowledgeQuery.isError}
        onClose={() => setKnowledgeDrawerOpen(false)}
        onReference={referenceKnowledge}
      />
      <CapabilityReferenceDrawer
        open={capabilityDrawerOpen}
        items={capabilityItems}
        loading={{
          expand: expandsQuery.isLoading || expandsQuery.isFetching,
          skill: skillsQuery.isLoading || skillsQuery.isFetching,
          plugin: skillsQuery.isLoading || skillsQuery.isFetching,
        }}
        error={{
          expand: expandsQuery.isError,
          skill: skillsQuery.isError,
          plugin: skillsQuery.isError,
        }}
        onClose={() => setCapabilityDrawerOpen(false)}
        onReference={referenceCapability}
      />
    </div>
  )
}
