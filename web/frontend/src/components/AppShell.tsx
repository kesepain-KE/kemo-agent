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
import { formatDateTime, statusLabel } from './ModuleUi'
import { SessionHistoryPanel } from './SessionHistoryPanel'
import { UserProfileCard } from './UserProfileCard'
import type { AuthStatusResponse, OverviewResponse } from '../types/api'
import { useUiStore } from '../store/ui'

export interface ShellOutletContext {
  user: string
  sessionId: string
  setSessionId: (sessionId: string) => void
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
  const [agentsOpen, setAgentsOpen] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [logoutPending, setLogoutPending] = useState(false)
  const [avatarRevision, setAvatarRevision] = useState(0)
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
    const next = new URLSearchParams()
    next.set('user', nextUser)
    setParams(next)
  }

  const setSessionId = (nextSession: string) => {
    const next = new URLSearchParams(params)
    if (nextSession) next.set('session', nextSession)
    else next.delete('session')
    setParams(next)
  }

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
     { label: '打开配置', detail: 'Provider、权限、Prompt 与配置文件', shortcut: ',', keywords: 'settings config provider', action: () => navigate(withContext('/settings')) },
  ], [navigate, sessionId, user, ui])
  const filteredCommands = commands.filter((command) => `${command.label} ${command.detail} ${command.keywords}`.toLocaleLowerCase().includes(commandQuery.trim().toLocaleLowerCase()))

  const overview = overviewQuery.data
  const context = overview?.context
  const contextTotal = Number(context?.usage.total_tokens || 0)
  const contextLimit = Number(context?.limit || 0)
  const contextPercent = Number(context?.percent || 0)
  const provider = overview?.provider

  const settingsPath = (tab: 'users' | 'config') => {
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
          onSelectUser={setUser}
          onOpenProfile={() => navigate(withContext('/profile'))}
          onOpenSettings={() => navigate(settingsPath('config'))}
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
              {modelMenuOpen && <div className="model-menu show"><div className="model-menu-head"><strong>当前模型路由</strong><span>配置镜像 · 只读</span></div><div className="model-current"><span className="model-glyph">{provider?.type.slice(0, 1).toUpperCase() || 'K'}</span><span><strong>{provider?.model || '未读取模型'}</strong><small>{provider?.base_url || '未配置兼容端点'}</small></span></div><button onClick={() => { setModelMenuOpen(false); navigate(withContext('/settings')) }}>查看 Provider 配置 <span>›</span></button></div>}
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
        <section className="content"><Outlet context={{ user, sessionId, setSessionId, overview, refreshOverview: () => { void overviewQuery.refetch() }, openCommandPanel: () => setCommandOpen(true) } satisfies ShellOutletContext} /></section>
      </main>

      <aside className={`drawer ${ui.drawerOpen ? 'show' : ''}`} inert={!ui.drawerOpen}>
        <div className="drawer-head"><strong>运行状态</strong><button className="icon-btn" onClick={() => ui.setDrawerOpen(false)} aria-label="关闭"><X size={17} /></button></div>
        <div className="drawer-body">
          <section className="drawer-section">
            <div className="drawer-title"><strong>当前上下文</strong><span>{sessionId ? sessionLabel(sessionId) : '新会话'}</span></div>
            <div className="drawer-context-number"><strong>{formatTokens(contextTotal)} / {formatTokens(contextLimit)}</strong><span>{context?.usage.estimated ? '本地估算' : contextTotal ? 'Provider 统计' : '等待首轮统计'}</span></div>
            <div className="progress-line"><i style={{ width: `${contextPercent}%` }} /></div><div className="drawer-context-meta"><span>占用 {contextPercent}%</span><span>上限 {contextLimit.toLocaleString()} Token</span></div>
            <div className="state-row"><span>S</span><span>摘要缓存</span><span className="state-pill">{overview?.summary_cache.exists ? `覆盖 ${overview.summary_cache.covered_rounds.length} 轮` : '无缓存'}</span></div>
          </section>
          <section className="drawer-section">
            <div className="drawer-title"><strong>核心与能力</strong><span>真实只读状态</span></div>
            <div className="state-row"><span>●</span><span>{healthQuery.data?.service || 'kemo-agent-web'}</span><span className={`state-pill ${healthQuery.isError ? 'muted' : ''}`}>{healthQuery.isSuccess ? '正常' : '不可用'}</span></div>
            <div className="state-row"><span>H</span><span>RuntimeHost</span><span className="state-pill">{overview?.runtime_host.state || '读取中'}</span></div>
            <div className="state-row"><span>M</span><span>{provider?.model || 'Provider'}</span><span className="state-pill">{provider?.configured ? '已配置' : '待配置'}</span></div>
            <div className="state-row"><span>K</span><span>文件知识</span><span className="state-pill">{overview?.counts.knowledge_documents ?? '—'} 项</span></div>
            <div className="state-row"><span>T</span><span>可用工具</span><span className="state-pill">{overview?.counts.enabled_tools ?? '—'} 个</span></div>
            <button className="state-row state-row-button" onClick={() => setAgentsOpen((value) => !value)}><span>A</span><span>子代理</span><span className="state-pill">{overview?.counts.enabled_agents ?? '—'} 个 {agentsOpen ? '⌃' : '⌄'}</span></button>
            {agentsOpen ? <div className="drawer-agent-list">{overview?.agents.map((agent) => <div key={`${agent.source}:${agent.name}`}><span><strong>{agent.name}</strong><small>{agent.description}</small></span><span>{agent.source} · {agent.model_profile}</span></div>)}</div> : null}
          </section>
          <section className="drawer-section"><div className="drawer-title"><strong>最近活动</strong><span>{overview?.activities.length || 0} 条</span></div><div className="drawer-activities">{overview?.activities.slice(0, 4).map((item, index) => <div className="drawer-activity" key={`${item.type}:${item.updated_at}:${index}`}><span>{item.type === 'session' ? <MessageSquarePlus size={13} /> : item.type === 'plan' ? <ListChecks size={13} /> : <FileSearch size={13} />}</span><span><strong>{item.title}</strong><small>{formatDateTime(item.updated_at)} · {statusLabel(item.status)}</small></span></div>)}{!overview?.activities.length && <span className="drawer-empty">暂无可显示的运行活动</span>}</div></section>
        </div>
      </aside>
      {ui.drawerOpen && <button className="drawer-backdrop" aria-label="关闭运行状态" onClick={() => ui.setDrawerOpen(false)} />}

      {commandOpen && <div className="command-layer show" onMouseDown={(event) => { if (event.target === event.currentTarget) setCommandOpen(false) }}><div className="command-box" role="dialog" aria-label="全局搜索与命令" aria-modal="true"><div className="command-search-row"><Search size={17} /><input ref={commandInputRef} className="command-input" value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} placeholder="搜索页面、状态或命令…" /><kbd>Esc</kbd></div><div className="command-list">{filteredCommands.map((command) => <button className="command-item" key={command.label} onClick={() => runCommand(command.action)}><span><strong>{command.label}</strong><small>{command.detail}</small></span><kbd>{command.shortcut}</kbd></button>)}{!filteredCommands.length && <div className="command-empty">没有匹配的命令</div>}</div><div className="command-foot">Ctrl K 打开 · 方向键导航将在后续增强</div></div></div>}
    </div>
  )
}
