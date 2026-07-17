import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  Bot,
  BrainCircuit,
  ChevronLeft,
  CircleGauge,
  ListChecks,
  Menu,
  Moon,
  Settings,
  Sun,
  Wrench,
  X,
} from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import { getHealth, getSessions, getUsers } from '../api/client'
import { useUiStore } from '../store/ui'

export interface ShellOutletContext {
  user: string
  sessionId: string
  setSessionId: (sessionId: string) => void
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

  useEffect(() => {
    document.documentElement.dataset.theme = ui.theme
    document.documentElement.dataset.fontSize = ui.fontSize
  }, [ui.theme, ui.fontSize])

  const withContext = (path: string, nextSession = sessionId) => {
    const next = new URLSearchParams()
    if (user) next.set('user', user)
    if (nextSession) next.set('session', nextSession)
    return `${path}?${next.toString()}`
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

  return (
    <div className={`app ${ui.sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar" aria-label="主导航">
        <div className="sidebar-head">
          <div className="brand-mark"><img src="/kemo-agent.jpg" alt="kemo-agent" /></div>
          <div className="brand-copy"><strong>kemo-agent</strong><span>Personal Agent Runtime</span></div>
          <button className="sidebar-toggle" onClick={ui.toggleSidebar} aria-label={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'}>
            <ChevronLeft size={16} />
          </button>
        </div>
        <nav className="nav-section">
          {navItems.map(({ path, label, icon: Icon }) => (
            <NavLink key={path} to={withContext(path)} className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>
              <span className="nav-icon"><Icon size={20} /></span>
              <span className="nav-label">{label}</span>
              <span className="nav-tip">{label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-rule" />
        <section className="recent-block">
          <div className="recent-title">最近对话</div>
          <div className="recent-list">
            {sessionsQuery.isLoading && <span className="sidebar-note">正在加载…</span>}
            {sessionsQuery.isError && <span className="sidebar-note error">会话加载失败</span>}
            {sessionsQuery.data?.sessions.slice(0, 8).map((session) => (
              <button
                key={session.session_id}
                className={`recent-btn ${session.session_id === sessionId ? 'active' : ''}`}
                onClick={() => navigate(withContext('/chat', session.session_id))}
              >
                <strong>{session.session_id}</strong>
                <span>{session.rounds} 轮 · {session.updated_at || '时间未知'}</span>
              </button>
            ))}
            {sessionsQuery.data?.sessions.length === 0 && <span className="sidebar-note">暂无 Web 会话</span>}
          </div>
        </section>
        <div className="sidebar-spacer" />
        <label className="role-button user-select-label">
          <span className="role-avatar">{user.slice(0, 1).toUpperCase() || '?'}</span>
          <span className="role-copy"><strong>{user || '未选择用户'}</strong><span>当前用户</span></span>
          <select value={user} onChange={(event) => setUser(event.target.value)} aria-label="切换当前用户">
            {usersQuery.data?.users.map((item) => <option key={item.name} value={item.name}>{item.name}</option>)}
          </select>
        </label>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="top-left">
            <button className="mobile-menu" onClick={ui.toggleSidebar} aria-label="切换导航"><Menu size={18} /></button>
            <div className="page-title">{pageTitles[location.pathname] || 'kemo-agent'}</div>
            <div className="agent-line">
              <span className={`status-dot ${healthQuery.isError ? 'offline' : ''}`} />
              <span>{healthQuery.isSuccess ? 'Web 核心正常' : healthQuery.isError ? 'Web 后端不可用' : '正在连接'}</span>
            </div>
            {user && <span className="role-chip">{user}</span>}
          </div>
          <div className="top-right">
            <span className="readonly-model"><CircleGauge size={15} /> 后端运行态</span>
            <label className="font-size-control">
              <span>Aa</span>
              <select value={ui.fontSize} onChange={(event) => ui.setFontSize(event.target.value as 'small' | 'medium' | 'large')} aria-label="界面字号">
                <option value="small">小</option><option value="medium">中</option><option value="large">大</option>
              </select>
            </label>
            <button className="icon-btn" onClick={() => ui.setTheme(ui.theme === 'dark' ? 'light' : 'dark')} aria-label="切换主题">
              {ui.theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button className="icon-btn" onClick={() => ui.setDrawerOpen(true)} aria-label="运行状态"><CircleGauge size={18} /></button>
          </div>
        </header>
        <section className="content">
          <Outlet context={{ user, sessionId, setSessionId } satisfies ShellOutletContext} />
        </section>
      </main>

      <aside className={`drawer ${ui.drawerOpen ? 'show' : ''}`} aria-hidden={!ui.drawerOpen}>
        <div className="drawer-head"><strong>运行状态</strong><button className="icon-btn" onClick={() => ui.setDrawerOpen(false)} aria-label="关闭"><X size={17} /></button></div>
        <div className="drawer-body">
          <section className="drawer-section">
            <div className="drawer-title"><strong>Web 后端</strong><span>真实健康检查</span></div>
            <div className="state-row"><span>●</span><span>{healthQuery.data?.service || 'kemo-agent-web'}</span><span className={`state-pill ${healthQuery.isError ? 'muted' : ''}`}>{healthQuery.isSuccess ? '正常' : '不可用'}</span></div>
            <div className="state-row"><span>U</span><span>当前用户</span><span className="state-pill">{user || '未选择'}</span></div>
            <div className="state-row"><span>S</span><span>当前会话</span><span className="state-pill">{sessionId || '新会话'}</span></div>
          </section>
          <section className="drawer-section"><div className="drawer-title"><strong>能力边界</strong><span>首期</span></div><p className="drawer-copy">当前只接入用户、Web 会话、文本历史与 POST SSE 聊天。任务、知识、技能、感知和配置管理接口尚未开放。</p></section>
        </div>
      </aside>
      {ui.drawerOpen && <button className="drawer-backdrop" aria-label="关闭运行状态" onClick={() => ui.setDrawerOpen(false)} />}
    </div>
  )
}
