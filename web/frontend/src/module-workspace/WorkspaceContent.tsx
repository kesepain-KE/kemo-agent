import { useQuery } from '@tanstack/react-query'
import { useState } from 'react'
import * as core from '../api/client'
import { DocumentMarkdown } from '../components/DocumentMarkdown'
import { ModulePanel } from '../components/ModulePanel'
import { t, useLanguage } from '../i18n'
import * as api from './api'
import { PluginWebHost } from './PluginWebHost'
import type { WorkspaceItem, WorkspacePage } from './types'
import styles from './Workbench.module.css'

export function WorkspaceContent({ user, item, page, refresh, onDirtyChange, onExpand }: {
  user: string; item: WorkspaceItem; page: WorkspacePage; refresh: () => Promise<unknown>
  onDirtyChange: (dirty: boolean) => void; onExpand: () => void
}) {
  useLanguage()
  const [busy, setBusy] = useState(false)
  const panel = useQuery({
    queryKey: ['workspace-panel', user, item.kind, item.scope, item.module],
    queryFn: ({ signal }) => api.panel(user, item, signal),
    enabled: page.type === 'panel', refetchInterval: 5000, refetchOnWindowFocus: false,
  })
  const document = useQuery({
    queryKey: ['workspace-document', user, item.key, page.id, item.ui_revision],
    queryFn: ({ signal }) => api.documentPage(user, item, page.id, signal),
    enabled: page.type === 'markdown' || page.view === 'docs',
  })
  if (page.type === 'web') return <PluginWebHost user={user} item={item} page={page} onDirtyChange={onDirtyChange} onExpand={onExpand} />
  if (page.type === 'panel') return <div className={styles.scrollContent}><ModulePanel
    kind={item.kind} data={panel.data} loading={panel.isLoading} error={panel.error instanceof Error ? panel.error.message : ''}
    busy={busy} emptyText={t('此模块没有声明配置面板')} onDirtyChange={onDirtyChange}
    onSave={async (values, clearSecrets, revision) => {
      setBusy(true)
      try { await api.savePanel(user, item, values, clearSecrets, revision); await panel.refetch(); await refresh() }
      finally { setBusy(false) }
    }}
    onAction={async (command, params) => {
      setBusy(true)
      try {
        if (item.kind === 'sense') {
          if (command !== 'refresh') throw new Error(t('未声明的感知面板动作'))
          await core.refreshSenseModule(user, item.module)
        } else if (item.kind === 'message') {
          if (command === 'refresh') await core.checkMessageModule(user, item.module)
          else await api.invokeAction(user, item, command, params)
        } else await core.invokeExpandModulePanelAction(user, item.scope, item.module, command, params)
        await panel.refetch(); await refresh()
      } finally { setBusy(false) }
    }} /></div>
  if (page.type === 'markdown' || page.view === 'docs') return <div className={styles.scrollContent}>
    {document.isLoading ? <p>{t('正在加载文档…')}</p> : document.error ? <div className={styles.error} role="alert">{document.error.message}</div>
      : <DocumentMarkdown content={document.data?.content || ''} />}
  </div>
  if (page.view === 'collection') return <div className={styles.scrollContent}>
    <h3>{t('采集原文')}</h3><DocumentMarkdown content={item.collected_markdown || t('暂无采集数据')} />
  </div>
  if (page.view === 'injection') return <div className={styles.scrollContent}>
    <h3>{t('实际注入内容')}</h3><p className={styles.muted}>{t('注入内容遵循当前数据空间的白名单与上下文预算。')}</p>
    <DocumentMarkdown content={item.injected_markdown || t('当前未注入')} />
  </div>
  if (page.view === 'logs') return <div className={styles.scrollContent}><h3>{t('模块运行记录')}</h3>
    <pre className={styles.code}>{JSON.stringify(item.runtime || {}, null, 2)}</pre>
  </div>
  return <div className={styles.scrollContent}>
    <div className={styles.overviewGrid}>
      <div><span>{t('模块状态')}</span><strong>{item.valid ? item.health : t('清单无效')}</strong></div>
      <div><span>{t('白名单')}</span><strong>{item.enabled ? t('已启用') : t('未启用')}</strong></div>
      <div><span>{t('提示词注入')}</span><strong>{item.active_for_main_agent ? t('已注入') : t('未注入')}</strong></div>
      <div><span>{t('注入 Tokens')}</span><strong>{(item.injected_tokens || 0).toLocaleString()}</strong></div>
    </div>
    <h3>{t('模块说明')}</h3><p>{item.description || item.title}</p>
    {(item.error || item.ui_error || item.panel_error) ? <div className={styles.error} role="alert">{item.error || item.ui_error || item.panel_error}</div> : null}
    <h3>{t('可用功能')}</h3><div className={styles.featureList}>{item.pages.map(entry => <span key={entry.id}>{t(entry.label)}</span>)}</div>
    {item.control_operation_markdown ? <DocumentMarkdown content={item.control_operation_markdown} /> : null}
  </div>
}
