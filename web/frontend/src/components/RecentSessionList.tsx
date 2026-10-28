import { Pin, Star } from 'lucide-react'
import type { SessionSummary } from '../types/api'
import { t, useLanguage } from '../i18n'
import { ReferenceIcon } from '../reference/ReferenceIcon'
import { SessionActions, type SessionActionCallbacks } from './SessionActions'
import styles from './SessionActions.module.css'

interface Props extends SessionActionCallbacks {
  sessions: SessionSummary[]
  activeSessionId: string
  switching: boolean
  running: boolean
  onOpen: (sessionId: string) => void
}

export function RecentSessionList({ sessions, activeSessionId, switching, running, onOpen, ...actions }: Props) {
  useLanguage()
  return <>{sessions.map(session => <SessionActions key={session.session_id} session={session} {...actions}
    onOpen={() => onOpen(session.session_id)} openDisabled={switching || running}
    deleteDisabled={switching || (running && session.session_id === activeSessionId)}>
    {(trigger, onContextMenu) => <div className={styles.recent} onContextMenu={onContextMenu}>
      <button className={`session-item ${session.session_id === activeSessionId ? 'is-active' : ''}`}
        onClick={() => onOpen(session.session_id)} disabled={switching || running}
        title={session.title || session.session_id}>
        <ReferenceIcon name="chat" small /><span className="si-title">{session.title || t('未命名对话')}</span>
        {session.pinned && <Pin size={12} className={styles.badge} aria-label={t('已置顶')} />}
        {session.favorite && <Star size={12} className={styles.badge} fill="currentColor" aria-label={t('已收藏')} />}
        {session.summary_status === 'failed' && <span className="dot dot-warning" />}
        <span className="si-time">{session.rounds} 轮</span>
      </button>{trigger}
    </div>}
  </SessionActions>)}</>
}
