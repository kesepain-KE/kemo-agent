import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  Activity,
  BookOpen,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronLeft,
  CircleGauge,
  FileSearch,
  ListChecks,
  Menu,
  MessageSquarePlus,
  Moon,
  Search,
  Settings,
  Sun,
  Wrench,
  X,
} from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { getHealth, getOverview, getSessions, getUsers } from '../api/client'
import { formatDateTime, statusLabel } from './ModuleUi'
import type { OverviewResponse } from '../types/api'
import { useUiStore } from '../store/ui'

export interface ShellOutletContext {
  user: string
  sessionId: string
  setSessionId: (sessionId: string) => void
  overview?: OverviewResponse
  refreshOverview: () => void
}

const navItems = [
  { path: '/chat', label: '对话', icon: Bot },
  { path: '/tasks', label: '任务', icon: ListChecks },
  { path: '/knowledge', label: '知识库', icon: BookOpen },
  { path: '/skills', label: '技能', icon: Wrench },
  { path: '/sense', label: '全局感知', icon: BrainCircuit },
  { path: '/settings', label: '配置概览', icon: Settings },
]

const pageTitles: Record<string, string> = {
  '/chat': 'kemo-agent',
  '/tasks': '任务',
  '/knowledge': '知识库',
  '/skills': '技能',
  '/sense': '全局感知',
  '/settings': '配置概览',
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

  const [roleMenuOpen, setRoleMenuOpen] = useState(false)
  const [fontSizeMenuOpen, setFontSizeMenuOpen] = useState(false)
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const roleMenuRef = useRef<HTMLDivElement>(null)
  const fontSizeRef = useRef<HTMLDivElement>(null)
  const modelMenuRef = useRef<HTMLDivElement>(null)
  const commandInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = ui.theme
    document.documentElement.dataset.fontSize = ui.fontSize
  }, [ui.theme, ui.fontSize])

  useEffect(() => {
    const handler = (event: MouseEvent) => {
      const target = event.target as Node
      if (roleMenuRef.current && !roleMenuRef.current.contains(target)) setRoleMenuOpen(false)
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
        setRoleMenuOpen(false)
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
    setRoleMenuOpen(false)
  }

  const setSessionId = (nextSession: string) => {
    const next = new URLSearchParams(params)
    if (nextSession) next.set('session', nextSession)
    else next.delete('session')
    setParams(next)
  }

  const runCommand = (action: () => void) => {
    setCommandOpen(false)
    action()
  }

  const commands = useMemo(() => [
    { label: '新建对话', detail: '打开当前用户的新上下文窗口', shortcut: 'N', keywords: 'chat conversation', action: () => { setSessionId(''); navigate(withContext('/chat', '')) } },
    { label: '查看任务中枢', detail: '计划、Cron 与执行记录', shortcut: 'T', keywords: 'task plan cron', action: () => navigate(withContext('/tasks')) },
    { label: '查询文件知识库', detail: '用户层与全局层索引', shortcut: 'K', keywords: 'knowledge file search', action: () => navigate(withContext('/knowledge')) },
    { label: '查看技能注册表', detail: '工具与能力来源', shortcut: 'S', keywords: 'skills tools', action: () => navigate(withContext('/skills')) },
    { label: '查看全局感知', detail: '来源与注入闸门', shortcut: 'G', keywords: 'sense context source', action: () => navigate(withContext('/sense')) },
    { label: '打开运行状态', detail: '上下文、Provider 与能力计数', shortcut: 'R', keywords: 'runtime status context', action: () => ui.setDrawerOpen(true) },
    { label: '打开配置概览', detail: '脱敏运行配置镜像', shortcut: ',', keywords: 'settings config provider', action: () => navigate(withContext('/settings')) },
  ], [navigate, sessionId, user, ui])
  const filteredCommands = commands.filter((command) => `${command.label} ${command.detail} ${command.keywords}`.toLocaleLowerCase().includes(commandQuery.trim().toLocaleLowerCase()))

  const overview = overviewQuery.data
  const context = overview?.context
  const contextTotal = Number(context?.usage.total_tokens || 0)
  const contextLimit = Number(context?.limit || 0)
  const contextPercent = Number(context?.percent || 0)
  const provider = overview?.provider

  return (
    <div className={`app ${ui.sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar" aria-label="主导航">
        <div className="sidebar-head">
          <div className="brand-mark"><img src="/kemo-agent.jpg" alt="kemo-agent logo" /></div>
          <div className="brand-copy"><strong>kemo-agent</strong><span>Personal Agent Runtime</span></div>
          <button className="sidebar-toggle" onClick={ui.toggleSidebar} aria-label={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'} title={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'}><ChevronLeft size={16} /></button>
        </div>
        <nav className="nav-section">
          {navItems.map(({ path, label, icon: Icon }) => {
            const badge = path === '/tasks' ? overview?.counts.active_tasks : undefined
            return <NavLink key={path} to={withContext(path)} className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>
              <span className="nav-icon"><Icon size={20} /></span><span className="nav-label">{label}</span>
              {badge ? <span className="nav-badge">{badge}</span> : null}<span className="nav-tip">{label}</span>
            </NavLink>
          })}
        </nav>
        <div className="sidebar-rule" />
        <section className="recent-block">
          <div className="recent-title">最近对话</div>
          <div className="recent-list">
            {sessionsQuery.isLoading && <span className="sidebar-note">正在加载…</span>}
            {sessionsQuery.isError && <span className="sidebar-note error">会话加载失败</span>}
            {sessionsQuery.data?.sessions.slice(0, 8).map((session) => <button key={session.session_id} className={`recent-btn ${session.session_id === sessionId ? 'active' : ''}`} onClick={() => navigate(withContext('/chat', session.session_id))}><strong>{sessionLabel(session.session_id)}</strong><span>{session.rounds} 轮 · {formatDateTime(session.updated_at)}</span></button>)}
            {sessionsQuery.data?.sessions.length === 0 && <span className="sidebar-note">暂无 Web 会话</span>}
          </div>
        </section>
        <div className="sidebar-spacer" />
        <div className="role-wrap" ref={roleMenuRef}>
          <button className="role-button" onClick={() => setRoleMenuOpen((value) => !value)} aria-label="切换当前用户" title="切换当前用户">
            <span className="role-avatar">{user.slice(0, 1).toUpperCase() || '?'}</span>
            <span className="role-copy"><strong>{user || '未选择用户'}</strong><span>users/{user || '—'} · 当前用户</span></span>
            <span className="role-chevron"><ChevronDown size={15} /></span>
          </button>
          {roleMenuOpen && <div className="role-menu show">
            <div className="role-menu-head"><span><strong>切换用户</strong><small>载入对应 users/&lt;user_id&gt; 运行态</small></span><span className="space-mode-badge">单用户执行</span></div>
            {usersQuery.data?.users.map((item) => <button key={item.name} className={`role-option ${item.name === user ? 'active' : ''}`} onClick={() => setUser(item.name)}><span className="mini-avatar">{item.name.slice(0, 1).toUpperCase()}</span><span><strong>{item.name}</strong><small>用户运行态</small><em className="space-option-id">users/{item.name}</em></span><span className="check">{item.name === user ? '✓' : ''}</span></button>)}
            <div className="role-menu-foot">每个窗口只激活一个当前用户；切换不会并行运行多个智能体。</div>
          </div>}
        </div>
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
              <span className="context-main"><span className="context-icon"><CircleGauge size={14} /></span><span className="context-copy"><strong>上下文窗口</strong><span>{contextLimit ? `${formatTokens(contextTotal)} / ${formatTokens(contextLimit)}` : '正在读取'}</span></span></span>
              <span className="context-mini"><b>{contextLimit ? `${contextPercent}%` : '—'}</b><span className="context-track"><i style={{ width: `${contextPercent}%` }} /></span></span>
            </button>
            <div className="model-wrap" ref={modelMenuRef}>
              <button className="model-btn" onClick={() => setModelMenuOpen((value) => !value)} aria-expanded={modelMenuOpen} title="查看当前 Provider">
                <span className="model-main"><span className="model-glyph">{provider?.type.slice(0, 1).toUpperCase() || 'K'}</span><span className="model-copy"><span className="model-name">{provider?.model || 'Provider'}</span><span className="model-sub">{provider?.type || '读取中'} · {provider?.configured ? '已配置' : '待配置'}</span></span></span><ChevronDown size={14} />
              </button>
              {modelMenuOpen && <div className="model-menu show"><div className="model-menu-head"><strong>当前模型路由</strong><span>配置镜像 · 只读</span></div><div className="model-current"><span className="model-glyph">{provider?.type.slice(0, 1).toUpperCase() || 'K'}</span><span><strong>{provider?.model || '未读取模型'}</strong><small>{provider?.base_url || '未配置兼容端点'}</small></span></div><button onClick={() => { setModelMenuOpen(false); navigate(withContext('/settings')) }}>查看 Provider 配置 <span>›</span></button></div>}
            </div>
            <div className="font-size-wrap" ref={fontSizeRef}>
              <button className="font-size-button" aria-expanded={fontSizeMenuOpen} aria-label="调整界面字号" title="调整界面字号" onClick={() => setFontSizeMenuOpen((value) => !value)}><span className="font-size-aa">Aa</span><span className="font-size-caption">字号</span><strong>{fontSizeLabels[ui.fontSize]}</strong><ChevronDown size={13} /></button>
              {fontSizeMenuOpen && <div className="font-size-menu show" role="menu"><div className="font-size-menu-head"><strong>界面字号</strong><span>仅调整文字，不改变功能布局</span></div>{(['small', 'medium', 'large'] as const).map((size) => <button key={size} className={`font-size-option ${ui.fontSize === size ? 'active' : ''}`} role="menuitem" onClick={() => { ui.setFontSize(size); setFontSizeMenuOpen(false) }}><span className={`font-sample ${size}`}>Aa</span><span><strong>{fontSizeLabels[size]}</strong><small>{size === 'small' ? '紧凑' : size === 'medium' ? '默认' : '舒适'}</small></span><i>{ui.fontSize === size ? '✓' : ''}</i></button>)}</div>}
            </div>
            <button className="icon-btn theme-toggle-btn" onClick={() => ui.setTheme(ui.theme === 'dark' ? 'light' : 'dark')} aria-label={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'} title={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'}>{ui.theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}</button>
            <button className="icon-btn" onClick={() => ui.setDrawerOpen(true)} aria-label="运行状态" title="运行状态"><Activity size={18} /></button>
            <button className="icon-btn" onClick={() => setCommandOpen(true)} aria-label="命令面板" title="命令面板"><Search size={18} /></button>
          </div>
        </header>
        <section className="content"><Outlet context={{ user, sessionId, setSessionId, overview, refreshOverview: () => { void overviewQuery.refetch() } } satisfies ShellOutletContext} /></section>
      </main>

      <aside className={`drawer ${ui.drawerOpen ? 'show' : ''}`} aria-hidden={!ui.drawerOpen} inert={!ui.drawerOpen}>
        <div className="drawer-head"><strong>运行状态</strong><button className="icon-btn" onClick={() => ui.setDrawerOpen(false)} aria-label="关闭"><X size={17} /></button></div>
        <div className="drawer-body">
          <section className="drawer-section"><div className="drawer-title"><strong>当前上下文</strong><span>{sessionId ? sessionLabel(sessionId) : '新会话'}</span></div><div className="drawer-context-number"><strong>{formatTokens(contextTotal)} / {formatTokens(contextLimit)}</strong><span>{context?.usage.estimated ? '本地估算' : contextTotal ? 'Provider 统计' : '等待首轮统计'}</span></div><div className="progress-line"><i style={{ width: `${contextPercent}%` }} /></div><div className="drawer-context-meta"><span>占用 {contextPercent}%</span><span>上限 {contextLimit.toLocaleString()} Token</span></div></section>
          <section className="drawer-section"><div className="drawer-title"><strong>核心与能力</strong><span>真实只读状态</span></div><div className="state-row"><span>●</span><span>{healthQuery.data?.service || 'kemo-agent-web'}</span><span className={`state-pill ${healthQuery.isError ? 'muted' : ''}`}>{healthQuery.isSuccess ? '正常' : '不可用'}</span></div><div className="state-row"><span>M</span><span>{provider?.model || 'Provider'}</span><span className="state-pill">{provider?.configured ? '已配置' : '待配置'}</span></div><div className="state-row"><span>K</span><span>文件知识</span><span className="state-pill">{overview?.counts.knowledge_documents ?? '—'} 项</span></div><div className="state-row"><span>T</span><span>可用工具</span><span className="state-pill">{overview?.counts.enabled_tools ?? '—'} 个</span></div><div className="state-row"><span>A</span><span>子代理</span><span className="state-pill">{overview?.counts.enabled_agents ?? '—'} 个</span></div></section>
          <section className="drawer-section"><div className="drawer-title"><strong>最近活动</strong><span>{overview?.activities.length || 0} 条</span></div><div className="drawer-activities">{overview?.activities.slice(0, 4).map((item, index) => <div className="drawer-activity" key={`${item.type}:${item.updated_at}:${index}`}><span>{item.type === 'session' ? <MessageSquarePlus size={13} /> : item.type === 'plan' ? <ListChecks size={13} /> : <FileSearch size={13} />}</span><span><strong>{item.title}</strong><small>{formatDateTime(item.updated_at)} · {statusLabel(item.status)}</small></span></div>)}{!overview?.activities.length && <span className="drawer-empty">暂无可显示的运行活动</span>}</div></section>
        </div>
      </aside>
      {ui.drawerOpen && <button className="drawer-backdrop" aria-label="关闭运行状态" onClick={() => ui.setDrawerOpen(false)} />}

      {commandOpen && <div className="command-layer show" onMouseDown={(event) => { if (event.target === event.currentTarget) setCommandOpen(false) }}><div className="command-box" role="dialog" aria-label="全局搜索与命令" aria-modal="true"><div className="command-search-row"><Search size={17} /><input ref={commandInputRef} className="command-input" value={commandQuery} onChange={(event) => setCommandQuery(event.target.value)} placeholder="搜索页面、状态或命令…" /><kbd>Esc</kbd></div><div className="command-list">{filteredCommands.map((command) => <button className="command-item" key={command.label} onClick={() => runCommand(command.action)}><span><strong>{command.label}</strong><small>{command.detail}</small></span><kbd>{command.shortcut}</kbd></button>)}{!filteredCommands.length && <div className="command-empty">没有匹配的命令</div>}</div><div className="command-foot">Ctrl K 打开 · 方向键导航将在后续增强</div></div></div>}
    </div>
  )
}
