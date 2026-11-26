import { t as tr, useLanguage } from '../i18n'
import { useEffect, useMemo, useRef, useState } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { QueryClientProvider, useQuery, useQueryClient } from '@tanstack/react-query'
import { useLocation, useNavigate, useOutletContext } from 'react-router-dom'
import * as api from '../api/client'
import type { ShellOutletContext } from '../components/appShellTypes'
import { DocumentMarkdown } from '../components/DocumentMarkdown'
import { ModulePanel } from '../components/ModulePanel'
import { RuntimeCongestion } from '../components/RuntimeCongestion'
import type { KnowledgeDocumentSummary, ModulePanelDefinition, ModulePanelResponse, ModulePanelValue, ExpandScope, RuntimeStatusResponse } from '../types/api'
import { KnowledgeFilePreview } from './KnowledgeFilePreview'
import { disposeReferencePage, renderReferencePage, toast } from './runtime.js'

type PageData = Record<string, any>

/** 按路由读取真实库存，不加载样例数据，也不为了首屏读取全部用户资源。 */
export async function loadReferenceData(route: string, user: string, sessionId: string): Promise<PageData> {
  switch (route) {
    case '/agents': return { agents: await api.getAgents(user) }
    case '/skills': return { skills: await api.getSkills(user) }
    case '/knowledge': return { knowledge: await api.getKnowledge(user) }
    case '/memory': {
      const [memory, important] = await Promise.all([api.getMemorySummary(user), api.getImportantMemory(user)])
      return { memory, important: {
        ...important,
        lifecycle: important.lifecycle ?? { status: 'untracked', reason: '服务端未提供来源追踪信息', reason_codes: [], is_current: false, prompt_eligible: false },
      } }
    }
    case '/tasks': {
      const tasks = await api.getTasks(user, sessionId)
      // 不把系统维护任务的执行记录混入用户任务记录。record_id 老数据可能为空。
      tasks.executions = tasks.executions.filter(item => item.kind !== 'cron' || item.user_defined !== false)
        .map((item, index) => ({ ...item, record_id: item.record_id || `${item.kind}:${item.task_id}:${item.updated_at}:${index}` }))
      return { tasks }
    }
    case '/sense': return { sense: await api.getSense(user) }
    case '/expand': return { expand: await api.getExpands(user) }
    case '/runtime': {
      const [agents, messages, expand] = await Promise.all([api.getAgents(user), api.getMessageStatus(user), api.getExpands(user)])
      return { agents, messages, expand }
    }
    default: throw new Error(`未注册的业务页面：${route}`)
  }
}

function useRuntimeSection(user: string, sessionId: string, sections: string[], active = true) {
  return useQuery({
    queryKey: ['runtime-status', user, sessionId, sections.join(',')],
    queryFn: ({ signal }) => api.getRuntimeStatus(user, sessionId, sections, signal),
    enabled: Boolean(user) && active,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  })
}

function included(data: RuntimeStatusResponse | undefined, section: string) {
  return data?.included_sections?.includes(section) ? data : undefined
}

function useRuntimeView(user: string, sessionId: string, tab: string) {
  const apiRead = useRuntimeSection(user, sessionId, ['api'])
  const maintenance = useRuntimeSection(user, sessionId, ['maintenance'])
  const prompt = useRuntimeSection(user, sessionId, ['summary', 'prompt'], tab === 'prompt')
  const tokens = useRuntimeSection(user, sessionId, ['tokens'], tab === 'tokens')
  const external = useRuntimeSection(user, sessionId, ['external'], tab === 'external')
  const provider = included(apiRead.data, 'api')?.api
  const readCapabilities = Boolean(user && tab === 'api' && provider?.type === 'kemo' && provider.configured)
  const capabilities = useQuery({
    queryKey: ['runtime-status', user, sessionId, 'capabilities', provider?.model, provider?.base_url],
    queryFn: ({ signal }) => api.getKemoModelCapabilities(user, provider!.model, false, signal),
    enabled: readCapabilities,
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  })
  const reads = new Map([['api', apiRead], ['maintenance', maintenance], ['prompt', prompt], ['tokens', tokens], ['external', external]])
  const detail = reads.get(tab)
  return {
    tab, provider,
    context: included(prompt.data, 'summary')?.context,
    routes: included(external.data, 'external')?.message_routes.summary,
    maintenance: included(maintenance.data, 'maintenance')?.system_cron,
    maintenancePending: maintenance.isPending,
    maintenanceError: maintenance.error?.message,
    runtime: included(detail?.data, tab),
    detailPending: detail?.isPending,
    detailError: detail?.error?.message,
    capability: readCapabilities ? capabilities.data?.capabilities : undefined,
    capabilityPending: readCapabilities && capabilities.isPending,
    capabilityError: readCapabilities ? capabilities.error?.message : undefined,
  }
}

function EmbeddedPanel({ user, kind, name, scope, initial, panelError, refresh }: {
  user: string; kind: 'sense' | 'expand'; name: string; scope: ExpandScope;
  initial?: ModulePanelDefinition | null; panelError?: string; refresh: () => Promise<unknown>
}) {
  useLanguage()
  const client = useQueryClient()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const queryKey = ['reference-panel', user, kind, scope, name]
  const panelQuery = useQuery({
    queryKey,
    queryFn: () => kind === 'sense' ? api.getSenseModulePanel(user, name) : api.getExpandModulePanel(user, scope, name),
  })
  const update = async (action: () => Promise<unknown>) => {
    if (busy) return
    setBusy(true); setError('')
    try {
      await action()
      await (kind === 'sense' ? api.refreshSenseModule(user, name) : api.refreshExpandModule(user, scope, name))
      await client.invalidateQueries({ queryKey })
      await refresh()
      toast('模块配置和状态已更新', { kind: 'success' })
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : '模块操作失败')
      throw cause
    } finally { setBusy(false) }
  }
  const initialData = initial ? { panel: initial, panel_error: panelError } as ModulePanelResponse : undefined
  return <ModulePanel kind={kind} data={panelQuery.data || initialData} loading={panelQuery.isLoading}
    busy={busy} error={error || (panelQuery.error instanceof Error ? panelQuery.error.message : '')}
    emptyText={panelError || (kind === 'sense' ? '此感知模块无可控组件' : '此模块没有声明配置面板')}
    onSave={(values, clearSecrets) => update(() => kind === 'sense'
      ? api.putSenseModulePanel(user, name, values, clearSecrets)
      : api.putExpandModulePanel(user, scope, name, values, clearSecrets))}
    onAction={(command: string, params: Record<string, ModulePanelValue>) => update(() => {
      if (command === 'refresh') return Promise.resolve()
      if (kind === 'sense') throw new Error('感知面板不提供动作接口，仅支持专用刷新')
      return api.invokeExpandModulePanelAction(user, scope, name, command, params)
    })} />
}

/** 原样例 DOM 的 React 组合根；协议组件仍由生产版安全渲染器负责。 */
export function ReferencePage() {
  const language = useLanguage()
  const { user, sessionId, chatRunning, sessions } = useOutletContext<ShellOutletContext>()
  const location = useLocation()
  const navigate = useNavigate()
  const client = useQueryClient()
  const hostRef = useRef<HTMLDivElement>(null)
  const route = location.pathname
  const isStatus = route === '/status'
  const params = Object.fromEntries(new URLSearchParams(location.search))
  const viewState = useMemo(() => ({}), [route, user, sessionId, location.search])
  const [selectedTab, setSelectedTab] = useState<{ viewState: object; tab: string }>()
  const statusTab = selectedTab?.viewState === viewState ? selectedTab.tab : params.tab ?? ''
  const status = useRuntimeView(isStatus ? user : '', sessionId, statusTab)
  const renderRef = useRef<{ active: boolean; busy: boolean; updateStatus?: (view: typeof status) => void } | null>(null)
  const forceRefreshRef = useRef(false)
  const queryKey = ['reference-page', route, user, route === '/tasks' ? sessionId : '']
  const pageQuery = useQuery({
    queryKey,
    queryFn: () => {
      const previous = client.getQueryData<PageData>(queryKey)
      if (previous && !forceRefreshRef.current && (renderRef.current?.busy || document.querySelector('.overlay') ||
        hostRef.current?.contains(document.activeElement) && document.activeElement?.matches('input,textarea,select'))) return previous
      return loadReferenceData(route, user, sessionId)
    },
    enabled: Boolean(user) && !isStatus,
    staleTime: 0,
    // 编辑、确认窗口和配置面板操作期间不销毁用户正在使用的 DOM。
    refetchOnWindowFocus: false,
    refetchInterval: query => {
      if (route === '/tasks') return query.state.data?.tasks.summary.active_plans > 0 ? 1_200 : 15_000
      return false
    },
  })
  useEffect(() => {
    if ((!isStatus && !pageQuery.data) || !hostRef.current) return
    const host = hostRef.current
    const roots: Array<{ root: Root; node: HTMLElement; mounted: boolean; disposed: boolean }> = []
    const cleanups: Array<() => void> = []
    const ctx: any = {
      user, sessionId, running: chatRunning, params, viewState, active: true, busy: false, host,
      selectStatusTab: (tab: string) => setSelectedTab({ viewState, tab }),
      registerCleanup: (cleanup: () => void) => cleanups.push(cleanup),
      navigate: (path: string, extra: Record<string, string> = {}) => {
        const query = new URLSearchParams({ user, ...(sessionId ? { session: sessionId } : {}), ...extra })
        navigate(`${path}?${query}`)
      },
      navigatePlan: async (plan: any) => {
        if (chatRunning) throw new Error('请先结束当前运行，再切换计划会话')
        if ((plan.source || 'web') !== 'web' || !plan.session_id) throw new Error('此计划必须在其原来源渠道执行')
        const owner = sessions?.find(item => item.session_id === plan.session_id)
        if (!owner) throw new Error('计划所属会话已清理或不可用，不能为孤立计划创建新会话')
        if (ctx.active) ctx.navigate('/chat', { session: plan.session_id, plan_id: plan.plan_id })
      },
      refresh: async () => {
        if (isStatus) {
          await Promise.all([
            client.refetchQueries({ queryKey: ['runtime-status', user, sessionId], type: 'active' }, { throwOnError: true }),
            ctx.refreshLogs?.(),
          ])
          return
        }
        forceRefreshRef.current = true
        try {
          const result = await pageQuery.refetch()
          if (result.error) throw result.error
        } finally { forceRefreshRef.current = false }
        await Promise.all(['overview', 'tasks', 'knowledge', 'skills', 'sense', 'expands', 'memory', 'agents', 'message-status']
          .map(key => client.invalidateQueries({ queryKey: [key, user] })))
      },
      restoreSwitches: () => { void pageQuery.refetch() },
    }
    const mount = (content: React.ReactNode) => {
      const node = document.createElement('div')
      const root = createRoot(node)
      const entry = { root, node, mounted: false, disposed: false }
      roots.push(entry)
      queueMicrotask(() => { if (ctx.active && !entry.disposed) root.render(content) })
      return node
    }
    ctx.mountCongestion = () => mount(
      <QueryClientProvider client={client}><RuntimeCongestion user={user} sessionId={sessionId} /></QueryClientProvider>)
    ctx.mountKnowledgePreview = (document: KnowledgeDocumentSummary, onClose: () => void, onEdit?: () => void) =>
      mount(<KnowledgeFilePreview document={document} user={user} onClose={onClose} onEdit={onEdit} />)
    ctx.mountMarkdown = (content: string, options: { readOnly?: boolean } = {}) => mount(<DocumentMarkdown content={content} onWidgetAction={options.readOnly ? undefined : action => {
      if (action.type === 'copy') void import('../utils/clipboard').then(({ copyText }) => copyText(action.text))
      else ctx.navigate('/chat', { prompt: action.text }) // 业务文档动作只回填，不自动发起运行。
    }} />)
    ctx.mountPanel = (panel: ModulePanelDefinition | null, options: any) => {
      // Provider 与缓存上下文不能依赖原型的自绘表单或任意 HTML。
      return mount(<QueryClientProvider client={client}>
          <EmbeddedPanel user={user} kind={options.kind || 'expand'} name={options.name}
            scope={options.scope || 'global'} initial={panel} panelError={options.panelError} refresh={ctx.refresh} />
        </QueryClientProvider>)
    }
    try {
      host.replaceChildren(renderReferencePage(route, ctx, pageQuery.data ?? {}))
    } catch (error) {
      const alert = document.createElement('div')
      alert.className = 'banner banner-danger'; alert.setAttribute('role', 'alert')
      alert.textContent = `页面渲染失败：${error instanceof Error ? error.message : String(error)}`
      host.replaceChildren(alert)
      console.error(error)
    }
    renderRef.current = ctx
    // 当本地标签/预览切换移除 DOM 时，及时卸载其 React 桥接组件。
    const observer = new MutationObserver(() => {
      for (const entry of roots) {
        if (entry.disposed) continue
        if (entry.mounted && !entry.node.isConnected) {
          entry.disposed = true
          queueMicrotask(() => entry.root.unmount())
        } else if (entry.node.isConnected) entry.mounted = true
      }
    })
    observer.observe(document.body, { childList: true, subtree: true })
    roots.forEach(entry => { entry.mounted = entry.node.isConnected })
    return () => {
      disposeReferencePage(ctx); cleanups.forEach(cleanup => cleanup()); observer.disconnect(); renderRef.current = null
      const remaining = roots.filter(entry => !entry.disposed)
      remaining.forEach(entry => { entry.disposed = true })
      queueMicrotask(() => remaining.forEach(entry => entry.root.unmount()))
      host.replaceChildren()
    }
  }, [isStatus, pageQuery.data, route, user, sessionId, location.search, client, navigate, viewState, language])
  useEffect(() => { renderRef.current?.updateStatus?.(status) })

  if (!user) return <div className="empty"><div className="empty-title">{tr("请先选择用户")}</div></div>
  if (!isStatus && pageQuery.isLoading) return <div className="reference-loading" role="status"><span className="spinner" />{tr("正在读取真实数据…")}</div>
  if (pageQuery.isError && !pageQuery.data) return <div className="page"><div className="page-inner"><div className="banner banner-danger" role="alert">
    {pageQuery.error instanceof Error ? pageQuery.error.message : tr('读取失败')}
    <button className="btn btn-outline" onClick={() => void pageQuery.refetch()}>{tr("重试")}</button>
  </div></div></div>
  return <>{pageQuery.isError && <div className="banner banner-warning" role="alert">{tr("刷新失败，当前显示上次成功读取的数据。")}<button onClick={() => void pageQuery.refetch()}>{tr("重试")}</button></div>}
    <div key={isStatus ? JSON.stringify([user, sessionId]) : route} className="reference-page-host" ref={hostRef} />
  </>
}
