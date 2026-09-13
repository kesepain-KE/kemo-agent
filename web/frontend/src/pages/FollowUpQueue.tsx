import { ArrowDown, ArrowUp, GripVertical } from 'lucide-react'
import type { PendingNextTurnMessage } from '../components/appShellTypes'
import { pendingInputAttachment } from './chatRunSupport'
import { UserAttachmentCard } from './chatPresentation'
import styles from './FollowUpQueue.module.css'

export function FollowUpQueue({ user, messages, canGuide, onGuide, onRemove, onRetry, onReorder }: {
  user: string
  messages: PendingNextTurnMessage[]
  canGuide: boolean
  onGuide: (message: PendingNextTurnMessage) => void
  onRemove: (id: string) => void
  onRetry: (id: string) => void
  onReorder: (id: string, targetId: string) => void
}) {
  // A message that is being submitted as current-round guidance is no longer
  // a follow-up item.  Keep it in the state queue until the request settles
  // so a failed submission can be restored, but do not render it here.
  const visibleMessages = messages.filter((message) => message.status !== 'guiding')
  if (!visibleMessages.length) return null
  const locked = (message: PendingNextTurnMessage) => ['sending', 'guiding'].includes(message.status)
  return <section className={styles.queue} aria-label="消息跟进队列">
    <ol>
      {visibleMessages.map((message, index) => <li key={message.id} data-follow-up-id={message.id}
        onDragOver={(event) => { if (!locked(message)) event.preventDefault() }}
        onDrop={(event) => {
          event.preventDefault()
          const id = event.dataTransfer.getData('application/x-kemo-follow-up')
          if (id && !locked(message)) onReorder(id, message.id)
        }}>
        <article className={styles.bubble} aria-label={`消息跟进 ${index + 1}`}>
          <div className={styles.heading}>
            <button type="button" className={styles.drag} draggable={!locked(message)} disabled={locked(message)} aria-label={`拖动消息跟进 ${index + 1}`}
              onDragStart={(event) => {
                event.dataTransfer.setData('application/x-kemo-follow-up', message.id)
                event.dataTransfer.effectAllowed = 'move'
              }}><GripVertical aria-hidden="true" /></button>
            <strong>消息跟进 {index + 1}</strong>
            <span role="status">{message.status === 'guiding' ? '正在提交本轮引导' : message.status === 'sending' ? '正在发送' : message.status === 'error' ? '自动发送失败' : '已排队到下一轮'}</span>
          </div>
          {message.content ? <p>{message.content}</p> : null}
          {message.uploadedFiles?.length ? <div className="user-attachment-list guidance-attachment-list">
            {message.uploadedFiles.map((file) => <UserAttachmentCard key={file.path} user={user} attachment={pendingInputAttachment(file)} />)}
          </div> : null}
          {message.error ? <small role="alert">{message.error}</small> : null}
          <div className={styles.actions}>
            <button type="button" onClick={() => onReorder(message.id, visibleMessages[index - 1].id)} disabled={locked(message) || index === 0 || locked(visibleMessages[index - 1])} aria-label={`上移消息跟进 ${index + 1}`}><ArrowUp aria-hidden="true" />上移</button>
            <button type="button" onClick={() => onReorder(message.id, visibleMessages[index + 1].id)} disabled={locked(message) || index === visibleMessages.length - 1 || locked(visibleMessages[index + 1])} aria-label={`下移消息跟进 ${index + 1}`}><ArrowDown aria-hidden="true" />下移</button>
            <span className={styles.spacer} />
            {canGuide ? <button type="button" className={styles.guide} disabled={locked(message)} onClick={() => onGuide(message)}>本轮引导</button> : null}
            {message.status === 'error' ? <button type="button" onClick={() => onRetry(message.id)}>重新发送</button> : null}
            <button type="button" onClick={() => onRemove(message.id)} disabled={locked(message)}>取消</button>
          </div>
        </article>
      </li>)}
    </ol>
  </section>
}
