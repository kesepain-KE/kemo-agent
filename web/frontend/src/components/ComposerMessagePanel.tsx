import { useEffect, useId, useState, type ReactNode } from 'react'
import { ChevronDown, MessageSquareText } from 'lucide-react'
import styles from './ComposerMessagePanel.module.css'

export function ComposerMessagePanel({
  sessionKey,
  followUpCount,
  followUpContent,
  guidanceContent,
}: {
  sessionKey: string
  followUpCount: number
  followUpContent: ReactNode
  guidanceContent?: ReactNode
}) {
  const [expanded, setExpanded] = useState(false)
  const contentId = useId()
  const hasGuidance = Boolean(guidanceContent)
  const visibleContent = hasGuidance || (expanded && followUpCount > 0)

  useEffect(() => {
    setExpanded(false)
  }, [sessionKey])

  useEffect(() => {
    if (followUpCount === 0) setExpanded(false)
  }, [followUpCount])

  if (followUpCount === 0 && !hasGuidance) return null

  const summary = followUpCount > 0
    ? expanded
      ? '正在显示 ' + followUpCount + ' 条跟进' + (hasGuidance ? '和当前引导' : '')
      : '已收起 ' + followUpCount + ' 条跟进' + (hasGuidance ? '，保留当前引导' : '')
    : '当前运行引导'

  return (
    <section className={styles.panel} aria-label="跟进和引导消息栏">
      <div className={styles.header}>
        <span className={styles.icon} aria-hidden="true"><MessageSquareText size={17} /></span>
        <span className={styles.copy}>
          <strong>跟进 / 引导消息栏</strong>
          <small>{summary}</small>
        </span>
        <span className={styles.badges} aria-label="消息栏状态">
          {followUpCount > 0 ? <span>跟进 {followUpCount}</span> : null}
          {hasGuidance ? <span className={styles.guidanceBadge}>引导中</span> : null}
        </span>
        {followUpCount > 0 ? (
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={expanded}
            aria-controls={contentId}
            aria-label={expanded ? '收起跟进消息' : '展开跟进消息'}
            onClick={() => setExpanded((value) => !value)}
          >
            <span>{expanded ? '收起' : '展开'}</span>
            <ChevronDown className={expanded ? styles.chevronOpen : ''} size={16} aria-hidden="true" />
          </button>
        ) : null}
      </div>
      {visibleContent ? (
        <div className={styles.content} id={contentId}>
          {expanded && followUpCount > 0 ? <div className={styles.followUps}>{followUpContent}</div> : null}
          {guidanceContent ? <div className={styles.guidance}>{guidanceContent}</div> : null}
        </div>
      ) : null}
    </section>
  )
}
