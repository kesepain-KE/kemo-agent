import { t as tr, useLanguage } from '../i18n'
import type { ChangeEvent, ClipboardEvent, KeyboardEvent, ReactNode } from 'react'
import { useRef } from 'react'
import { BookOpen, Boxes, Mic, Paperclip, Send, Square, Zap } from 'lucide-react'
import { useSpeechRecognition } from '../hooks/useSpeechRecognition'
import { useComposerAutosize } from '../hooks/useComposerAutosize'
import { ReferenceIcon } from '../reference/ReferenceIcon'

export interface AgentComposerProps {
  value: string
  placeholder: string
  currentRound: number
  totalRounds?: number
  roundLimit: number
  contextPercent?: number
  contextUsedTokens?: number
  contextTokenWindow?: number
  running?: boolean
  stopping?: boolean
  disabled?: boolean
  conversationMenuOpen?: boolean
  conversationMenu?: ReactNode
  notice?: ReactNode
  uploadFeedback?: ReactNode
  modelBar?: ReactNode
  pendingFileCount?: number
  uploading?: boolean
  onChange: (value: string) => void
  onUploadFiles?: (files: File[]) => void
  onOpenKnowledge: () => void
  onOpenCapabilities: () => void
  onOpenCommands: () => void
  onToggleConversationMenu: () => void
  onSubmit: () => void
  onGuide?: () => void
  onNextTurn?: () => void
  onStop?: () => void
}

export function AgentComposer({
  value,
  placeholder,
  currentRound,
  totalRounds = currentRound,
  roundLimit,
  contextPercent = 0,
  contextUsedTokens,
  contextTokenWindow,
  running = false,
  stopping = false,
  disabled = false,
  conversationMenuOpen = false,
  conversationMenu,
  notice,
  uploadFeedback,
  modelBar,
  pendingFileCount = 0,
  uploading = false,
  onChange,
  onUploadFiles,
  onOpenKnowledge,
  onOpenCapabilities,
  onOpenCommands,
  onToggleConversationMenu,
  onSubmit,
  onGuide,
  onNextTurn,
  onStop,
}: AgentComposerProps) {
  useLanguage()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const canSubmit = !disabled
    && !uploading
    && (!stopping || running)
    && (value.trim().length > 0 || pendingFileCount > 0)
  const speech = useSpeechRecognition((text) => {
    const separator = value && !/\s$/.test(value) ? ' ' : ''
    onChange(`${value}${separator}${text}`)
    window.requestAnimationFrame(() => textareaRef.current?.focus())
  })

  useComposerAutosize(textareaRef, value)
  const normalizedContextPercent = Math.max(0, Math.min(100, Number.isFinite(contextPercent) ? contextPercent : 0))
  const contextPercentLabel = Number.isInteger(normalizedContextPercent)
    ? String(normalizedContextPercent)
    : normalizedContextPercent.toFixed(1)
  const contextUsedTokensLabel = formatTokenCount(contextUsedTokens)
  const contextTokenWindowLabel = formatTokenCount(contextTokenWindow)
  const contextUsageLabel = `${contextPercentLabel}% | ${contextUsedTokensLabel} token | ${contextTokenWindowLabel} token窗口`

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)
  const handleSubmit = () => {
    if (canSubmit) onSubmit()
  }
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key !== 'Enter' || event.nativeEvent.isComposing) return
    if (running && !stopping && event.ctrlKey && onGuide) {
      event.preventDefault()
      if (canSubmit) onGuide()
      return
    }
    if (!event.shiftKey && !event.ctrlKey) {
      event.preventDefault()
      handleSubmit()
    }
  }
  const handleFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? [])
    event.currentTarget.value = ''
    if (files.length) onUploadFiles?.(files)
  }
  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (disabled || uploading || !onUploadFiles) return
    const itemFiles = Array.from(event.clipboardData.items)
      .filter((item) => item.kind === 'file')
      .map((item) => item.getAsFile())
      .filter((file): file is File => file !== null)
    const files = itemFiles.length ? itemFiles : Array.from(event.clipboardData.files)
    if (!files.length) return
    event.preventDefault()
    onUploadFiles(files)
  }

  return (
    <section className={`composer composer-inner ${running ? 'is-running' : ''}`} aria-label={tr("消息输入区域")}>
      {notice}
      {uploadFeedback}
      {modelBar}
      <input ref={fileInputRef} type="file" multiple hidden aria-hidden="true" onChange={handleFileChange} />
      <textarea
        ref={textareaRef}
        className="composer-textarea"
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        aria-label={tr("消息内容")}
        aria-keyshortcuts={running && onGuide ? 'Control+Enter' : undefined}
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />

      <div className="composer-bar">
        <div className="reference-composer-tools">
          <ComposerIconButton label={tr("上传文件")} onClick={() => fileInputRef.current?.click()} disabled={disabled || uploading || !onUploadFiles}>
            <Paperclip />
          </ComposerIconButton>
          {speech.supported ? <ComposerIconButton
            label={speech.listening ? tr('停止语音识别') : tr('语音识别')}
            title={speech.listening ? tr('停止语音识别') : tr('语音识别')}
            onClick={speech.listening ? speech.stop : speech.start}
            disabled={disabled}
            active={speech.listening}
          >
            <Mic />
          </ComposerIconButton> : null}
          <ComposerIconButton label={tr("打开知识库")} onClick={onOpenKnowledge} disabled={disabled}>
            <BookOpen />
          </ComposerIconButton>
          <ComposerIconButton label={tr("打开能力引用")} title="引用拓展、技能或插件" onClick={onOpenCapabilities} disabled={disabled}>
            <Boxes />
          </ComposerIconButton>
          <ComposerIconButton label={tr("打开快捷指令")} onClick={onOpenCommands} disabled={disabled} command>
            <Zap />
          </ComposerIconButton>
        </div>

        <div className="reference-composer-actions">
          <div
            className="model-pill reference-round-stat"
            title={`上下文占用 ${contextUsageLabel} · 当前轮数 ${currentRound} · 压缩触发轮数 ${roundLimit} · 总轮数 ${totalRounds}`}
            aria-label={`上下文占用 ${contextUsageLabel} · 当前轮数 ${currentRound} · 压缩触发轮数 ${roundLimit} · 总轮数 ${totalRounds}`}
          >
            <span className="context-usage-ring" aria-hidden="true">
              <svg viewBox="0 0 36 36">
                <circle className="context-usage-ring-track" cx="18" cy="18" r="15" pathLength="100" />
                <circle
                  className="context-usage-ring-fill"
                  cx="18"
                  cy="18"
                  r="15"
                  pathLength="100"
                  strokeDasharray={`${normalizedContextPercent} 100`}
                />
              </svg>
            </span>
            <span className="reference-stat-divider" aria-hidden="true" />
            <span className="reference-round-bubble">
              <span className="reference-round-number">{currentRound}</span>
              <span className="reference-stat-divider" aria-hidden="true" />
              <span className="reference-round-number">{roundLimit}</span>
              <span className="reference-stat-divider" aria-hidden="true" />
              <span className="reference-round-number">{totalRounds}</span>
            </span>
          </div>

          <div className="composer-more reference-menu-anchor">
            <button
              type="button"
              className={`btn btn-ghost btn-sm ${conversationMenuOpen ? 'is-active' : ''}`}
              disabled={disabled}
              aria-expanded={conversationMenuOpen}
              aria-label={tr("展开对话操作")}
              onClick={onToggleConversationMenu}
            >
              <span>{tr("对话操作")}</span>
            </button>
            {conversationMenu}
          </div>

          {running && onNextTurn ? <button type="button" className="btn btn-ghost btn-sm" disabled={!canSubmit} onClick={() => { if (canSubmit) onNextTurn() }}>
            <span>{tr("下一轮发送")}</span>
          </button> : null}
          <button type="button" className={`send-btn ${running ? 'reference-followup-send' : ''}`} disabled={!canSubmit} onClick={handleSubmit}>
            <ReferenceIcon name="arrowUp" />
            <span className="sr-only">{running ? tr('消息跟进') : tr('发送')}</span>
          </button>
          {running && onStop ? (
            <button type="button" className="send-btn is-stop" onClick={onStop} aria-label={tr("停止生成")} disabled={stopping}>
              <Square aria-hidden="true" />
              <span className="sr-only">{stopping ? tr('正在停止…') : tr('停止')}</span>
            </button>
          ) : null}
        </div>
      </div>
    </section>
  )
}

function formatTokenCount(value: number | undefined) {
  return Number.isFinite(value) ? Math.max(0, Number(value)).toLocaleString() : '—'
}

interface ComposerIconButtonProps {
  label: string
  title?: string
  disabled?: boolean
  command?: boolean
  active?: boolean
  children: ReactNode
  onClick?: () => void
}

function ComposerIconButton({ label, title, disabled, command = false, active = false, children, onClick }: ComposerIconButtonProps) {
  useLanguage()
  return (
    <button
      type="button"
      className={`icon-btn ${command ? 'reference-command-tool' : ''} ${active ? 'is-active' : ''}`}
      aria-label={label}
      aria-pressed={active || undefined}
      title={disabled && !onClick ? `${label} · 待接入` : title || label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
