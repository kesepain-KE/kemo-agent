import { useEffect, useState } from 'react'
import { t, useLanguage } from '../../i18n'
import { chatDraftKey, useChatDraftStore } from '../../store/chatDrafts'
import { EMPTY_SCRATCH, scratchKey, useScratchNotes, useScratchSender } from '../../store/scratchNotes'
import styles from './Workspace.module.css'

export function ScratchNote({ user, sessionId }: { user: string; sessionId: string }) {
  return <ConversationNote key={scratchKey(user, sessionId)} user={user} sessionId={sessionId} />
}
function ConversationNote({ user, sessionId }: { user: string; sessionId: string }) {
  useLanguage()
  const key = scratchKey(user, sessionId)
  const note = useScratchNotes(state => state.notes[key] ?? EMPTY_SCRATCH)
  const sender = useScratchSender(state => state.handlers[key])
  const [busy, setBusy] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [confirm, setConfirm] = useState<'transfer' | 'clear' | ''>('')
  useEffect(() => useScratchNotes.getState().load(key), [key])
  const transfer = (mode: 'append' | 'replace') => {
    const draftKey = chatDraftKey(user, sessionId)
    useChatDraftStore.getState().setText(draftKey, current =>
      mode === 'append' && current ? `${current}\n\n${note.text}` : note.text)
    setConfirm('')
    setFeedback('已放入聊天框')
  }
  const send = async () => {
    if (!sender || busy || !note.text.trim()) return
    const revision = note.revision, text = note.text
    setBusy(true)
    setFeedback('')
    try {
      if (await sender(text)) {
        useScratchNotes.getState().clearRevision(key, revision)
        setFeedback('已提交；运行中时加入下一轮队列')
      } else setFeedback('未能提交，草稿已保留')
    } catch { setFeedback('未能提交，草稿已保留') }
    finally { setBusy(false) }
  }
  return <div className={styles.scratch}>
    <div className={styles.toolbar}><strong>{t('草稿本')}</strong><span className={styles.status}>{t(note.saved ? '仅保存在此浏览器' : '本机保存失败，请勿关闭页面')}</span></div>
    <textarea className={styles.scratchEditor} aria-label={t('草稿内容')} placeholder={t('在这里整理问题，确认后发送给智能体。')}
      value={note.text} onChange={event => useScratchNotes.getState().write(key, event.target.value)} />
    {feedback && <p className={styles.hint} role="status">{t(feedback)}</p>}
    {confirm === 'transfer' && <div className={styles.notice} role="group" aria-label={t('聊天框已有内容')}>
      <span>{t('聊天框已有内容')}</span><button className={styles.button} onClick={() => transfer('append')}>{t('追加')}</button>
      <button className={styles.button} onClick={() => transfer('replace')}>{t('替换')}</button><button className={styles.button} onClick={() => setConfirm('')}>{t('取消')}</button>
    </div>}
    {confirm === 'clear' && <div className={styles.notice} role="group" aria-label={t('确认清空草稿')}>
      <span>{t('确认清空草稿')}</span><button className={styles.button} onClick={() => { useScratchNotes.getState().write(key, ''); setConfirm('') }}>{t('清空')}</button>
      <button className={styles.button} onClick={() => setConfirm('')}>{t('取消')}</button>
    </div>}
    <div className={styles.scratchActions}><span className={styles.status}>{note.text.length} {t('字符')}</span>
      <button className={styles.button} disabled={!note.text || busy} onClick={() => setConfirm('clear')}>{t('清空')}</button>
      <button className={styles.button} disabled={!note.text.trim()} onClick={() => {
        if (useChatDraftStore.getState().drafts[key]?.text) setConfirm('transfer')
        else transfer('replace')
      }}>{t('放入聊天框')}</button>
      <button className={styles.button} disabled={!sender || busy || !note.text.trim()} onClick={() => void send()}>{t(busy ? '正在提交…' : '发送给智能体')}</button>
    </div>
  </div>
}
