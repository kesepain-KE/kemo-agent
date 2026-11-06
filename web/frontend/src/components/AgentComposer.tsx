import { t as tr, useLanguage } from '../i18n'
import type { ChangeEvent, ClipboardEvent, KeyboardEvent, ReactNode } from 'react'
import { useId, useRef, useState } from 'react'
import { BookOpen, Boxes, Mic, Paperclip, Square } from 'lucide-react'
import { useSpeechRecognition } from '../hooks/useSpeechRecognition'
import { useComposerAutosize } from '../hooks/useComposerAutosize'
import { ReferenceIcon } from '../reference/ReferenceIcon'
import { composerSlashCommands, type ConversationCommandAction } from './appShellConfig'

export interface AgentComposerProps {
  value: string
  placeholder: string
  contextUsage?: ReactNode
  running?: boolean
  stopping?: boolean
  disabled?: boolean
  commandBusy?: boolean
  notice?: ReactNode
  uploadFeedback?: ReactNode
  modelBar?: ReactNode
  pendingFileCount?: number
  uploading?: boolean
  onChange: (value: string) => void
  onUploadFiles?: (files: File[]) => void
  onOpenKnowledge: () => void
  onOpenCapabilities: () => void
  onSlashCommand?: (action: ConversationCommandAction) => void
  onSubmit: () => void
  onGuide?: () => void
  onStop?: () => void
  onStopAndCancelSubagents?: () => void
}

export function AgentComposer({
  value,
  placeholder,
  contextUsage,
  running = false,
  stopping = false,
  disabled = false,
  commandBusy = false,
  notice,
  uploadFeedback,
  modelBar,
  pendingFileCount = 0,
  uploading = false,
  onChange,
  onUploadFiles,
  onOpenKnowledge,
  onOpenCapabilities,
  onSlashCommand,
  onSubmit,
  onGuide,
  onStop,
  onStopAndCancelSubagents,
}: AgentComposerProps) {
  useLanguage()
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const slashMenuId = useId()
  const [slashMenuDismissed, setSlashMenuDismissed] = useState(false)
  const [activeSlashIndex, setActiveSlashIndex] = useState(0)
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
  const slashMatch = value.match(/^\s*\/([^\s/]*)$/)
  const slashMenuOpen = Boolean(slashMatch) && !slashMenuDismissed
  const slashQuery = (slashMatch?.[1] || '').toLocaleLowerCase()
  const filteredSlashCommands = slashMenuOpen
    ? composerSlashCommands.filter((command) =>
      `${command.command} ${command.label} ${command.description}`.toLocaleLowerCase().includes(slashQuery))
    : []
  const selectedSlashIndex = Math.min(activeSlashIndex, Math.max(0, filteredSlashCommands.length - 1))
  const slashCommandsDisabled = disabled || running || stopping || commandBusy

  const runSlashCommand = (action: ConversationCommandAction) => {
    if (slashCommandsDisabled) return
    setSlashMenuDismissed(true)
    onChange('')
    onSlashCommand?.(action)
    window.requestAnimationFrame(() => textareaRef.current?.focus())
  }
  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    setSlashMenuDismissed(false)
    setActiveSlashIndex(0)
    onChange(event.target.value)
  }
  const handleSubmit = () => {
    if (slashMenuOpen && filteredSlashCommands.length > 0) {
      runSlashCommand(filteredSlashCommands[selectedSlashIndex].action)
      return
    }
    if (canSubmit) onSubmit()
  }
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashMenuOpen && !event.nativeEvent.isComposing && event.key === 'Escape') {
      event.preventDefault()
      event.stopPropagation()
      setSlashMenuDismissed(true)
      return
    }
    if (slashMenuOpen && !event.nativeEvent.isComposing
      && filteredSlashCommands.length > 0 && !slashCommandsDisabled) {
      if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
        event.preventDefault()
        const direction = event.key === 'ArrowDown' ? 1 : -1
        setActiveSlashIndex((index) => (
          (index + direction + filteredSlashCommands.length) % filteredSlashCommands.length
        ))
        return
      }
      if (event.key === 'Enter' && !event.nativeEvent.isComposing
        && !event.shiftKey && !event.ctrlKey && !event.metaKey) {
        event.preventDefault()
        runSlashCommand(filteredSlashCommands[selectedSlashIndex].action)
        return
      }
    }
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
      <div className="composer-input-wrap">
        <textarea
          ref={textareaRef}
          className="composer-textarea"
          value={value}
          placeholder={placeholder}
          disabled={disabled}
          rows={1}
          aria-label={tr("消息内容")}
          aria-keyshortcuts={running && onGuide ? 'Control+Enter' : undefined}
          aria-autocomplete={slashMenuOpen ? 'list' : undefined}
          aria-expanded={slashMenuOpen}
          aria-controls={slashMenuOpen ? slashMenuId : undefined}
          aria-activedescendant={slashMenuOpen && filteredSlashCommands.length
            ? `${slashMenuId}-option-${selectedSlashIndex}` : undefined}
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onPaste={handlePaste}
        />
      </div>
      {slashMenuOpen && (
        <div id={slashMenuId} className="composer-slash-menu" role="listbox" aria-label={tr('对话操作')}>
          <div className="composer-slash-menu-head">{tr('对话操作')}</div>
          {filteredSlashCommands.map((command, index) => {
            const Icon = command.icon
            return (
              <button
                id={`${slashMenuId}-option-${index}`}
                key={command.action}
                type="button"
                className={`composer-slash-option ${index === selectedSlashIndex ? 'is-active' : ''}`}
                role="option"
                aria-selected={index === selectedSlashIndex}
                disabled={slashCommandsDisabled}
                onMouseDown={(event) => event.preventDefault()}
                onMouseEnter={() => setActiveSlashIndex(index)}
                onClick={() => runSlashCommand(command.action)}
              >
                <span className="composer-slash-option-icon"><Icon size={17} aria-hidden="true" /></span>
                <span className="composer-slash-option-copy">
                  <strong>{tr(command.label)}</strong>
                  <small>{tr(command.description)}</small>
                </span>
                <code>{command.command}</code>
              </button>
            )
          })}
          {!filteredSlashCommands.length && <div className="composer-slash-empty">{tr('没有匹配的对话操作')}</div>}
        </div>
      )}

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
        </div>

        <div className="reference-composer-actions">
          {contextUsage}

          {running ? (
            onStop ? <button type="button" className="send-btn is-stop" onClick={onStopAndCancelSubagents || onStop}
              aria-label={tr("停止生成")} title={tr("停止生成")} disabled={stopping}>
              <Square aria-hidden="true" />
              <span className="sr-only">{stopping ? tr('正在停止…') : tr('停止')}</span>
            </button> : null
          ) : (
            <button type="button" className="send-btn" disabled={!canSubmit} onClick={handleSubmit}>
              <ReferenceIcon name="arrowUp" />
              <span className="sr-only">{tr('发送')}</span>
            </button>
          )}
        </div>
      </div>
    </section>
  )
}

interface ComposerIconButtonProps {
  label: string
  title?: string
  disabled?: boolean
  active?: boolean
  children: ReactNode
  onClick?: () => void
}

function ComposerIconButton({ label, title, disabled, active = false, children, onClick }: ComposerIconButtonProps) {
  useLanguage()
  return (
    <button
      type="button"
      className={`icon-btn ${active ? 'is-active' : ''}`}
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
