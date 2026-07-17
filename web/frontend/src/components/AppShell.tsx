import { useEffect, useRef, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  BookOpen,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronLeft,
  CircleGauge,
  ListChecks,
  Menu,
  Moon,
  Search,
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

const fontSizeLabels: Record<string, string> = { small: '小', medium: '中', large: '大' }

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

  const [roleMenuOpen, setRoleMenuOpen] = useState(false)
  const [fontSizeMenuOpen, setFontSizeMenuOpen] = useState(false)
  const roleMenuRef = useRef<HTMLDivElement>(null)
  const fontSizeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    document.documentElement.dataset.theme = ui.theme
    document.documentElement.dataset.fontSize = ui.fontSize
  }, [ui.theme, ui.fontSize])

  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (roleMenuRef.current && !roleMenuRef.current.contains(e.target as Node)) setRoleMenuOpen(false)
      if (fontSizeRef.current && !fontSizeRef.current.contains(e.target as Node)) setFontSizeMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

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
    setRoleMenuOpen(false)
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
          <div className="brand-mark"><img src="/kemo-agent.jpg" alt="kemo-agent logo" /></div>
          <div className="brand-copy"><strong>kemo-agent</strong><span>Personal Agent Runtime</span></div>
          <button className="sidebar-toggle" onClick={ui.toggleSidebar} aria-label={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'} title={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'}>
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
        <div className="role-wrap" ref={roleMenuRef}>
          <button
            className="role-button"
            onClick={() => setRoleMenuOpen((v) => !v)}
            aria-label="切换当前用户"
            title="切换当前用户"
          >
            <span className="role-avatar">{user.slice(0, 1).toUpperCase() || '?'}</span>
            <span className="role-copy"><strong>{user || '未选择用户'}</strong><span>users/{user || '—'} · 当前用户</span></span>
            <span className="role-chevron"><ChevronDown size={15} /></span>
          </button>
          {roleMenuOpen && (
            <div className="role-menu show">
              <div className="role-menu-head">
                <span><strong>切换用户</strong><small>载入对应 users/&lt;user_id&gt; 运行态</small></span>
                <span className="space-mode-badge">单用户执行</span>
              </div>
              {usersQuery.data?.users.map((item) => (
                <button
                  key={item.name}
                  className={`role-option ${item.name === user ? 'active' : ''}`}
                  onClick={() => setUser(item.name)}
                >
                  <span className="mini-avatar">{item.name.slice(0, 1).toUpperCase()}</span>
                  <span><strong>{item.name}</strong><small>用户运行态</small><em className="space-option-id">users/{item.name}</em></span>
                  <span className="check">{item.name === user ? '✓' : ''}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      </aside>

      <main className="workspace">
        <header className="topbar">
          <div className="top-left">
            <button className="mobile-menu icon-btn" onClick={ui.toggleSidebar} aria-label="切换导航"><Menu size={18} /></button>
            <div className="page-title">{pageTitles[location.pathname] || 'kemo-agent'}</div>
            <div className="agent-line">
              <span className={`status-dot ${healthQuery.isError ? 'offline' : ''}`} />
              <span>{healthQuery.isSuccess ? '核心运行正常' : healthQuery.isError ? 'Web 后端不可用' : '正在连接'}</span>
            </div>
            {user && <span className="role-chip">{user}</span>}
          </div>
          <div className="top-right">
            <button className="context-button" title="查看上下文与运行状态" onClick={() => ui.setDrawerOpen(true)}>
              <span className="context-main">
                <span className="context-icon"><CircleGauge size={14} /></span>
                <span className="context-copy"><strong>上下文窗口</strong><span>运行态</span></span>
              </span>
              <span className="context-mini"><b>—</b><span className="context-track"><i /></span></span>
            </button>
            <div className="font-size-wrap" ref={fontSizeRef}>
              <button
                className="font-size-button"
                aria-expanded={fontSizeMenuOpen}
                aria-label="调整界面字号"
                title="调整界面字号"
                onClick={() => setFontSizeMenuOpen((v) => !v)}
              >
                <span className="font-size-aa">Aa</span>
                <span className="font-size-caption">字号</span>
                <strong>{fontSizeLabels[ui.fontSize]}</strong>
                <ChevronDown size={13} />
              </button>
              {fontSizeMenuOpen && (
                <div className="font-size-menu show" role="menu">
                  <div className="font-size-menu-head"><strong>界面字号</strong><span>仅调整文字，不改变功能布局</span></div>
                  {(['small', 'medium', 'large'] as const).map((size) => (
                    <button
                      key={size}
                      className={`font-size-option ${ui.fontSize === size ? 'active' : ''}`}
                      role="menuitem"
                      onClick={() => { ui.setFontSize(size); setFontSizeMenuOpen(false) }}
                    >
                      <span className={`font-sample ${size}`}>Aa</span>
                      <span><strong>{fontSizeLabels[size]}</strong><small>{size === 'small' ? '紧凑' : size === 'medium' ? '默认' : '舒适'}</small></span>
                      <i>{ui.fontSize === size ? '✓' : ''}</i>
                    </button>
                  ))}
                </div>
              )}
            </div>
            <button
              className="icon-btn theme-toggle-btn"
              onClick={() => ui.setTheme(ui.theme === 'dark' ? 'light' : 'dark')}
              aria-label={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'}
              title={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'}
            >
              {ui.theme === 'dark' ? <Sun size={18} /> : <Moon size={18} />}
            </button>
            <button className="icon-btn" onClick={() => ui.setDrawerOpen(true)} aria-label="运行状态" title="运行状态"><CircleGauge size={18} /></button>
            <button className="icon-btn" aria-label="命令面板" title="命令面板"><Search size={18} /></button>
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
          <section className="drawer-section">
            <div className="drawer-title"><strong>能力边界</strong><span>首期</span></div>
            <p className="drawer-copy">当前只接入用户、Web 会话、文本历史与 POST SSE 聊天。任务、知识、技能、感知和配置管理接口尚未开放。</p>
          </section>
        </div>
      </aside>
      {ui.drawerOpen && <button className="drawer-backdrop" aria-label="关闭运行状态" onClick={() => ui.setDrawerOpen(false)} />}
    </div>
  )
}
