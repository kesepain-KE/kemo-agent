import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import * as api from '../api/client'
import type { FileListEntry } from '../types/api'
import { DocumentMarkdown } from '../components/DocumentMarkdown'
import { formatBytes, formatDateTime } from '../components/ModuleUi'
import { copyText } from '../utils/clipboard'
import { ReferenceIcon } from './ReferenceIcon'
import { toast } from './runtime.js'

export type FileArea = 'file_upload' | 'download' | 'tmp'
const PdfDocumentPreview = lazy(() => import('./PdfDocumentPreview'))
const OFFICE = new Set(['doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'odt', 'ods', 'odp', 'rtf'])
const TEXT = new Set(['txt', 'md', 'markdown', 'json', 'jsonl', 'ndjson', 'csv', 'tsv', 'log',
  'py', 'js', 'jsx', 'ts', 'tsx', 'css', 'html', 'yaml', 'yml', 'toml', 'xml', 'ini', 'cfg',
  'sh', 'ps1', 'bat', 'sql', 'c', 'cpp', 'h', 'java', 'rs', 'go'])
export const fileExtension = (file: FileListEntry) =>
  (file.extension || file.name.split('.').pop() || '').toLowerCase().replace(/^\./, '')

export function fileKind(file: FileListEntry) {
  const ext = fileExtension(file)
  if (['jpg', 'jpeg', 'png', 'gif', 'webp', 'avif', 'bmp', 'ico', 'svg'].includes(ext)) return 'image'
  if (['mp3', 'wav', 'ogg', 'm4a', 'aac', 'flac', 'opus', 'weba'].includes(ext)) return 'audio'
  if (['mp4', 'webm', 'ogv', 'mov', 'm4v'].includes(ext)) return 'video'
  if (OFFICE.has(ext) || ext === 'pdf') return 'document'
  if (TEXT.has(ext)) return 'text'
  return 'file'
}

function textDocument(text: string, ext: string) {
  if (ext === 'md' || ext === 'markdown') return text
  let body = text
  if (ext === 'json') {
    try { body = JSON.stringify(JSON.parse(text), null, 2) } catch { /* Invalid JSON remains visible verbatim. */ }
  }
  let fenceLength = 3
  for (const match of body.matchAll(/`+/g)) fenceLength = Math.max(fenceLength, match[0].length + 1)
  const fence = '`'.repeat(fenceLength)
  const language = ({ txt: 'text', log: 'text', py: 'python', js: 'javascript', ts: 'typescript',
    sh: 'bash', ps1: 'powershell', yml: 'yaml', csv: 'text', tsv: 'text', markdown: 'markdown' } as Record<string, string>)[ext] || ext
  return `${fence}${language}\n${body}\n${fence}`
}

/** One bounded fetch shared by the inline and expanded preview; no third-party viewers. */
export function FilePreview({ file, user, area, onClose }: {
  file: FileListEntry; user: string; area: FileArea; onClose: () => void
}) {
  const media = fileKind(file)
  const ext = fileExtension(file)
  const url = area === 'tmp' ? api.getTmpFilePreviewUrl(file.relative_path)
    : api.getUserFilePreviewUrl(user, area, file.relative_path)
  const download = area === 'tmp' ? api.getTmpFileDownloadUrl(file.relative_path) : api.getUserFileDownloadUrl(user, area, file.relative_path)
  const [text, setText] = useState<string>()
  const [pdf, setPdf] = useState('')
  const [error, setError] = useState('')
  const [expanded, setExpanded] = useState(false)
  const [retry, setRetry] = useState(0)
  const enlargeRef = useRef<HTMLButtonElement>(null)
  const dialogRef = useRef<HTMLDivElement>(null)
  const max = media === 'text' ? 4 * 1024 * 1024 : media === 'image' ? 10 * 1024 * 1024
    : media === 'audio' ? 100 * 1024 * 1024 : media === 'video' ? 300 * 1024 * 1024
      : OFFICE.has(ext) ? 32 * 1024 * 1024 : 64 * 1024 * 1024

  useEffect(() => {
    const controller = new AbortController()
    let blobUrl = ''
    setError(''); setText(undefined); setPdf('')
    if (file.size > max) {
      setError(`此文件超过预览上限（${formatBytes(max)}），请下载原文件查看。`)
    } else if (media === 'text' || media === 'document') {
      void (async () => {
        const response = await fetch(url, { credentials: 'same-origin', cache: 'no-cache', signal: controller.signal })
        if (!response.ok) {
          let message = `预览失败（HTTP ${response.status}）`
          try { message = (await response.json()).error?.message || message } catch { /* Non-JSON error. */ }
          throw new Error(message)
        }
        const limit = media === 'text' ? max : 64 * 1024 * 1024
        const reader = response.body?.getReader()
        if (!reader) throw new Error('预览响应没有文件内容')
        const chunks: Uint8Array<ArrayBuffer>[] = []
        let size = 0
        while (true) {
          const chunk = await reader.read()
          if (chunk.done) break
          size += chunk.value.length
          if (size > limit) { await reader.cancel(); throw new Error('预览内容过大，请下载原文件查看') }
          chunks.push(new Uint8Array(chunk.value))
        }
        if (controller.signal.aborted) return
        const blob = new Blob(chunks, { type: media === 'text' ? 'text/plain' : 'application/pdf' })
        if (media === 'text') {
          const value = await blob.text()
          if (!controller.signal.aborted) setText(value)
        }
        else {
          if (!(await blob.slice(0, 5).text()).startsWith('%PDF-')) throw new Error('服务未返回有效 PDF 预览')
          if (controller.signal.aborted) return
          blobUrl = URL.createObjectURL(blob)
          setPdf(blobUrl)
        }
      })().catch(cause => { if (!controller.signal.aborted) setError(cause instanceof Error ? cause.message : '预览失败') })
    }
    return () => { controller.abort(); if (blobUrl) URL.revokeObjectURL(blobUrl) }
  }, [file, url, media, max, retry])

  useEffect(() => {
    if (!expanded) return
    const previous = document.activeElement as HTMLElement | null
    const closeButton = dialogRef.current?.querySelector<HTMLButtonElement>('[aria-label="关闭放大预览"]')
    closeButton?.focus()
    const key = (event: KeyboardEvent) => {
      if (event.key === 'Escape') { event.stopPropagation(); setExpanded(false) }
      if (event.key !== 'Tab') return
      const controls = [...(dialogRef.current?.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],iframe,[tabindex="0"]') || [])]
      const first = controls[0], last = controls.at(-1)
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus() }
    }
    document.addEventListener('keydown', key)
    return () => { document.removeEventListener('keydown', key); previous?.isConnected && previous.focus() }
  }, [expanded])

  const content = () => error ? <div className="empty" role="alert"><ReferenceIcon name="alertTriangle" />
    <div className="empty-title">无法预览</div><div className="empty-desc">{error}</div>
    <button className="btn btn-outline btn-sm" onClick={() => setRetry(retry + 1)}>重试预览</button></div>
    : media === 'image' ? <img key={retry} src={url} alt={file.name} onError={() => setError('图片加载失败或格式不受浏览器支持，请下载查看')} />
      : media === 'audio' ? <div className="file-audio-preview"><ReferenceIcon name="audio" /><audio key={retry} controls preload="metadata" src={url} onError={() => setError('音频加载失败或编码不受浏览器支持，请下载查看')} /></div>
        : media === 'video' ? <video key={retry} controls preload="metadata" src={url} onError={() => setError('视频加载失败或编码不受浏览器支持，请下载查看')} />
          : media === 'document' ? pdf
            ? <Suspense fallback={<div className="reference-loading" role="status"><span className="spinner" />正在渲染文档…</div>}>
              <PdfDocumentPreview url={pdf} name={file.name} /></Suspense>
            : <div className="reference-loading" role="status"><span className="spinner" />{OFFICE.has(ext) ? '正在生成文档预览…' : '正在读取 PDF…'}</div>
            : media === 'text' ? text !== undefined
              ? <DocumentMarkdown content={textDocument(text, ext)} />
              : <div className="reference-loading" role="status"><span className="spinner" />正在读取文件…</div>
              : <div className="empty"><ReferenceIcon name="file" /><div className="empty-title">此格式暂不支持预览</div><div className="empty-desc">下载后使用本地应用查看。</div></div>
  const footer = <><button className="btn btn-ghost btn-sm" onClick={() => void copyText(file.relative_path)
    .then(() => toast('已复制相对路径', { kind: 'success' })).catch(() => toast('复制失败', { kind: 'error' }))}>
    <ReferenceIcon name="copy" small />复制路径</button><div className="spacer" />
    <a className="btn btn-outline btn-sm" href={download} download={file.name}><ReferenceIcon name="download" small />下载</a></>

  return <section className="editor-panel file-detail-panel" aria-label="文件详情">
    <div className="editor-head"><div className="reference-entry-copy"><div className="editor-title">{file.name}</div>
      <div className="file-path">{file.relative_path}</div></div>
      <button ref={enlargeRef} className="icon-btn" aria-label="放大预览" onClick={() => setExpanded(true)}><ReferenceIcon name="maximize" /></button>
      <button className="icon-btn" aria-label="关闭文件详情" onClick={onClose}><ReferenceIcon name="close" /></button></div>
    <div className="file-detail-meta"><span>{ext.toUpperCase() || 'FILE'} · {formatBytes(file.size)}</span><span>{formatDateTime(file.updated_at)}</span></div>
    <div className="file-detail-body reference-file-preview">{expanded
      ? <div className="empty"><ReferenceIcon name="maximize" /><div className="empty-desc">正在放大预览</div></div> : content()}</div>
    <div className="editor-foot">{footer}</div>
    {expanded && createPortal(<div className="overlay overlay-center file-preview-overlay" role="dialog" aria-modal="true"
      aria-label={`预览 ${file.name}`} ref={dialogRef} onMouseDown={event => { if (event.target === event.currentTarget) setExpanded(false) }}>
      <div className="modal modal-xl"><div className="modal-head"><div className="reference-entry-copy"><div className="modal-title">{file.name}</div>
        <div className="modal-sub">{file.relative_path} · {formatBytes(file.size)}</div></div>
        <button className="icon-btn" aria-label="关闭放大预览" onClick={() => setExpanded(false)}><ReferenceIcon name="close" /></button></div>
        <div className="modal-body reference-file-preview">{content()}</div><div className="modal-foot">{footer}</div></div>
    </div>, document.body)}
  </section>
}
