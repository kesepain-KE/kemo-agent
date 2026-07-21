import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BookOpen,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronLeft,
  CircleGauge,
  FileSearch,
  FolderOpen,
  Brain,
  RadioTower,
  ListChecks,
  Menu,
  MessageSquarePlus,
  Moon,
  Search,
  Settings,
  Shapes,
  Sun,
  Wrench,
  X,
} from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  deleteAllSessions,
  deleteSession,
  getHealth,
  getLogoUrl,
  getOverview,
  getPreferences,
  getSessions,
  getUserAvatarUrl,
  getUsers,
  logoutAuth,
  patchPreferences,
  renameSession,
  AVATAR_UPDATED_EVENT,
} from '../api/client'
import { SessionHistoryPanel } from './SessionHistoryPanel'
import { UserProfileCard } from './UserProfileCard'
import type { AuthStatusResponse, OverviewResponse, SessionsResponse } from '../types/api'
import { useUiStore } from '../store/ui'

export interface ShellOutletContext {
  user: string
  sessionId: string
  chatRunning: boolean
  setChatRunning: (running: boolean) => void
  chatRunId: string
  setChatRunId: (runId: string) => void
  setChatAbortController: (controller: AbortController | null) => void
  abortChatRun: () => void
  setSessionId: (sessionId: string) => void
  sessions: SessionsResponse['sessions']
  refreshSessions: () => Promise<SessionsResponse | undefined>
  overview?: OverviewResponse
  refreshOverview: () => void
  openCommandPanel: () => void
}

const navGroups = [
  {
    label: '核心工作区',
    items: [
      { path: '/chat', label: '对话', icon: Bot },
      { path: '/tasks', label: '任务', icon: ListChecks },
      { path: '/knowledge', label: '知识库', icon: BookOpen },
      { path: '/memory', label: '记忆', icon: Brain },
    ],
  },
  {
    label: '运行能力',
    items: [
      { path: '/agents', label: '子智能体', icon: Bot },
      { path: '/skills', label: '工具与技能', icon: Wrench },
      { path: '/sense', label: '感知', icon: BrainCircuit },
      { path: '/expand', label: '拓展', icon: Shapes },
    ],
  },
  {
    label: '资源与系统',
    items: [
      { path: '/files', label: '文件空间', icon: FolderOpen },
      { path: '/messages', label: '外部消息', icon: RadioTower },
      { path: '/status', label: '运行状态', icon: Activity },
      { path: '/settings', label: '配置', icon: Settings },
      { path: '/profile', label: '身份与人格', icon: FileSearch },
    ],
  },
]

const pageTitles: Record<string, string> = {
  '/chat': 'kemo-agent',
  '/tasks': '任务',
  '/knowledge': '知识库',
  '/memory': '记忆',
  '/agents': '子智能体',
  '/skills': '工具与技能',
  '/sense': '感知',
  '/expand': '拓展',
  '/files': '文件空间',
  '/messages': '外部消息',
  '/status': '运行状态',
  '/runtime': '运行模块',
  '/profile': '身份与人格',
  '/settings': '配置',
}

const fontSizeLabels: Record<string, string> = { small: '小', medium: '中', large: '大' }

function formatTokens(value: number) {
  if (!Number.isFinite(value) || value <= 0) return '0'
  if (value < 1000) return String(value)
  return `${(value / 1000).toFixed(value >= 100_000 ? 0 : 1)}K`
}

function sessionLabel(sessionId: string) {
  if (sessionId.startsWith('web_') && sessionId.length > 16) return `Web 会话 · ${sessionId.slice(4, 12)}`
  return sessionId
}

export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const ui = useUiStore()
  const queryClient = useQueryClient()
  const authStatus = queryClient.getQueryData<AuthStatusResponse>(['auth-status'])
  const usersQuery = useQuery({ queryKey: ['users'], queryFn: getUsers })
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 30_000 })
  const user = params.get('user') || usersQuery.data?.users[0]?.name || ''
  const sessionId = params.get('session') || ''
  const sessionsQuery = useQuery({
    queryKey: ['sessions', user],
    queryFn: () => getSessions(user),
    enabled: Boolean(user),
  })
  const overviewQuery = useQuery({
    queryKey: ['overview', user, sessionId],
    queryFn: () => getOverview(user, sessionId),
    enabled: Boolean(user),
    refetchInterval: 30_000,
  })
  const preferencesQuery = useQuery({
    queryKey: ['preferences', user],
    queryFn: () => getPreferences(user),
    enabled: Boolean(user),
    staleTime: 60_000,
  })

  const [fontSizeMenuOpen, setFontSizeMenuOpen] = useState(false)
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [logoutPending, setLogoutPending] = useState(false)
  const [avatarRevision, setAvatarRevision] = useState(0)
  const [chatRunning, setChatRunning] = useState(false)
  const [chatRunId, setChatRunId] = useState('')
  const chatAbortControllerRef = useRef<AbortController | null>(null)
  const fontSizeRef = useRef<HTMLDivElement>(null)
  const modelMenuRef = useRef<HTMLDivElement>(null)
  const commandInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = ui.theme
    document.documentElement.dataset.fontSize = ui.fontSize
  }, [ui.theme, ui.fontSize])

  useEffect(() => {
    if (!preferencesQuery.data) return
    ui.setTheme(preferencesQuery.data.appearance.theme)
    ui.setFontSize(preferencesQuery.data.appearance.font_size)
    // The store setters persist local cache; the API remains the source for new devices.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [preferencesQuery.data])

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      const target = event.target as Node
      if (fontSizeRef.current && !fontSizeRef.current.contains(target)) setFontSizeMenuOpen(false)
      if (modelMenuRef.current && !modelMenuRef.current.contains(target)) setModelMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  useEffect(() => {
    const handler = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault()
        setCommandOpen((value) => !value)
      }
      if (event.key === 'Escape') {
        setCommandOpen(false)
        setFontSizeMenuOpen(false)
        setModelMenuOpen(false)
        ui.setDrawerOpen(false)
      }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [ui])

  useEffect(() => {
    if (commandOpen) requestAnimationFrame(() => commandInputRef.current?.focus())
    else setCommandQuery('')
  }, [commandOpen])

  useEffect(() => {
    const refreshAvatar = (event: Event) => {
      const updatedUser = (event as CustomEvent<{ user?: string }>).detail?.user
      if (!updatedUser || updatedUser === user) setAvatarRevision(Date.now())
    }
    window.addEventListener(AVATAR_UPDATED_EVENT, refreshAvatar)
    return () => window.removeEventListener(AVATAR_UPDATED_EVENT, refreshAvatar)
  }, [user])

  const withContext = (path: string, nextSession = sessionId) => {
    const next = new URLSearchParams()
    if (user) next.set('user', user)
    if (nextSession) next.set('session', nextSession)
    const query = next.toString()
    return query ? `${path}?${query}` : path
  }

  const setUser = (nextUser: string) => {
    if (chatRunning || nextUser === user) return
    const next = new URLSearchParams()
    next.set('user', nextUser)
    setParams(next)
  }

  const setChatAbortController = (controller: AbortController | null) => {
    chatAbortControllerRef.current = controller
  }

  const abortChatRun = () => chatAbortControllerRef.current?.abort()

  const setSessionId = (nextSession: string) => {
    const next = new URLSearchParams(params)
    if (nextSession) next.set('session', nextSession)
    else next.delete('session')
    setParams(next)
  }

  const refreshSessions = async () => (await sessionsQuery.refetch()).data

  const renameHistorySession = async (targetSessionId: string, title: string) => {
    if (!user) throw new Error('当前没有可用用户')
    await renameSession(user, targetSessionId, title)
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessions', user] }),
      queryClient.invalidateQueries({ queryKey: ['overview', user] }),
    ])
  }

  const deleteHistorySession = async (targetSessionId: string) => {
    if (!user) throw new Error('当前没有可用用户')
    await deleteSession(user, targetSessionId)
    queryClient.removeQueries({ queryKey: ['history', user, targetSessionId] })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessions', user] }),
      queryClient.invalidateQueries({ queryKey: ['overview', user] }),
    ])
    if (targetSessionId === sessionId) {
      setSessionId('')
    }
  }

  const deleteAllHistorySessions = async () => {
    if (!user) throw new Error('当前没有可用用户')
    await deleteAllSessions(user)
    queryClient.removeQueries({ queryKey: ['history', user] })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessions', user] }),
      queryClient.invalidateQueries({ queryKey: ['overview', user] }),
    ])
    if (sessionId) setSessionId('')
  }

  const runCommand = (action: () => void) => {
    setCommandOpen(false)
    action()
  }

  const logout = async () => {
    if (logoutPending) return
    setLogoutPending(true)
    try {
      await logoutAuth()
      queryClient.removeQueries({
        predicate: (query) => query.queryKey[0] !== 'auth-status',
      })
      await queryClient.invalidateQueries({ queryKey: ['auth-status'] })
    } finally {
      setLogoutPending(false)
    }
  }

  const commands = useMemo(() => [
    { label: '新建对话', detail: '打开当前用户的新上下文窗口', shortcut: 'N', keywords: 'chat conversation', action: () => { setSessionId(''); navigate(withContext('/chat', '')) } },
    { label: '查看任务中枢', detail: '计划、Cron 与执行记录', shortcut: 'T', keywords: 'task plan cron', action: () => navigate(withContext('/tasks')) },
    { label: '查询文件知识库', detail: '用户层与全局层索引', shortcut: 'K', keywords: 'knowledge file search', action: () => navigate(withContext('/knowledge')) },
    { label: '查看技能注册表', detail: '工具与能力来源', shortcut: 'S', keywords: 'skills tools', action: () => navigate(withContext('/skills')) },
    { label: '查看全局感知', detail: '来源与注入闸门', shortcut: 'G', keywords: 'sense context source', action: () => navigate(withContext('/sense')) },
    { label: '浏览文件空间', detail: '上传文件、生成产物与临时目录', shortcut: 'F', keywords: 'files upload download tmp', action: () => navigate(withContext('/files')) },
     { label: '查看子智能体', detail: '内置与用户子代理', shortcut: 'A', keywords: 'agents subagent', action: () => navigate(withContext('/agents')) },
     { label: '查看外部消息', detail: '身份绑定与传输插件', shortcut: 'M', keywords: 'message transport', action: () => navigate(withContext('/messages')) },
     { label: '查看拓展库存', detail: '三层 Expand 模块', shortcut: 'E', keywords: 'expand extensions', action: () => navigate(withContext('/expand')) },
    { label: '编辑用户资料', detail: '头像、用户人格与全局人格', shortcut: 'P', keywords: 'profile avatar soul', action: () => navigate(withContext('/profile')) },
     { label: '打开运行状态', detail: '上下文、Provider 与后台组件', shortcut: 'R', keywords: 'runtime status context', action: () => navigate(withContext('/status')) },
    { label: '打开配置', detail: 'Provider、上下文、权限与运行限制', shortcut: ',', keywords: 'settings config provider', action: () => navigate(withContext('/settings')) },
  ], [navigate, sessionId, user, ui])
  const filteredCommands = commands.filter((command) => `${command.label} ${command.detail} ${command.keywords}`.toLocaleLowerCase().includes(commandQuery.trim().toLocaleLowerCase()))

  const overview = overviewQuery.data
  const context = overview?.context
  const contextWindow = overview?.context_window
  const contextTotal = Number(contextWindow?.tokens.total_tokens ?? context?.usage.total_tokens ?? 0)
  const contextLimit = Number(contextWindow?.tokens.capacity_tokens ?? context?.limit ?? 0)
  const contextPercent = Number(contextWindow?.tokens.percent ?? context?.percent ?? 0)
  const provider = overview?.provider

  const settingsPath = (tab: 'users' | 'provider') => {
    const path = withContext('/settings')
    return `${path}${path.includes('?') ? '&' : '?'}tab=${tab}`
  }

  return (
    <div className={`app ${ui.sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar" aria-label="主导航">
        <div className="sidebar-head">
          <div className="brand-mark"><img src={getLogoUrl()} width={571} height={568} alt="kemo-agent logo" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = '/kemo-agent.jpg' }} /></div>
          <div className="brand-copy"><strong>kemo-agent</strong><span>Personal Agent Runtime</span></div>
          <button className="sidebar-toggle" onClick={ui.toggleSidebar} aria-label={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'} title={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'}><ChevronLeft size={16} /></button>
        </div>
        <nav className="nav-section nav-scroll">
          {navGroups.map((group) => <div className="nav-group" key={group.label}>
            <span className="nav-group-label">{group.label}</span>
            {group.items.map(({ path, label, icon: Icon }) => {
              const badge = path === '/tasks' ? overview?.counts.active_tasks : undefined
              return <NavLink key={path} to={withContext(path)} className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>
                <span className="nav-icon"><Icon size={20} /></span><span className="nav-label">{label}</span>
                {badge ? <span className="nav-badge">{badge}</span> : null}<span className="nav-tip">{label}</span>
              </NavLink>
            })}
          </div>)}
        </nav>
        <div className="sidebar-rule" />
        <SessionHistoryPanel
          sessions={sessionsQuery.data?.sessions ?? []}
          activeSessionId={sessionId}
          collapsed={ui.sidebarCollapsed}
          loading={sessionsQuery.isLoading}
          error={sessionsQuery.isError}
          onSelectSession={(targetSessionId) => navigate(withContext('/chat', targetSessionId))}
          onRenameSession={renameHistorySession}
          onDeleteSession={deleteHistorySession}
          onDeleteAllSessions={deleteAllHistorySessions}
        />
        <div className="sidebar-spacer" />
        <UserProfileCard
          username={user || '未选择用户'}
          userPath={user ? `users/${user}` : 'users/—'}
          avatarUrl={user ? getUserAvatarUrl(user, avatarRevision) : undefined}
          users={usersQuery.data?.users.map((item) => ({ username: item.name, userPath: `users/${item.name}`, avatarUrl: getUserAvatarUrl(item.name, avatarRevision) }))}
          compact={ui.sidebarCollapsed}
          logoutPending={logoutPending}
          switchingDisabled={chatRunning}
          switchingDisabledReason="当前对话正在运行，结束或停止后才能切换用户"
          onSelectUser={setUser}
          onOpenProfile={() => navigate(withContext('/profile'))}
          onOpenSettings={() => navigate(settingsPath('provider'))}
          onLogout={authStatus?.enabled ? () => { void logout() } : undefined}
        />
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="top-left">
            <button className="mobile-menu icon-btn" onClick={ui.toggleSidebar} aria-label="切换导航"><Menu size={18} /></button>
            <div className="page-title">{pageTitles[location.pathname] || 'kemo-agent'}</div>
            <div className="agent-line"><span className={`status-dot ${healthQuery.isError ? 'offline' : ''}`} /><span>{healthQuery.isSuccess ? '核心运行正常' : healthQuery.isError ? 'Web 后端不可用' : '正在连接'}</span></div>
            {user && <span className="role-chip">{user}</span>}
          </div>
          <div className="top-right">
            <button className="context-button" title="查看上下文与运行状态" onClick={() => ui.setDrawerOpen(true)}>
              <span className="context-main"><span className="context-icon"><CircleGauge size="1.528rem" strokeWidth={2.3} /></span><span className="context-copy"><strong>上下文窗口</strong><span>{contextLimit ? `${formatTokens(contextTotal)} / ${formatTokens(contextLimit)}` : '正在读取'}</span></span></span>
              <span className="context-mini"><b>{contextLimit ? `${contextPercent}%` : '—'}</b><span className="context-track"><i style={{ width: `${contextPercent}%` }} /></span></span>
            </button>
            <div className="model-wrap" ref={modelMenuRef}>
              <button className="model-btn" onClick={() => setModelMenuOpen((value) => !value)} aria-expanded={modelMenuOpen} title="查看当前 Provider">
                <span className="model-main"><span className="model-glyph">{provider?.type.slice(0, 1).toUpperCase() || 'K'}</span><span className="model-copy"><span className="model-name">{provider?.model || 'Provider'}</span><span className="model-sub">{provider?.type || '读取中'} · {provider?.configured ? '已配置' : '待配置'}</span></span></span><ChevronDown size="1.25rem" strokeWidth={2.2} />
              </button>
              {modelMenuOpen && <div className="model-menu show"><div className="model-menu-head"><strong>当前模型路由</strong><span>配置镜像</span></div><div className="model-current"><span className="model-glyph">{provider?.type.slice(0, 1).toUpperCase() || 'K'}</span><span><strong>{provider?.model || '未读取模型'}</strong><small>{provider?.base_url || '未配置兼容端点'}</small></span></div><button onClick={() => { setModelMenuOpen(false); navigate(settingsPath('provider')) }}>编辑 Provider 配置 <span>›</span></button></div>}
            </div>
            <div className="font-size-wrap" ref={fontSizeRef}>
              <button className="font-size-button" aria-expanded={fontSizeMenuOpen} aria-label="调整界面字号" title="调整界面字号" onClick={() => setFontSizeMenuOpen((value) => !value)}><span className="font-size-aa">Aa</span><span className="font-size-caption">字号</span><strong>{fontSizeLabels[ui.fontSize]}</strong><ChevronDown size="1.181rem" strokeWidth={2.2} /></button>
              {fontSizeMenuOpen && <div className="font-size-menu show" role="menu"><div className="font-size-menu-head"><strong>界面字号</strong><span>文字与顶部布局同步适配</span></div>{(['small', 'medium', 'large'] as const).map((size) => <button key={size} className={`font-size-option ${ui.fontSize === size ? 'active' : ''}`} role="menuitem" onClick={() => { ui.setFontSize(size); if (user) void patchPreferences(user, { font_size: size }); setFontSizeMenuOpen(false) }}><span className={`font-sample ${size}`}>Aa</span><span><strong>{fontSizeLabels[size]}</strong><small>{size === 'small' ? '紧凑' : size === 'medium' ? '默认' : '舒适'}</small></span><i>{ui.fontSize === size ? '✓' : ''}</i></button>)}</div>}
            </div>
            <button className="icon-btn theme-toggle-btn" onClick={() => { const theme = ui.theme === 'dark' ? 'light' : 'dark'; ui.setTheme(theme); if (user) void patchPreferences(user, { theme }) }} aria-label={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'} title={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'}>{ui.theme === 'dark' ? <Sun size="1.736rem" strokeWidth={2.1} /> : <Moon size="1.736rem" strokeWidth={2.1} />}</button>
            <button className="icon-btn" onClick={() => ui.setDrawerOpen(true)} aria-label="运行状态" title="运行状态"><Activity size="1.806rem" strokeWidth={2.1} /></button>
            <button className="icon-btn" onClick={() => setCommandOpen(true)} aria-label="命令面板" title="命令面板"><Search size="1.736rem" strokeWidth={2.1} /></button>
          </div>
        </header>
        <section className="content"><Outlet context={{ user, sessionId, chatRunning, setChatRunning, chatRunId, setChatRunId, setChatAbortController, abortChatRun, setSessionId, sessions: sessionsQuery.data?.sessions ?? [], refreshSessions, overview, refreshOverview: () => { void overviewQuery.refetch() }, openCommandPanel: () => setCommandOpen(true) } satisfies ShellOutletContext} /></section>
      </main>

      <aside className={`drawer ${ui.drawerOpen ? 'show' : ''}`} inert={!ui.drawerOpen}>
        <div className="drawer-head"><div className="context-drawer-heading"><strong>上下文窗口</strong><span>{sessionId ? sessionLabel(sessionId) : '新会话 · 系统提示词已就绪'}</span></div><button className="icon-btn" onClick={() => ui.setDrawerOpen(false)} aria-label="关闭"><X size={17} /></button></div>
        <div className="drawer-body context-drawer-body">
          <section className="context-drawer-card context-token-card">
            <div className="context-card-head"><span><CircleGauge size={17} /><strong>Token 占用概览</strong></span><small>当前输入窗口</small></div>
            <div className="context-metric-grid two-columns">
              <div className="context-metric"><span>系统提示词</span><strong>{formatTokens(contextWindow?.tokens.system_prompt_tokens ?? 0)}</strong><small>Token</small></div>
              <div className="context-metric"><span>对话上下文</span><strong>{formatTokens(contextWindow?.tokens.context_tokens ?? 0)}</strong><small>Token</small></div>
              <div className="context-metric emphasized"><span>当前总占用</span><strong>{formatTokens(contextWindow?.tokens.total_tokens ?? 0)}</strong><small>Token</small></div>
              <div className="context-metric"><span>容量上限</span><strong>{formatTokens(contextWindow?.tokens.capacity_tokens ?? 0)}</strong><small>Token</small></div>
            </div>
            <div className="context-capacity"><div><span>上下文容量</span><strong>{contextWindow ? `${contextWindow.tokens.percent.toFixed(2)}%` : '—'}</strong></div><span className="context-capacity-track"><i style={{ width: `${contextWindow?.tokens.percent ?? 0}%` }} /></span></div>
          </section>

          <section className="context-drawer-card">
            <div className="context-card-head"><span><MessageSquarePlus size={17} /><strong>对话统计</strong></span><small>前台与归档</small></div>
            <div className="context-metric-grid three-columns">
              <div className="context-metric"><span>前台对话</span><strong>{contextWindow?.conversation.foreground_rounds ?? '—'}</strong><small>轮</small></div>
              <div className="context-metric"><span>后台归档</span><strong>{contextWindow?.conversation.archived_rounds ?? '—'}</strong><small>轮</small></div>
              <div className="context-metric"><span>工具调用</span><strong>{contextWindow?.conversation.total_tool_calls ?? '—'}</strong><small>次</small></div>
            </div>
          </section>

          <section className="context-drawer-card">
            <div className="context-card-head"><span><ListChecks size={17} /><strong>任务与定时</strong></span><small>当前调度</small></div>
            <div className="context-metric-grid two-columns">
              <div className="context-metric"><span>活跃任务计划</span><strong>{contextWindow?.tasks.active_plans ?? '—'}</strong><small>项</small></div>
              <div className="context-metric"><span>等待定时任务</span><strong>{contextWindow?.tasks.waiting_crons ?? '—'}</strong><small>项</small></div>
            </div>
          </section>

          <section className="context-drawer-card">
            <div className="context-card-head"><span><Wrench size={17} /><strong>工具与子智能体</strong></span><small>能力启用状态</small></div>
            <div className="context-metric-grid three-columns">
              <div className="context-metric"><span>启用工具</span><strong>{contextWindow?.capabilities.tools_enabled ?? '—'}</strong><small>个</small></div>
              <div className="context-metric"><span>禁用工具</span><strong>{contextWindow?.capabilities.tools_disabled ?? '—'}</strong><small>个</small></div>
              <div className="context-metric"><span>子智能体</span><strong>{contextWindow?.capabilities.agents_enabled ?? '—'}</strong><small>个</small></div>
            </div>
          </section>

          <section className="context-drawer-card">
            <div className="context-card-head"><span><BookOpen size={17} /><strong>知识库状态</strong></span><small>当前注入范围</small></div>
            <div className="context-metric-grid three-columns">
              <div className="context-metric"><span>启用知识</span><strong>{contextWindow?.knowledge.enabled ?? '—'}</strong><small>项</small></div>
              <div className="context-metric"><span>禁用知识</span><strong>{contextWindow?.knowledge.disabled ?? '—'}</strong><small>项</small></div>
              <div className="context-metric status-metric"><span>知识图谱</span><strong className={contextWindow?.knowledge.graph_enabled ? 'status-on' : 'status-off'}>{contextWindow ? (contextWindow.knowledge.graph_enabled ? '已启动' : '未启动') : '—'}</strong></div>
            </div>
          </section>

          <section className="context-drawer-card compact-card">
            <div className="context-card-head"><span><RadioTower size={17} /><strong>外部消息</strong></span><small>连接状态</small></div>
            <div className="context-single-stat"><span>已连接的外部消息模块</span><strong>{contextWindow?.messages.connected ?? '—'}<small> 个</small></strong></div>
          </section>

          <section className="context-drawer-card compact-card">
            <div className="context-card-head"><span><Shapes size={17} /><strong>拓展与感知</strong></span><small>外部接入</small></div>
            <div className="context-metric-grid two-columns">
              <div className="context-metric"><span>已接入拓展</span><strong>{contextWindow?.integrations.expands ?? '—'}</strong><small>个</small></div>
              <div className="context-metric"><span>已接入感知</span><strong>{contextWindow?.integrations.senses ?? '—'}</strong><small>个</small></div>
            </div>
          </section>
        </div>
      </aside>
      {ui.drawerOpen && <button className="drawer-backdrop" aria-label="关闭运行状态" onClick={() => ui.setDrawerOpen(false)} />}

      {commandOpen && <div className="command-layer show" onMouseDown={(event) => { if (event.target === event.currentTarget) setCommandOpen(false) }}><div className="command-box" role="dialog" aria-label="全局搜索与命令" aria-modal="true"><div className="command-search-row"><Search size={17} /><input ref={commandInputRef} className="command-input" value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} placeholder="搜索页面、状态或命令…" /><kbd>Esc</kbd></div><div className="command-list">{filteredCommands.map((command) => <button className="command-item" key={command.label} onClick={() => runCommand(command.action)}><span><strong>{command.label}</strong><small>{command.detail}</small></span><kbd>{command.shortcut}</kbd></button>)}{!filteredCommands.length && <div className="command-empty">没有匹配的命令</div>}</div><div className="command-foot">Ctrl K 打开 · 方向键导航将在后续增强</div></div></div>}
    </div>
  )
}
