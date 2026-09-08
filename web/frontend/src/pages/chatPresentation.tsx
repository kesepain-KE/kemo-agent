import { useEffect, useState } from 'react'
import { Activity, BrainCircuit, Download, File as FileIcon, FileX2, Image as ImageIcon, ListChecks, Music2, Shapes, TimerReset, Trash2, UserRound, Video, X } from 'lucide-react'
import { getUserArtifactUrl, getUserAttachmentThumbnailUrl, getUserFileDownloadUrl, getUserFilePreviewUrl } from '../api/client'
import { formatBytes, formatDateTime, statusLabel } from '../components/ModuleUi'
import type { ScheduledTaskItem, SenseDataItem } from '../components/RecentActivityCard'
import type { ChatItem, CronTaskSummary, InputAttachment, MediaArtifact, PlanSummary, SenseSourceSummary } from '../types/api'
import type { PendingUploadedFile } from '../store/chatDrafts'

export function UserMessageAvatar({ avatarUrl }: { avatarUrl?: string }) {
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

export function MediaArtifactCard({ user, artifact }: { user: string; artifact: MediaArtifact }) {
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

export function UserAttachmentCard({ user, attachment }: { user: string; attachment: InputAttachment }) {
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

export function PendingAttachmentCard({
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

export function PendingAttachmentTray({
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

export const quickStartCards = [
  { prompt: '查询 kemo-agent 当前感知情况', icon: BrainCircuit, title: '查询感知情况', desc: '查看感知来源、采集数据与当前注入状态', tone: 'sense' },
  { prompt: '查询 kemo-agent 当前拓展情况', icon: Shapes, title: '查询拓展情况', desc: '查看拓展模块、采集能力与注入状态', tone: 'expand' },
  { prompt: '查询 kemo-agent 当前运行状态', icon: Activity, title: '查询运行状态', desc: '汇总核心模块、Provider 与外接服务状态', tone: 'status' },
  { prompt: '为当前用户创建一个定时任务', icon: TimerReset, title: '创建定时任务', desc: '通过对话描述时间、内容与执行目标', tone: 'timer' },
]

export function greetingLabel() {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

export function TaskPlanRecord({ plan, docked, onOpen }: { plan: PlanSummary; docked: boolean; onOpen: () => void }) {
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

function senseIconFor(source: SenseSourceSummary): SenseDataItem['icon'] {
  const text = `${source.name} ${source.display_name}`.toLowerCase()
  if (text.includes('温度') || text.includes('temperature')) return 'temperature'
  if (text.includes('湿度') || text.includes('humidity')) return 'humidity'
  if (text.includes('天气') || text.includes('weather')) return 'weather'
  return 'radio'
}

export type GuidanceItem = Extract<ChatItem, { kind: 'guidance' }>
export type GuidanceDisplayItem = GuidanceItem | {
  id: string
  kind: 'guidance'
  content: string
  guidanceId?: string
  attachments?: InputAttachment[]
  status: 'next_turn' | 'next_turn_error'
}

export function GuidanceMessage({ user, item, placement, onRetry, onCancel }: { user: string; item: GuidanceDisplayItem; placement: 'current' | 'completed'; onRetry?: () => void; onCancel?: () => void }) {
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
