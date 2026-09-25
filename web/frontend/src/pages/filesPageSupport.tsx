import { useEffect, useId, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Archive, Braces, Check, ChevronDown, Download, File, FileAudio, FileImage, FileText, FileVideo, Folder, Gauge, Info, EllipsisVertical, Pause, Play, Volume2, VolumeX, X } from 'lucide-react'
import type { FileListEntry, FileSortBy } from '../types/api'
import styles from './FilesPage.module.css'

export type FileArea = 'file_upload' | 'download' | 'tmp'
export type UserFileArea = Exclude<FileArea, 'tmp'>

export interface FileEntry {
  type: 'directory' | 'file'
  name: string
  relativePath: string
  parentPath: string
  extension: string
  size: number
  updatedAt: number
  childCount: number
}

export interface DeleteRequest {
  kind: 'single' | 'selected' | 'all'
  paths: string[]
  label: string
}

export const areaLabels: Record<FileArea, { label: string; detail: string; description: string }> = {
  file_upload: {
    label: '用户上传',
    detail: 'users/<user>/file_upload',
    description: '由当前用户上传并交给智能体使用的文件',
  },
  download: {
    label: '智能体产物',
    detail: 'users/<user>/download',
    description: '智能体为当前用户生成的可下载产物',
  },
  tmp: {
    label: '全局临时',
    detail: 'tmp',
    description: '运行过程产生的全局临时文件',
  },
}

export const imageExtensions = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg', '.bmp', '.ico'])
export const previewImageExtensions = new Set(['.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.ico'])
export const audioExtensions = new Set(['.mp3', '.wav', '.ogg', '.m4a', '.aac', '.flac', '.opus'])
export const videoExtensions = new Set(['.mp4', '.webm', '.ogv', '.mov', '.m4v'])
export const archiveExtensions = new Set(['.zip', '.7z', '.rar', '.tar', '.gz', '.bz2', '.xz'])
export const codeExtensions = new Set(['.js', '.jsx', '.ts', '.tsx', '.py', '.json', '.yaml', '.yml', '.css', '.scss', '.html', '.xml', '.sh', '.ps1'])
export const documentExtensions = new Set(['.md', '.txt', '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.csv', '.log'])
export const FILES_PER_PAGE = 6
export const FILE_SORT_OPTIONS: FileSortBy[] = ['name', 'updated_at', 'size']
export const FILE_SORT_LABELS: Record<FileSortBy, string> = {
  name: '按名称',
  updated_at: '按最新日期',
  size: '按大小',
}
export const MEDIA_PREVIEW_LIMITS = {
  image: 10 * 1024 * 1024,
  audio: 100 * 1024 * 1024,
  video: 300 * 1024 * 1024,
} as const

export interface MediaPreviewInfo {
  kind: keyof typeof MEDIA_PREVIEW_LIMITS
  limitLabel: string
  available: boolean
}

export function mediaPreviewInfo(entry: FileEntry): MediaPreviewInfo | null {
  if (entry.type !== 'file') return null
  const extension = entry.extension.toLowerCase()
  const kind = previewImageExtensions.has(extension)
    ? 'image'
    : audioExtensions.has(extension)
      ? 'audio'
      : videoExtensions.has(extension)
        ? 'video'
        : null
  if (!kind) return null
  const maxBytes = MEDIA_PREVIEW_LIMITS[kind]
  const labels = { image: '图片预览上限（10 MB）', audio: '音频预览上限（100 MB）', video: '视频预览上限（300 MB）' } as const
  return { kind, limitLabel: labels[kind], available: entry.size <= maxBytes }
}

export function toFileEntry(entry: FileListEntry): FileEntry {
  return {
    type: entry.type,
    name: entry.name,
    relativePath: entry.relative_path,
    parentPath: entry.parent_path,
    extension: entry.extension,
    size: entry.size,
    updatedAt: entry.updated_at,
    childCount: entry.child_count,
  }
}

export function fileKind(entry: FileEntry): { label: string; className: string } {
  if (entry.type === 'directory') return { label: '文件夹', className: styles.kindFolder }
  const extension = entry.extension.toLowerCase()
  if (imageExtensions.has(extension)) return { label: '图片', className: styles.kindImage }
  if (audioExtensions.has(extension)) return { label: '音频', className: styles.kindAudio }
  if (videoExtensions.has(extension)) return { label: '视频', className: styles.kindVideo }
  if (archiveExtensions.has(extension)) return { label: '压缩包', className: styles.kindArchive }
  if (codeExtensions.has(extension)) return { label: '代码', className: styles.kindCode }
  if (documentExtensions.has(extension)) return { label: '文档', className: styles.kindDocument }
  return { label: extension ? extension.slice(1).toUpperCase() : '文件', className: styles.kindOther }
}

export function EntryIcon({ entry, size = 18 }: { entry: FileEntry; size?: number }) {
  if (entry.type === 'directory') return <Folder size={size} />
  const extension = entry.extension.toLowerCase()
  if (imageExtensions.has(extension)) return <FileImage size={size} />
  if (audioExtensions.has(extension)) return <FileAudio size={size} />
  if (videoExtensions.has(extension)) return <FileVideo size={size} />
  if (archiveExtensions.has(extension)) return <Archive size={size} />
  if (codeExtensions.has(extension)) return <Braces size={size} />
  if (documentExtensions.has(extension)) return <FileText size={size} />
  return <File size={size} />
}

export const AUDIO_PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const

export function formatMediaTime(value: number): string {
  if (!Number.isFinite(value) || value < 0) return '0:00'
  const wholeSeconds = Math.floor(value)
  const minutes = Math.floor(wholeSeconds / 60)
  const seconds = wholeSeconds % 60
  return `${minutes}:${String(seconds).padStart(2, '0')}`
}

export function FileSortSelect({ value, onChange }: { value: FileSortBy; onChange: (value: FileSortBy) => void }) {
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const listboxId = useId()

  useEffect(() => {
    if (!open) return
    const closeFromOutside = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const closeFromKeyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', closeFromOutside)
    document.addEventListener('keydown', closeFromKeyboard)
    return () => {
      document.removeEventListener('pointerdown', closeFromOutside)
      document.removeEventListener('keydown', closeFromKeyboard)
    }
  }, [open])

  return (
    <div ref={rootRef} className={`${styles.sortSelect} ${open ? styles.sortSelectOpen : ''}`}>
      <button
        type="button"
        className={styles.sortTrigger}
        aria-label="文件排序方式"
        aria-controls={listboxId}
        aria-expanded={open}
        aria-haspopup="listbox"
        onClick={() => setOpen((current) => !current)}
      >
        <span>排序</span>
        <strong>{FILE_SORT_LABELS[value]}</strong>
        <ChevronDown size={14} aria-hidden="true" />
      </button>
      {open && (
        <div className={styles.sortPopover} id={listboxId} role="listbox" aria-label="文件排序选项">
          {FILE_SORT_OPTIONS.map((option) => (
            <button
              type="button"
              role="option"
              aria-selected={option === value}
              key={option}
              className={option === value ? styles.sortOptionActive : ''}
              onClick={() => {
                onChange(option)
                setOpen(false)
              }}
            >
              <span>{FILE_SORT_LABELS[option]}</span>
              {option === value && <Check size={14} aria-hidden="true" />}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

export function ThemedAudioPlayer({ src, name, downloadUrl }: { src: string; name: string; downloadUrl: string }) {
  const audioRef = useRef<HTMLAudioElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [playing, setPlaying] = useState(false)
  const [currentTime, setCurrentTime] = useState(0)
  const [duration, setDuration] = useState(0)
  const [muted, setMuted] = useState(false)
  const [volume, setVolume] = useState(1)
  const [playbackRate, setPlaybackRate] = useState(1)
  const [menuOpen, setMenuOpen] = useState(false)
  const [speedOpen, setSpeedOpen] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!menuOpen) return
    const closeFromOutside = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) {
        setMenuOpen(false)
        setSpeedOpen(false)
      }
    }
    const closeFromKeyboard = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setMenuOpen(false)
      setSpeedOpen(false)
    }
    document.addEventListener('pointerdown', closeFromOutside)
    document.addEventListener('keydown', closeFromKeyboard)
    return () => {
      document.removeEventListener('pointerdown', closeFromOutside)
      document.removeEventListener('keydown', closeFromKeyboard)
    }
  }, [menuOpen])

  const togglePlayback = async () => {
    const audio = audioRef.current
    if (!audio) return
    setError('')
    if (!audio.paused) {
      audio.pause()
      return
    }
    try {
      await audio.play()
    } catch {
      setPlaying(false)
      setError('浏览器未能开始播放，请稍后重试')
    }
  }

  const seekTo = (value: number) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(value)) return
    audio.currentTime = value
    setCurrentTime(value)
  }

  const toggleMute = () => {
    const audio = audioRef.current
    if (!audio) return
    audio.muted = !audio.muted
    setMuted(audio.muted)
  }

  const setAudioVolume = (value: number) => {
    const audio = audioRef.current
    if (!audio || !Number.isFinite(value)) return
    const nextVolume = Math.min(1, Math.max(0, value))
    audio.volume = nextVolume
    audio.muted = nextVolume === 0
    setVolume(nextVolume)
    setMuted(audio.muted)
  }

  const selectPlaybackRate = (value: number) => {
    const audio = audioRef.current
    if (!audio) return
    audio.playbackRate = value
    setPlaybackRate(value)
    setMenuOpen(false)
    setSpeedOpen(false)
  }

  const safeDuration = Number.isFinite(duration) ? duration : 0
  const progress = safeDuration > 0 ? Math.min(100, (currentTime / safeDuration) * 100) : 0
  const volumeProgress = muted ? 0 : volume * 100

  return (
    <div className={styles.audioPlayer}>
      <audio
        ref={audioRef}
        className={styles.audioElement}
        src={src}
        preload="metadata"
        aria-label={`音频 ${name}`}
        onLoadedMetadata={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onDurationChange={(event) => setDuration(Number.isFinite(event.currentTarget.duration) ? event.currentTarget.duration : 0)}
        onTimeUpdate={(event) => setCurrentTime(event.currentTarget.currentTime)}
        onPlay={() => setPlaying(true)}
        onPause={() => setPlaying(false)}
        onEnded={() => setPlaying(false)}
        onVolumeChange={(event) => {
          setVolume(event.currentTarget.volume)
          setMuted(event.currentTarget.muted)
        }}
        onRateChange={(event) => setPlaybackRate(event.currentTarget.playbackRate)}
        onError={() => { setPlaying(false); setError('无法读取此音频文件') }}
      >
        当前浏览器不支持音频播放。
      </audio>

      <div className={styles.audioControlRow}>
        <button
          type="button"
          className={styles.audioPrimaryButton}
          onClick={() => void togglePlayback()}
          aria-label={playing ? `暂停 ${name}` : `播放 ${name}`}
          title={playing ? '暂停' : '播放'}
        >
          {playing ? <Pause size={16} fill="currentColor" /> : <Play size={16} fill="currentColor" />}
        </button>
        <span className={styles.audioTime} aria-label={`已播放 ${formatMediaTime(currentTime)}，总时长 ${formatMediaTime(safeDuration)}`}>
          {formatMediaTime(currentTime)} <i>/</i> {formatMediaTime(safeDuration)}
        </span>
        <button
          type="button"
          className={styles.audioIconButton}
          onClick={toggleMute}
          aria-label={muted ? `取消静音 ${name}` : `静音 ${name}`}
          title={muted ? '取消静音' : '静音'}
        >
          {muted || volume === 0 ? <VolumeX size={16} /> : <Volume2 size={16} />}
        </button>
        <div className={styles.audioMenuAnchor} ref={menuRef}>
          <button
            type="button"
            className={`${styles.audioIconButton} ${menuOpen ? styles.audioIconButtonActive : ''}`}
            onClick={() => {
              setMenuOpen((current) => !current)
              setSpeedOpen(false)
            }}
            aria-label={`打开音频菜单 ${name}`}
            aria-haspopup="menu"
            aria-expanded={menuOpen}
            title="更多音频选项"
          >
            <EllipsisVertical size={17} />
          </button>
          {menuOpen && (
            <div className={styles.audioMenu} role="menu" aria-label={`${name} 音频选项`}>
              <div className={styles.audioVolumeRow}>
                <Volume2 size={15} aria-hidden="true" />
                <span>音量</span>
                <input
                  type="range"
                  min="0"
                  max="1"
                  step="0.05"
                  value={muted ? 0 : volume}
                  onChange={(event) => setAudioVolume(Number(event.currentTarget.value))}
                  aria-label={`${name} 音量`}
                  style={{ background: `linear-gradient(90deg, var(--brand) 0%, var(--brand) ${volumeProgress}%, var(--line-strong) ${volumeProgress}%, var(--line-strong) 100%)` }}
                />
                <strong>{Math.round(volumeProgress)}%</strong>
              </div>
              <a href={downloadUrl} download={name} role="menuitem" aria-label={`下载 ${name}`}>
                <Download size={16} /><span>下载</span>
              </a>
              <button
                type="button"
                role="menuitem"
                onClick={() => setSpeedOpen((current) => !current)}
                aria-expanded={speedOpen}
              >
                <Gauge size={16} /><span>播放速度</span><strong>{playbackRate}×</strong>
              </button>
              {speedOpen && (
                <div className={styles.audioSpeedGrid} aria-label="选择播放速度">
                  {AUDIO_PLAYBACK_RATES.map((rate) => (
                    <button
                      key={rate}
                      type="button"
                      role="menuitemradio"
                      aria-checked={playbackRate === rate}
                      onClick={() => selectPlaybackRate(rate)}
                    >
                      {rate}×
                    </button>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      <input
        className={styles.audioTimeline}
        type="range"
        min="0"
        max={safeDuration || 0}
        step="0.1"
        value={Math.min(currentTime, safeDuration || 0)}
        onChange={(event) => seekTo(Number(event.currentTarget.value))}
        aria-label={`${name} 播放进度`}
        disabled={!safeDuration}
        style={{ background: `linear-gradient(90deg, var(--brand) 0%, var(--brand) ${progress}%, var(--line-strong) ${progress}%, var(--line-strong) 100%)` }}
      />
      {error && <span className={styles.audioError} role="status">{error}</span>}
    </div>
  )
}

export function joinPath(parent: string, name: string): string {
  return parent ? `${parent}/${name}` : name
}
