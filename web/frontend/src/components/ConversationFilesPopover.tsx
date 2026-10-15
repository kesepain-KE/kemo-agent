import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useQuery } from '@tanstack/react-query'
import { Download, File, FileImage, FileMusic, FileVideo, FolderOpen, Quote } from 'lucide-react'
import { getHistory } from '../api/client'
import { mediaArtifactUrl } from '../pages/chatPresentation'
import { getUserAttachmentThumbnailUrl, getUserFileDownloadUrl } from '../api/client'
import { chatDraftKey, EMPTY_CHAT_DRAFT, useChatDraftStore, type PendingUploadedFile } from '../store/chatDrafts'
import type { ChatItem, HistoryResponse, InputAttachment, MediaArtifact } from '../types/api'
import type { ChatRunSnapshot } from './appShellTypes'
import styles from './ConversationFilesPopover.module.css'

interface ConversationFilesPopoverProps {
  user: string
  sessionId: string
  liveRun?: ChatRunSnapshot
}

interface FileEntry {
  id: string
  name: string
  kind: 'image' | 'audio' | 'video' | 'file'
  mimeType?: string
  size?: number
  thumbnailUrl?: string
  href?: string
  downloadName?: string
  reference?: PendingUploadedFile
}

function attachmentEntry(user: string, attachment: InputAttachment): FileEntry {
  const image = attachment.media_kind === 'image'
  return {
    id: `input:${attachment.asset_id || attachment.relative_path}:${attachment.checksum_sha256}`,
    name: attachment.name,
    kind: attachment.media_kind,
    mimeType: attachment.mime_type,
    size: attachment.size,
    thumbnailUrl: image && attachment.checksum_sha256
      ? getUserAttachmentThumbnailUrl(user, attachment.checksum_sha256, attachment.relative_path)
      : undefined,
    href: attachment.available && attachment.scope === 'file_upload' && attachment.relative_path
      ? getUserFileDownloadUrl(user, 'file_upload', attachment.relative_path)
      : undefined,
    downloadName: attachment.name,
    reference: attachment.available && attachment.scope === 'file_upload' && attachment.relative_path
      ? {
          path: attachment.relative_path,
          name: attachment.name,
          size: attachment.size,
          mimeType: attachment.mime_type,
          mediaKind: attachment.media_kind,
          checksumSha256: attachment.checksum_sha256 || undefined,
        }
      : undefined,
  }
}

function pendingEntry(user: string, file: PendingUploadedFile): FileEntry {
  const image = file.mediaKind === 'image' || file.mimeType?.startsWith('image/')
  return {
    id: `pending:${file.checksumSha256 || file.path}`,
    name: file.name,
    kind: file.mediaKind || (image ? 'image' : 'file'),
    mimeType: file.mimeType,
    size: file.size,
    thumbnailUrl: image && file.checksumSha256
      ? getUserAttachmentThumbnailUrl(user, file.checksumSha256, file.path)
      : undefined,
    href: file.path ? getUserFileDownloadUrl(user, 'file_upload', file.path) : undefined,
    downloadName: file.name,
    reference: file,
  }
}

function artifactEntry(user: string, artifact: MediaArtifact): FileEntry {
  return {
    id: `output:${artifact.asset_id || artifact.path}:${artifact.checksum_sha256}`,
    name: artifact.name,
    kind: artifact.type === 'image' || artifact.type === 'audio' || artifact.type === 'video' ? artifact.type : 'file',
    mimeType: artifact.mime_type,
    size: artifact.size,
    thumbnailUrl: artifact.type === 'image' ? mediaArtifactUrl(user, artifact) : undefined,
    href: artifact.path ? mediaArtifactUrl(user, artifact) : undefined,
    downloadName: artifact.name,
  }
}

function collectHistoryFiles(user: string, history: HistoryResponse | undefined) {
  const sources = new Map<string, FileEntry>()
  const outputs = new Map<string, FileEntry>()
  for (const message of history?.messages ?? []) {
    for (const attachment of message.attachments ?? []) {
      const entry = attachmentEntry(user, attachment)
      sources.set(entry.id, entry)
    }
  }
  for (const metric of history?.round_metrics ?? []) {
    for (const attachment of metric.guidance_details?.flatMap((item) => item.uploaded_files ?? []) ?? []) {
      const entry = attachmentEntry(user, attachment)
      sources.set(entry.id, entry)
    }
    for (const artifact of metric.artifacts ?? []) {
      const entry = artifactEntry(user, artifact)
      outputs.set(entry.id, entry)
    }
  }
  for (const trace of history?.round_traces ?? []) {
    for (const tool of trace.tools ?? []) {
      for (const artifact of tool.artifacts ?? []) {
        const entry = artifactEntry(user, artifact)
        outputs.set(entry.id, entry)
      }
    }
  }
  return { sources: [...sources.values()], outputs: [...outputs.values()] }
}

function collectLiveFiles(user: string, liveRun: ChatRunSnapshot | undefined) {
  const sources = new Map<string, FileEntry>()
  const outputs = new Map<string, FileEntry>()
  const addItem = (item: ChatItem) => {
    if (item.kind === 'media') {
      const entry = artifactEntry(user, item.artifact)
      outputs.set(entry.id, entry)
    } else if (item.kind === 'message') {
      for (const attachment of item.attachments ?? []) {
        const entry = attachmentEntry(user, attachment)
        sources.set(entry.id, entry)
      }
    } else if (item.kind === 'guidance') {
      for (const attachment of item.attachments ?? []) {
        const entry = attachmentEntry(user, attachment)
        sources.set(entry.id, entry)
      }
    }
  }
  for (const item of liveRun?.items ?? []) addItem(item)
  for (const queued of liveRun?.nextTurnQueue ?? []) {
    for (const file of queued.uploadedFiles ?? []) {
      const entry = pendingEntry(user, file)
      sources.set(entry.id, entry)
    }
  }
  return { sources: [...sources.values()], outputs: [...outputs.values()] }
}

function fileIcon(kind: FileEntry['kind']) {
  if (kind === 'image') return <FileImage size={17} />
  if (kind === 'audio') return <FileMusic size={17} />
  if (kind === 'video') return <FileVideo size={17} />
  return <File size={17} />
}

function formatSize(size?: number) {
  if (!Number.isFinite(size) || !size || size < 0) return ''
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / 1024 / 1024).toFixed(1)} MB`
}

function FileEntryRow({ entry, onReference }: { entry: FileEntry; onReference?: (file: PendingUploadedFile) => void }) {
  const [thumbnailFailed, setThumbnailFailed] = useState(false)
  const image = entry.kind === 'image' && entry.thumbnailUrl && !thumbnailFailed
  return (
    <div className={styles.entry}>
      {image
        ? <span className={styles.preview}><img src={entry.thumbnailUrl} alt="" onError={() => setThumbnailFailed(true)} /></span>
        : <span className={styles.fileIcon} aria-hidden="true">{fileIcon(entry.kind)}</span>}
      <span className={styles.copy}>
        <strong title={entry.name}>{entry.name}</strong>
        <small>{[entry.mimeType, formatSize(entry.size)].filter(Boolean).join(' · ') || '当前对话文件'}</small>
      </span>
      {entry.reference && onReference
        ? <button type="button" className={styles.entryAction} onClick={() => onReference(entry.reference!)} aria-label={`引用 ${entry.name}`} title="引用到输入框"><Quote size={14} /></button>
        : null}
      {entry.href
        ? <a className={styles.entryAction} href={entry.href} download={entry.downloadName} aria-label={`下载 ${entry.name}`} title="下载"><Download size={14} /></a>
        : null}
    </div>
  )
}

export function ConversationFilesPopover({ user, sessionId, liveRun }: ConversationFilesPopoverProps) {
  const [open, setOpen] = useState(false)
  const refreshMarkerRef = useRef('')
  const draftKey = chatDraftKey(user, sessionId)
  const draftSnapshot = useChatDraftStore((state) => state.drafts[draftKey] ?? EMPTY_CHAT_DRAFT)
  const setPendingUploads = useChatDraftStore((state) => state.setPendingUploads)
  const pendingUploads = draftSnapshot.pendingUploads
  const historyQuery = useQuery({
    queryKey: ['conversation-files', user, sessionId],
    queryFn: () => getHistory(user, sessionId, { source: 'web' }),
    enabled: Boolean(open && user && sessionId),
    staleTime: 0,
    refetchOnWindowFocus: false,
  })

  useEffect(() => {
    if (!open || !sessionId) {
      refreshMarkerRef.current = ''
      return
    }
    const marker = `${sessionId}:${liveRun?.phase || 'idle'}:${liveRun?.runId || ''}`
    if (!refreshMarkerRef.current) {
      refreshMarkerRef.current = marker
      return
    }
    if (refreshMarkerRef.current === marker) return
    refreshMarkerRef.current = marker
    // A running session can add artifacts after the history query was opened.
    void historyQuery.refetch()
  // historyQuery is stable for this component instance; the marker limits refetches to run changes.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, sessionId, liveRun?.phase, liveRun?.runId])

  const files = useMemo(() => {
    const history = collectHistoryFiles(user, historyQuery.data)
    const live = collectLiveFiles(user, liveRun)
    const sources = new Map(history.sources.map((entry) => [entry.id, entry]))
    const outputs = new Map(history.outputs.map((entry) => [entry.id, entry]))
    for (const entry of live.sources) sources.set(entry.id, entry)
    for (const entry of live.outputs) outputs.set(entry.id, entry)
    for (const file of pendingUploads) {
      const entry = pendingEntry(user, file)
      sources.set(entry.id, entry)
    }
    return { sources: [...sources.values()], outputs: [...outputs.values()] }
  }, [historyQuery.data, liveRun, pendingUploads, user])
  const count = files.sources.length + files.outputs.length
  const referenceFile = (file: PendingUploadedFile) => {
    setPendingUploads(draftKey, (current) => current.some((item) => item.path === file.path) ? current : [...current, file])
  }

  if (!sessionId) return null
  return (
    <div className={styles.anchor}>
      <button
        type="button"
        className={styles.trigger}
        aria-label="当前对话文件历史"
        aria-expanded={open}
        title="当前对话文件历史"
        onClick={() => setOpen((value) => !value)}
      >
        <FolderOpen size={16} />
      </button>
      {open && createPortal(
        <aside
          id="conversation-files-panel"
          className={styles.panel}
          role="region"
          aria-label="当前对话文件历史"
        >
          <div className={styles.head}>
            <span className={styles.headCopy}><strong>相关文件</strong></span>
          </div>
          <div className={styles.body}>
            {historyQuery.isPending && !historyQuery.data ? <div className={styles.loading}>正在读取当前对话文件…</div> : null}
            {historyQuery.isError ? <div className={styles.error}>文件历史读取失败<button type="button" className={styles.retry} onClick={() => { void historyQuery.refetch() }}>重试</button></div> : null}
            {!historyQuery.isPending && !historyQuery.isError && count === 0 ? <div className={styles.empty}>此对话还没有关联文件</div> : null}
            {files.outputs.length > 0 ? <section className={styles.section}><div className={styles.sectionHead}><span>输出内容</span><small>{files.outputs.length}</small></div><div className={styles.list}>{files.outputs.map((entry) => <FileEntryRow key={entry.id} entry={entry} />)}</div></section> : null}
            {files.sources.length > 0 ? <section className={styles.section}><div className={styles.sectionHead}><span>来源</span><small>{files.sources.length}</small></div><div className={styles.list}>{files.sources.map((entry) => <FileEntryRow key={entry.id} entry={entry} onReference={referenceFile} />)}</div></section> : null}
          </div>
        </aside>,
        document.body,
      )}
    </div>
  )
}
