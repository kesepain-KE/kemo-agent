import type { ChangeEvent, ClipboardEvent, KeyboardEvent, ReactNode } from 'react'
import { useEffect, useRef } from 'react'
import { BarChart3, BookOpen, Boxes, ChevronDown, Mic, Paperclip, Send, Square, Zap } from 'lucide-react'
import { useSpeechRecognition } from '../hooks/useSpeechRecognition'
import styles from './AgentComposer.module.css'

export interface AgentComposerProps {
  value: string
  placeholder: string
  currentRound: number
  totalRounds?: number
  roundLimit: number
  running?: boolean
  stopping?: boolean
  disabled?: boolean
  conversationMenuOpen?: boolean
  conversationMenu?: ReactNode
  notice?: ReactNode
  uploadFeedback?: ReactNode
  pendingFileCount?: number
  uploading?: boolean
  onChange: (value: string) => void
  onUploadFiles?: (files: File[]) => void
  onOpenKnowledge: () => void
  onOpenExpand: () => void
  onOpenCommands: () => void
  onToggleConversationMenu: () => void
  onSubmit: () => void
  onStop?: () => void
}

export function AgentComposer({
  value,
  placeholder,
  currentRound,
  totalRounds = currentRound,
  roundLimit,
  running = false,
  stopping = false,
  disabled = false,
  conversationMenuOpen = false,
  conversationMenu,
  notice,
  uploadFeedback,
  pendingFileCount = 0,
  uploading = false,
  onChange,
  onUploadFiles,
  onOpenKnowledge,
  onOpenExpand,
  onOpenCommands,
  onToggleConversationMenu,
  onSubmit,
  onStop,
}: AgentComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const canSubmit = !disabled
    && !stopping
    && !uploading
    && (value.trim().length > 0 || (!running && pendingFileCount > 0))
  const speech = useSpeechRecognition((text) => {
    const separator = value && !/\s$/.test(value) ? ' ' : ''
    onChange(`${value}${separator}${text}`)
    window.requestAnimationFrame(() => textareaRef.current?.focus())
  })

  useEffect(() => {
    const textarea = textareaRef.current
    if (!textarea) return
    textarea.style.height = '0px'
    const rootSize = Number.parseFloat(getComputedStyle(document.documentElement).fontSize) || 16
    const minimum = rootSize * 4.5
    const maximum = rootSize * 12
    textarea.style.height = `${Math.min(Math.max(textarea.scrollHeight, minimum), maximum)}px`
  }, [value])

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => onChange(event.target.value)
  const handleSubmit = () => {
    if (canSubmit) onSubmit()
  }
  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === 'Enter' && !event.shiftKey && !event.nativeEvent.isComposing) {
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
    <section className={styles.composer} aria-label="消息输入区域">
      {notice}
      {uploadFeedback}
      <input ref={fileInputRef} type="file" multiple hidden aria-hidden="true" onChange={handleFileChange} />
      <textarea
        ref={textareaRef}
        className={styles.input}
        value={value}
        placeholder={placeholder}
        disabled={disabled}
        rows={1}
        aria-label="消息内容"
        onChange={handleChange}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
      />

      <div className={styles.footer}>
        <div className={styles.tools}>
          <ComposerIconButton label="上传文件" onClick={() => fileInputRef.current?.click()} disabled={disabled || uploading || !onUploadFiles}>
            <Paperclip />
          </ComposerIconButton>
          {speech.supported ? <ComposerIconButton
            label={speech.listening ? '停止语音识别' : '语音识别'}
            title={speech.listening ? '停止语音识别' : '语音识别'}
            onClick={speech.listening ? speech.stop : speech.start}
            disabled={disabled}
            active={speech.listening}
          >
            <Mic />
          </ComposerIconButton> : null}
          <ComposerIconButton label="打开知识库" onClick={onOpenKnowledge} disabled={disabled}>
            <BookOpen />
          </ComposerIconButton>
          <ComposerIconButton label="打开拓展" onClick={onOpenExpand} disabled={disabled}>
            <Boxes />
          </ComposerIconButton>
          <ComposerIconButton label="打开快捷指令" onClick={onOpenCommands} disabled={disabled} command>
            <Zap />
          </ComposerIconButton>
        </div>

        <div className={styles.actions}>
          <div className={styles.stat} title={`当前上下文 ${currentRound}/${roundLimit} 轮，历史累计 ${totalRounds} 轮`}>
            <BarChart3 aria-hidden="true" />
            <span className={styles.statRound}>上下文 {currentRound} 轮</span>
            <span className={styles.statDivider} aria-hidden="true" />
            <span>{currentRound}/{roundLimit}</span>
            <span className={styles.statDivider} aria-hidden="true" />
            <span>历史 {totalRounds}</span>
          </div>

          <div className={`${styles.menuWrap} composer-more`}>
            <button
              type="button"
              className={`${styles.actionButton} ${conversationMenuOpen ? styles.active : ''}`}
              disabled={disabled || running}
              aria-expanded={conversationMenuOpen}
              aria-label="展开对话操作"
              onClick={onToggleConversationMenu}
            >
              <span>对话操作</span>
              <ChevronDown aria-hidden="true" />
            </button>
            {conversationMenu}
          </div>

          <button type="button" className={`${styles.sendButton} ${running ? styles.guidance : ''}`} disabled={!canSubmit} onClick={handleSubmit}>
            <Send aria-hidden="true" />
            <span>{running ? '发送引导' : '发送'}</span>
          </button>
          {running && onStop ? (
            <button type="button" className={styles.stopButton} onClick={onStop} aria-label="停止生成" disabled={stopping}>
              <Square aria-hidden="true" />
              <span>{stopping ? '正在停止…' : '停止'}</span>
            </button>
          ) : null}
        </div>
      </div>
    </section>
  )
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
  return (
    <button
      type="button"
      className={`${styles.iconButton} ${command ? styles.commandButton : ''} ${active ? styles.listeningButton : ''}`}
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
