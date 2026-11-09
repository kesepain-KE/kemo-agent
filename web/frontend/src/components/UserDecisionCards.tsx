import { useEffect, useId, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { ChevronDown, ChevronUp, CircleHelp, Clock3 } from 'lucide-react'
import { answerDecision, getDecisions, type UserDecision } from '../api/decisions'
import { t, useLanguage } from '../i18n'
import shared from './TaskPlanBubble.module.css'
import styles from './UserDecisionCards.module.css'

export function UserDecisionCard({ decision, onAnswer }: {
  decision: UserDecision
  onAnswer: (answer: { option_id: string; text: string }) => Promise<void>
}) {
  useLanguage()
  const id = useId()
  const [collapsed, setCollapsed] = useState(false)
  const [selection, setSelection] = useState('')
  const [custom, setCustom] = useState(false)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const submitting = useRef(false)
  const [error, setError] = useState('')
  const [now, setNow] = useState(Date.now())
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000)
    return () => window.clearInterval(timer)
  }, [])
  const remaining = Math.max(0, Math.ceil(decision.expires_at - now / 1000))
  const countdown = `${Math.floor(remaining / 60)}:${String(remaining % 60).padStart(2, '0')}`

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting.current || !remaining) return
    submitting.current = true
    setBusy(true)
    setError('')
    try {
      await onAnswer({ option_id: custom ? '' : selection, text: custom ? text.trim() : '' })
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : t('提交回答失败'))
    } finally {
      submitting.current = false
      setBusy(false)
    }
  }

  return <section className={`${shared.taskPlanBubble} ${shared.sideCard} ${styles.card}`} aria-label={t('用户决策')}>
    <div className={styles.heading}>
      <CircleHelp size={18} aria-hidden="true" />
      <strong>{t('用户决策')}</strong>
      <span className={styles.mode}>{t(decision.mode === 'wait' ? '等待回答' : '异步提问')}</span>
      <button type="button" className={shared.iconButton} onClick={() => setCollapsed(value => !value)}
        aria-label={t(collapsed ? '展开决策卡片' : '收起决策卡片')} aria-expanded={!collapsed}>
        {collapsed ? <ChevronDown size={16} /> : <ChevronUp size={16} />}
      </button>
    </div>
    <h3 className={styles.title}>{decision.title}</h3>
    <form hidden={collapsed} onSubmit={submit}>
      <p className={styles.question}>{decision.question}</p>
      <fieldset disabled={busy || !remaining} className={styles.options}>
        <legend className={styles.srOnly}>{t('选择一个方案或自定义回复')}</legend>
        {decision.options.map(option => <label key={option.id} className={styles.option}>
          <input type="radio" name={id} checked={!custom && selection === option.id}
            onChange={() => { setSelection(option.id); setCustom(false) }} />
          <span><strong>{option.label}</strong>
            {option.recommended && <small className={styles.recommended}>{t('推荐')}</small>}
            {option.description && <span className={styles.description}>{option.description}</span>}
          </span>
        </label>)}
        <label className={styles.option}>
          <input type="radio" name={id} checked={custom}
            onChange={() => setCustom(true)} />
          <span>{t('自定义回复')}</span>
        </label>
        <textarea aria-label={t('自定义回复')} maxLength={8000} rows={3} value={text}
          placeholder={t('输入你的选择或补充说明')}
          onChange={event => { setText(event.target.value); setCustom(true) }} />
      </fieldset>
      {error && <p role="alert" className={styles.error}>{error}</p>}
      <button type="submit" className={styles.submit} disabled={busy || !remaining
        || (custom ? !text.trim() : !selection)}>{t(busy ? '正在提交…' : '提交选择')}</button>
    </form>
    <div className={styles.timeout}><Clock3 size={13} aria-hidden="true" /><span>
      {remaining ? t('{time} 后由智能体自主决策', { time: countdown }) : t('等待智能体自主决策')}
    </span></div>
  </section>
}

export function UserDecisionCards({ user, sessionId, running = false }: { user: string; sessionId: string; running?: boolean }) {
  const client = useQueryClient()
  const key = ['user-decisions', user, 'web', sessionId]
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => getDecisions(user, sessionId, signal),
    enabled: Boolean(user && sessionId),
    refetchInterval: 1500,
  })
  const revision = useRef<number | undefined>(undefined)
  useEffect(() => {
    const next = query.data?.revision
    if (next === undefined) return
    if (revision.current !== undefined && revision.current !== next && !running) {
      // Recovery after a refresh/restart has no browser-owned SSE stream.
      // Reconcile committed answers and final text through normal history.
      void client.invalidateQueries({ queryKey: ['history', user, sessionId] })
    }
    revision.current = next
  }, [client, query.data?.revision, running, sessionId, user])
  // No placeholderData across identities: an answer always targets the scope
  // that owns the mounted form, even while the user navigates to another chat.
  return query.data?.enabled ? <>
    {query.data.decisions.map(decision => <UserDecisionCard key={decision.decision_id} decision={decision}
      onAnswer={async answer => {
        try {
          await answerDecision(user, sessionId, decision.decision_id, answer)
          await client.invalidateQueries({ queryKey: ['history', user, sessionId] })
        } finally {
          await client.invalidateQueries({ queryKey: key })
        }
      }} />)}
    {query.isError && query.data.decisions.length > 0 && <p className={styles.error} role="alert">
      {t('决策状态刷新失败')}
    </p>}
  </> : null
}
