import { useEffect, useRef, useState } from 'react'
import { ApiError, apiBase } from '../api/transport'
import { t, useLanguage } from '../i18n'
import * as api from './api'
import type { WebInstance, WorkspaceItem, WorkspacePage } from './types'
import styles from './Workbench.module.css'

const protocol = 'kemo.module-ui.v1'
function theme() {
  const style = getComputedStyle(document.documentElement)
  return {
    mode: document.documentElement.dataset.theme || 'light',
    colors: Object.fromEntries(['--bg-app', '--bg-surface', '--bg-inset', '--text-primary', '--text-secondary', '--border', '--accent']
      .map(key => [key, style.getPropertyValue(key).trim()])),
  }
}

export function PluginWebHost({ user, item, page, onDirtyChange, onExpand }: {
  user: string; item: WorkspaceItem; page: WorkspacePage
  onDirtyChange: (dirty: boolean) => void; onExpand: () => void
}) {
  const language = useLanguage()
  const iframe = useRef<HTMLIFrameElement>(null)
  const [instance, setInstance] = useState<WebInstance | null>(null)
  const [error, setError] = useState('')
  const [ready, setReady] = useState(false)
  const [dirty, setDirty] = useState(false)
  const [revision, setRevision] = useState(item.ui_revision)
  const callbacks = useRef({ onDirtyChange, onExpand, language })
  callbacks.current = { onDirtyChange, onExpand, language }
  const active = useRef<WebInstance | null>(null)
  const send = (message: Record<string, unknown>) => {
    const current = active.current
    if (current) iframe.current?.contentWindow?.postMessage({ protocol, nonce: current.nonce, ...message }, '*')
  }
  const initialize = () => send({ kind: 'init', context: {
    module: item.module, kind: item.kind, scope: item.scope, user,
    permissions: active.current?.permissions || [], theme: theme(), locale: callbacks.current.language,
  } })
  const initializeRef = useRef(initialize)
  initializeRef.current = initialize

  useEffect(() => {
    if (!dirty) setRevision(item.ui_revision)
  }, [item.ui_revision, dirty])

  useEffect(() => {
    let disposed = false
    let subscribed = false
    let pending = 0
    let current: WebInstance | null = null
    let startup: ReturnType<typeof setTimeout> | undefined
    const controllers = new Set<AbortController>()
    setError(''); setReady(false); setInstance(null)
    const respond = (id: string, value: Record<string, unknown>) => {
      if (!disposed) send({ kind: 'response', request_id: id, ...value })
    }
    const receive = async (event: MessageEvent) => {
      const message = event.data
      if (event.source !== iframe.current?.contentWindow || !message || message.protocol !== protocol) return
      if (message.kind === 'ready') {
        clearTimeout(startup)
        setError('')
        setReady(true)
        initializeRef.current()
        return
      }
      if (!current || message.kind !== 'request' || message.nonce !== current.nonce) return
      const id = message.request_id
      if (typeof id !== 'string' || id.length > 80 || typeof message.method !== 'string') return
      if (pending >= 8) { respond(id, { error: { code: 'UNAVAILABLE', message: t('插件请求过多，请稍后重试') } }); return }
      const controller = new AbortController()
      controllers.add(controller); pending++
      try {
        if (JSON.stringify(message).length > 128 * 1024) throw new Error(t('插件请求过大'))
        const params = message.params ?? {}
        let result: unknown
        if (message.method === 'host.theme.get') result = theme()
        else if (message.method === 'host.locale.get') result = { locale: callbacks.current.language }
        else if (message.method === 'host.resize.request') { callbacks.current.onExpand(); result = { expanded: true } }
        else if (message.method === 'host.dirty.set') {
          if (typeof params.dirty !== 'boolean') throw new Error(t('dirty 必须是布尔值'))
          setDirty(params.dirty); callbacks.current.onDirtyChange(params.dirty); result = { dirty: params.dirty }
        } else {
          result = await api.rpc(current.nonce, message.method, params, controller.signal)
          if (message.method === 'module.events.subscribe') subscribed = true
        }
        respond(id, { result })
      } catch (cause) {
        const codes: Record<number, string> = { 400: 'INVALID_ARGUMENT', 401: 'FORBIDDEN', 403: 'FORBIDDEN', 404: 'NOT_FOUND', 409: 'CONFLICT' }
        respond(id, { error: {
          code: cause instanceof ApiError ? codes[cause.status] || 'UNAVAILABLE' : 'INVALID_ARGUMENT',
          message: cause instanceof Error ? cause.message : t('插件操作失败'),
        } })
      } finally { controllers.delete(controller); pending-- }
    }
    window.addEventListener('message', receive)
    void api.openInstance(user, item, page.id).then(value => {
      current = value
      if (disposed) { void api.closeInstance(value.nonce).catch(() => undefined); return }
      active.current = value; setInstance(value)
      startup = setTimeout(() => setError(t('插件网页未完成通信握手，请检查页面入口和浏览器控制台')), 15000)
    }).catch(cause => { if (!disposed) setError(cause instanceof Error ? cause.message : t('插件网页加载失败')) })
    const observer = new MutationObserver(() => send({ kind: 'event', event: 'host.theme.changed', data: theme() }))
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme', 'style'] })
    let polling = false
    const timer = setInterval(() => {
      if (!subscribed || !current || disposed || polling || document.hidden) return
      polling = true
      const controller = new AbortController()
      controllers.add(controller)
      void api.rpc(current.nonce, 'module.status.get', {}, controller.signal)
        .then(data => { if (!disposed) send({ kind: 'event', event: 'module.status.changed', data }) })
        .catch(cause => {
          if (cause instanceof ApiError && [403, 404, 409].includes(cause.status)) subscribed = false
          if (!disposed) send({ kind: 'event', event: 'module.error', data: { message: cause instanceof Error ? cause.message : t('模块状态读取失败') } })
        })
        .finally(() => { polling = false; controllers.delete(controller) })
    }, 5000)
    return () => {
      disposed = true; clearTimeout(startup); clearInterval(timer); observer.disconnect()
      window.removeEventListener('message', receive)
      controllers.forEach(controller => controller.abort())
      active.current = null
      if (current) void api.closeInstance(current.nonce).catch(() => undefined)
    }
    // Inventory polling must not replace a running page or its form drafts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, item.kind, item.scope, item.module, page.id, revision, item.permission_revision, item.enabled])

  useEffect(() => { send({ kind: 'event', event: 'host.locale.changed', data: { locale: language } }) }, [language])

  return <div className={styles.webHost}>
    {revision !== item.ui_revision && dirty ? <div className={styles.notice} role="status">
      {t('插件界面有新版本，当前未保存内容已保留。')}
      <button type="button" onClick={() => { setDirty(false); onDirtyChange(false); setRevision(item.ui_revision) }}>{t('放弃未保存内容并更新')}</button>
    </div> : null}
    {error ? <div className={styles.error} role="alert">{error}</div> : null}
    {!ready && !error ? <div className={styles.loading} role="status">{t('正在加载插件网页…')}</div> : null}
    {instance ? <iframe ref={iframe} title={`${item.title} · ${page.label}`}
      src={`${apiBase}${instance.asset_url}`} sandbox="allow-scripts" referrerPolicy="no-referrer"
      onLoad={() => initializeRef.current()} className={styles.webFrame} /> : null}
  </div>
}
