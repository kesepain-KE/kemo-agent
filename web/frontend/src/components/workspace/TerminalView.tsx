import { useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Copy, RefreshCw } from 'lucide-react'
import { t, useLanguage } from '../../i18n'
import { copyText } from '../../utils/clipboard'
import styles from './Workspace.module.css'

export interface TerminalLine { id: string; text: string; stream?: string }
function clean(text: string) {
  return text.replace(/\u001b\[[0-?]*[ -/]*[@-~]/g, '').replace(/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/g, '')
}
export function TerminalView({ lines, active, pending, onRefresh }: {
  lines: TerminalLine[]; active: boolean; pending: boolean; onRefresh: () => void
}) {
  useLanguage()
  const [search, setSearch] = useState('')
  const [follow, setFollow] = useState(true)
  const [feedback, setFeedback] = useState('')
  const scroll = useRef<HTMLDivElement>(null)
  const position = useRef(0)
  const visible = useMemo(() => lines.map(line => ({ ...line, text: clean(line.text) }))
    .filter(line => line.text.toLocaleLowerCase().includes(search.toLocaleLowerCase())), [lines, search])
  useLayoutEffect(() => {
    const element = scroll.current
    if (!active || !element) return
    element.scrollTop = follow ? element.scrollHeight : position.current
  }, [active, follow, visible])
  return <>
    <div className={styles.toolbar}>
      <input type="search" aria-label={t('搜索输出')} placeholder={t('搜索输出')} value={search} onChange={event => setSearch(event.target.value)} />
      <button className={styles.button} onClick={() => setFollow(!follow)} aria-pressed={follow}>{t(follow ? '暂停跟随' : '跟随最新输出')}</button>
      <button className={styles.icon} aria-label={t('复制输出')} title={t('复制输出')} onClick={() => { void copyText(visible.map(line => line.text).join('\n')).then(() => setFeedback(t('已复制'))).catch(() => setFeedback(t('复制失败'))) }}><Copy size={15} /></button>
      <button className={styles.icon} aria-label={t('刷新输出')} title={t('刷新输出')} onClick={onRefresh} disabled={pending}><RefreshCw size={15} className={pending ? 'spin' : undefined} /></button>
    </div>
    {feedback && <div className={styles.hint} role="status">{feedback}</div>}
    <div className={styles.terminal} ref={scroll} tabIndex={0} role="region" aria-label={t('终端输出')}
      onScroll={event => {
        const element = event.currentTarget
        // Hidden panes report zero sizes; preserve the last visible scroll position.
        if (!active || !element.clientHeight) return
        position.current = element.scrollTop
        if (element.scrollHeight - element.clientHeight - element.scrollTop > 32) setFollow(false)
      }}>
      {visible.length ? visible.map(line => <div key={line.id} className={styles.line} data-stream={line.stream}>
        <span>{line.stream === 'stderr' ? 'ERR' : 'OUT'}</span><pre>{line.text}</pre>
      </div>) : <p className={styles.hint}>{t(search ? '没有匹配的输出' : '暂无输出')}</p>}
    </div>
  </>
}
