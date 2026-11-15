import { useEffect, useState } from 'react'
import { t, useLanguage } from '../i18n'
import * as api from './api'
import type { WorkspaceItem, WorkspacePage } from './types'
import styles from './Workbench.module.css'

const loopbackHosts = new Set(['127.0.0.1', 'localhost', '[::1]'])

function loopbackUrl(value: unknown): string {
  if (typeof value !== 'string') return ''
  try {
    const url = new URL(value)
    return ['http:', 'https:'].includes(url.protocol) && loopbackHosts.has(url.hostname) ? url.toString() : ''
  } catch {
    return ''
  }
}

function consoleUrlOf(payload: unknown): string {
  const outer = (payload as { result?: unknown } | null)?.result ?? payload
  const inner = (outer as { result?: unknown } | null)?.result ?? outer
  for (const candidate of [inner, outer]) {
    const value = candidate as { console_url?: unknown; url?: unknown } | null
    const url = loopbackUrl(value?.console_url ?? value?.url)
    if (url) return url
  }
  return ''
}

export function EmbedHost({ user, item, page }: {
  user: string; item: WorkspaceItem; page: WorkspacePage
}) {
  useLanguage()
  const [attempt, setAttempt] = useState(0)
  const [url, setUrl] = useState('')
  const [error, setError] = useState('')
  const [opening, setOpening] = useState(true)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let disposed = false
    setUrl(''); setError(''); setOpening(true); setLoading(true)
    if (!item.enabled || !page.action) {
      setError(t(!item.enabled ? '当前模块已停用，不能执行动作' : '嵌入式页面没有声明动作'))
      setOpening(false); setLoading(false)
      return
    }
    void api.invokeAction(user, item, page.action, {}).then(payload => {
      if (disposed) return
      const next = consoleUrlOf(payload)
      if (!next) throw new Error(t('动作没有返回可用的本机地址'))
      setUrl(next)
    }).catch(cause => {
      if (!disposed) {
        setError(cause instanceof Error ? cause.message : t('控制台地址读取失败'))
        setLoading(false)
      }
    }).finally(() => { if (!disposed) setOpening(false) })
    return () => { disposed = true }
    // Inventory polling must preserve the iframe and the console's internal state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, item.kind, item.scope, item.module, item.ui_revision, item.enabled, page.id, page.action, attempt])

  return <div className={styles.webHost}>
    <div className={styles.embedBar}>
      <strong>{t(page.label)}</strong>
      <button type="button" disabled={opening || !item.enabled} onClick={() => setAttempt(value => value + 1)}>
        {opening ? t('正在打开…') : t('重新打开')}
      </button>
    </div>
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    {loading && !error ? <div className={styles.embedLoading} role="status">{t('正在加载控制台…')}</div> : null}
    {url ? <iframe title={`${item.title} · ${page.label}`} src={url}
      sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-downloads"
      referrerPolicy="no-referrer" onLoad={() => setLoading(false)} className={styles.webFrame} /> : null}
  </div>
}
