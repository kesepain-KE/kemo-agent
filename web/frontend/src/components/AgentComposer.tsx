import type { ChangeEvent, KeyboardEvent, ReactNode } from 'react'
import { useEffect, useRef } from 'react'
import { BarChart3, BookOpen, Boxes, ChevronDown, Paperclip, Send, Square, Zap } from 'lucide-react'
import styles from './AgentComposer.module.css'

export interface AgentComposerProps {
  value: string
  placeholder: string
  currentRound: number
  roundLimit: number
  running?: boolean
  disabled?: boolean
  conversationMenuOpen?: boolean
  conversationMenu?: ReactNode
  notice?: ReactNode
  onChange: (value: string) => void
  onUploadFile?: () => void
  onOpenKnowledge: () => void
  onOpenSkills: () => void
  onOpenCommands: () => void
  onToggleConversationMenu: () => void
  onSubmit: () => void
  onStop?: () => void
}

export function AgentComposer({
  value,
  placeholder,
  currentRound,
  roundLimit,
  running = false,
  disabled = false,
  conversationMenuOpen = false,
  conversationMenu,
  notice,
  onChange,
  onUploadFile,
  onOpenKnowledge,
  onOpenSkills,
  onOpenCommands,
  onToggleConversationMenu,
  onSubmit,
  onStop,
}: AgentComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null)
  const canSubmit = !disabled && value.trim().length > 0

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

  return (
    <section className={styles.composer} aria-label="消息输入区域">
      {notice}
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
      />

      <div className={styles.footer}>
        <div className={styles.tools}>
          <ComposerIconButton label="上传文件" onClick={onUploadFile} disabled={disabled || !onUploadFile}>
            <Paperclip />
          </ComposerIconButton>
          <ComposerIconButton label="打开知识库" onClick={onOpenKnowledge} disabled={disabled}>
            <BookOpen />
          </ComposerIconButton>
          <ComposerIconButton label="打开技能" onClick={onOpenSkills} disabled={disabled}>
            <Boxes />
          </ComposerIconButton>
          <ComposerIconButton label="打开快捷指令" onClick={onOpenCommands} disabled={disabled} command>
            <Zap />
          </ComposerIconButton>
        </div>

        <div className={styles.actions}>
          <div className={styles.stat} title={`当前第 ${currentRound} 轮，上下文轮次上限为 ${roundLimit} 轮`}>
            <BarChart3 aria-hidden="true" />
            <span className={styles.statRound}>第 {currentRound} 轮</span>
            <span className={styles.statDivider} aria-hidden="true" />
            <span>{currentRound}/{roundLimit}</span>
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
            <button type="button" className={styles.stopButton} onClick={onStop} aria-label="停止生成">
              <Square aria-hidden="true" />
              <span>停止</span>
            </button>
          ) : null}
        </div>
      </div>
    </section>
  )
}

interface ComposerIconButtonProps {
  label: string
  disabled?: boolean
  command?: boolean
  children: ReactNode
  onClick?: () => void
}

function ComposerIconButton({ label, disabled, command = false, children, onClick }: ComposerIconButtonProps) {
  return (
    <button
      type="button"
      className={`${styles.iconButton} ${command ? styles.commandButton : ''}`}
      aria-label={label}
      title={disabled && !onClick ? `${label} · 待接入` : label}
      disabled={disabled}
      onClick={onClick}
    >
      {children}
    </button>
  )
}
