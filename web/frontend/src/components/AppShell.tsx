import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  ArrowLeft,
  BookOpen,
  Bot,
  BrainCircuit,
  ChevronDown,
  ChevronLeft,
  CircleGauge,
  FileSearch,
  FolderOpen,
  Brain,
  History,
  RadioTower,
  ListChecks,
  Menu,
  MessageSquarePlus,
  Moon,
  RotateCcw,
  Save,
  Search,
  Settings,
  Shapes,
  Slash,
  Sun,
  Trash2,
  Wrench,
  X,
  Zap,
} from 'lucide-react'
import { NavLink, Outlet, useLocation, useNavigate, useSearchParams } from 'react-router-dom'
import {
  closeSession,
  createSession,
  deleteAllSessions,
  deleteSession,
  getActiveSession,
  getHealth,
  getLogoUrl,
  getOverview,
  getPreferences,
  getSessions,
  getUserAvatarUrl,
  getUsers,
  logoutAuth,
  patchPreferences,
  patchUserConfig,
  releaseSessionLease,
  retrySessionSummary,
  touchSessionLease,
  AVATAR_UPDATED_EVENT,
} from '../api/client'
import { HistorySearchDrawer } from './HistorySearchDrawer'
import { ReasoningEffortSelect } from './ReasoningEffortSelect'
import { UserProfileCard } from './UserProfileCard'
import type { AuthStatusResponse, ChatItem, OverviewResponse, SessionsResponse } from '../types/api'
import { useUiStore } from '../store/ui'
import { createSessionChannel, getPageClientId } from '../sessionClient'
import { normalizeReasoningEffort, reasoningEffortLabel, type ReasoningEffort } from '../reasoningEffort'

export interface ShellOutletContext {
  user: string
  userAvatarUrl?: string
  sessionId: string
  clientId: string
  chatRunning: boolean
  setChatRunning: (running: boolean) => void
  chatRunId: string
  setChatRunId: (runId: string) => void
  setChatAbortController: (controller: AbortController | null) => void
  abortChatRun: () => void
  chatRuns: Record<string, ChatRunSnapshot>
  beginChatRun: (user: string, sessionId: string, runId: string, historyUserMessages: number) => void
  updateChatRunItems: (user: string, sessionId: string, updater: ChatItemsUpdater) => void
  queueNextTurnMessage: (user: string, sessionId: string, message: PendingNextTurnMessage) => void
  setNextTurnMessageStatus: (user: string, sessionId: string, messageId: string, status: PendingNextTurnMessage['status'], error?: string) => void
  removeNextTurnMessage: (user: string, sessionId: string, messageId: string) => void
  finishChatRun: (user: string, sessionId: string, committed: boolean) => void
  clearChatRun: (user: string, sessionId: string) => void
  setSessionId: (sessionId: string) => void
  detachSession: () => void
  notifySessionDeleted: (sessionId: string) => void
  sessions: SessionsResponse['sessions']
  refreshSessions: () => Promise<SessionsResponse | undefined>
  createNewSession: () => Promise<string | undefined>
  overview?: OverviewResponse
  refreshOverview: () => void
  openCommandPanel: () => void
}

export type ChatItemsUpdater = ChatItem[] | ((items: ChatItem[]) => ChatItem[])

export interface PendingNextTurnMessage {
  id: string
  content: string
  historyUserMessages: number
  status: 'queued' | 'sending' | 'error'
  error?: string
}

export interface ChatRunSnapshot {
  items: ChatItem[]
  phase: 'streaming' | 'awaiting_history' | 'idle'
  runId: string
  historyUserMessages: number
  nextTurnQueue: PendingNextTurnMessage[]
}

export function chatRunKey(user: string, sessionId: string) {
  return JSON.stringify([user, sessionId])
}

export type ConversationCommandAction = 'save' | 'clear' | 'compress' | 'retry'
export const CONVERSATION_COMMAND_EVENT = 'kemo:conversation-command'

const slashCommands = [
  { command: '/new [名称]', description: '新建并切换会话' },
  { command: '/sessions', description: '列出全部已提交会话' },
  { command: '/use <会话ID>', description: '切换到指定会话' },
  { command: '/clear', description: '清空当前会话' },
  { command: '/history', description: '查看当前会话历史' },
  { command: '/status', description: '查看当前上下文占用状态' },
  { command: '/compress', description: '手动压缩当前上下文' },
  { command: '/memory', description: '列出当前用户记忆' },
  { command: '/remember <内容>', description: '保存一条永久记忆' },
  { command: '/forget <记忆ID或关键词>', description: '删除匹配的记忆' },
  { command: '/plans', description: '列出任务计划' },
  { command: '/plan <目标>', description: '创建任务计划' },
  { command: '/plan-show <计划ID>', description: '查看指定任务计划' },
  { command: '/plan-approve <计划ID>', description: '批准并执行任务计划' },
  { command: '/plan-pause <计划ID>', description: '暂停任务计划' },
  { command: '/plan-resume <计划ID>', description: '恢复任务计划' },
  { command: '/plan-cancel <计划ID>', description: '取消任务计划' },
  { command: '/crons', description: '列出定时任务' },
  { command: '/cron <自然语言要求>', description: '创建定时任务' },
  { command: '/cron-show <任务ID>', description: '查看指定定时任务' },
  { command: '/cron-pause <任务ID>', description: '暂停定时任务' },
  { command: '/cron-resume <任务ID>', description: '恢复定时任务' },
  { command: '/cron-cancel <任务ID>', description: '取消定时任务' },
  { command: '/cron-run <任务ID>', description: '立即执行定时任务' },
  { command: '/cron-start', description: '启动 CLI 定时调度器' },
  { command: '/cron-stop', description: '停止 CLI 定时调度器' },
  { command: '/exit 或 /quit', description: '退出 CLI 交互模式' },
]

const navGroups = [
  {
    label: '核心工作区',
    showLabel: true,
    items: [
      { path: '/chat', label: '对话', icon: Bot },
      { path: '/tasks', label: '任务', icon: ListChecks },
      { path: '/knowledge', label: '知识库', icon: BookOpen },
      { path: '/memory', label: '记忆', icon: Brain },
    ],
  },
  {
    label: '运行能力',
    showLabel: false,
    items: [
      { path: '/agents', label: '子智能体', icon: Bot },
      { path: '/skills', label: '工具与技能', icon: Wrench },
      { path: '/sense', label: '感知', icon: BrainCircuit },
      { path: '/expand', label: '拓展', icon: Shapes },
    ],
  },
  {
    label: '资源与系统',
    showLabel: true,
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
  const clientId = useMemo(() => getPageClientId(), [])
  const [sessionTransitioning, setSessionTransitioning] = useState(false)
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
  const activeSessionQuery = useQuery({
    queryKey: ['active-session', user, clientId],
    queryFn: () => getActiveSession(user, clientId),
    enabled: Boolean(user) && !sessionId && !sessionTransitioning,
    staleTime: 0,
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
  const reasoningEffortMutation = useMutation({
    mutationFn: ({ targetUser, reasoningEffort }: { targetUser: string; reasoningEffort: ReasoningEffort }) => patchUserConfig(targetUser, {
      provider: { reasoning_effort: reasoningEffort },
    }),
    onSuccess: async (_, variables) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['overview', variables.targetUser] }),
        queryClient.invalidateQueries({ queryKey: ['settings', variables.targetUser] }),
        queryClient.invalidateQueries({ queryKey: ['user-config', variables.targetUser] }),
        queryClient.invalidateQueries({ queryKey: ['runtime-status', variables.targetUser] }),
      ])
    },
  })

  const [fontSizeMenuOpen, setFontSizeMenuOpen] = useState(false)
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [commandMode, setCommandMode] = useState<'main' | 'slash'>('main')
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false)
  const [historySwitchingSessionId, setHistorySwitchingSessionId] = useState('')
  const [historySwitchError, setHistorySwitchError] = useState('')
  const [logoutPending, setLogoutPending] = useState(false)
  const [avatarRevision, setAvatarRevision] = useState(0)
  const [chatRunning, setChatRunning] = useState(false)
  const [chatRunId, setChatRunId] = useState('')
  const [chatRuns, setChatRuns] = useState<Record<string, ChatRunSnapshot>>({})
  const chatAbortControllerRef = useRef<AbortController | null>(null)
  const locationRef = useRef(location)
  const userRef = useRef(user)
  const sessionIdRef = useRef(sessionId)
  const sessionChannelRef = useRef<ReturnType<typeof createSessionChannel> | null>(null)
  const fontSizeRef = useRef<HTMLDivElement>(null)
  const modelMenuRef = useRef<HTMLDivElement>(null)
  const commandInputRef = useRef<HTMLInputElement>(null)

  locationRef.current = location
  userRef.current = user
  sessionIdRef.current = sessionId

  const beginChatRun = useCallback((runUser: string, runSessionId: string, runId: string, historyUserMessages: number) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => ({
      ...current,
      [key]: {
        items: current[key]?.items ?? [],
        phase: 'streaming',
        runId,
        historyUserMessages,
        nextTurnQueue: current[key]?.nextTurnQueue ?? [],
      },
    }))
  }, [])

  const updateChatRunItems = useCallback((runUser: string, runSessionId: string, updater: ChatItemsUpdater) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key] ?? { items: [], phase: 'idle' as const, runId: '', historyUserMessages: 0, nextTurnQueue: [] }
      const items = typeof updater === 'function' ? updater(existing.items) : updater
      return { ...current, [key]: { ...existing, items } }
    })
  }, [])

  const queueNextTurnMessage = useCallback((runUser: string, runSessionId: string, message: PendingNextTurnMessage) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key] ?? { items: [], phase: 'idle' as const, runId: '', historyUserMessages: message.historyUserMessages, nextTurnQueue: [] }
      if (existing.nextTurnQueue.some((item) => item.id === message.id)) return current
      return { ...current, [key]: { ...existing, nextTurnQueue: [...existing.nextTurnQueue, message] } }
    })
  }, [])

  const setNextTurnMessageStatus = useCallback((runUser: string, runSessionId: string, messageId: string, status: PendingNextTurnMessage['status'], error?: string) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key]
      if (!existing) return current
      return {
        ...current,
        [key]: {
          ...existing,
          nextTurnQueue: existing.nextTurnQueue.map((item) => item.id === messageId ? { ...item, status, error } : item),
        },
      }
    })
  }, [])

  const removeNextTurnMessage = useCallback((runUser: string, runSessionId: string, messageId: string) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key]
      if (!existing) return current
      return { ...current, [key]: { ...existing, nextTurnQueue: existing.nextTurnQueue.filter((item) => item.id !== messageId) } }
    })
  }, [])

  const finishChatRun = useCallback((runUser: string, runSessionId: string, committed: boolean) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key]
      if (!existing) return current
      return { ...current, [key]: { ...existing, phase: committed ? 'awaiting_history' : 'idle' } }
    })
  }, [])

  const clearChatRun = useCallback((runUser: string, runSessionId: string) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key]
      if (!existing) return current
      if (existing.nextTurnQueue.length) {
        return { ...current, [key]: { ...existing, items: [], phase: 'idle', runId: '' } }
      }
      const next = { ...current }
      delete next[key]
      return next
    })
  }, [])

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
        setHistoryDrawerOpen(false)
        ui.setDrawerOpen(false)
        setCommandOpen((value) => !value)
      }
      if (event.key === 'Escape') {
        setCommandOpen(false)
        setHistoryDrawerOpen(false)
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
    else {
      setCommandQuery('')
      setCommandMode('main')
    }
  }, [commandOpen])

  useEffect(() => {
    if (!historyDrawerOpen) return
    const sessions = sessionsQuery.data?.sessions ?? []
    const hasPendingSummary = sessions.some(
      (session) => ['queued', 'processing'].includes(session.summary_status || ''),
    )
    let delay = hasPendingSummary ? 2_000 : 0
    if (!delay) {
      const failed = sessions.filter(
        (session) => ['failed', 'retry_wait'].includes(session.summary_status || ''),
      )
      if (!failed.length) return
      const retryTimes = failed
        .map((session) => Date.parse(session.summary_retry_at || ''))
        .filter((value) => Number.isFinite(value))
      const nextRetryAt = retryTimes.length ? Math.min(...retryTimes) : Date.now() + 30_000
      delay = Math.min(30_000, Math.max(2_000, nextRetryAt - Date.now() + 1_500))
    }
    const timer = window.setTimeout(() => { void sessionsQuery.refetch() }, delay)
    return () => window.clearTimeout(timer)
  }, [historyDrawerOpen, sessionsQuery.data?.sessions])

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
    if (nextSession) setSessionTransitioning(false)
    setParams(next)
  }

  const detachSession = () => {
    setSessionTransitioning(true)
    queryClient.removeQueries({ queryKey: ['active-session', user, clientId] })
    setSessionId('')
  }

  const notifySessionDeleted = (deletedSessionId: string) => {
    if (!user || !deletedSessionId) return
    sessionChannelRef.current?.post({
      type: 'session-deleted',
      user,
      sessionId: deletedSessionId,
      clientId,
    })
  }

  useEffect(() => {
    const active = activeSessionQuery.data?.session?.session_id
    if (!sessionId && !sessionTransitioning && active) setSessionId(active)
  }, [activeSessionQuery.data?.session?.session_id, sessionId, sessionTransitioning])

  useEffect(() => {
    if (!user || !sessionId) return
    let disposed = false
    const touch = () => {
      if (!disposed) void touchSessionLease(user, sessionId, clientId).catch(() => undefined)
    }
    touch()
    const heartbeat = window.setInterval(touch, 15_000)
    return () => {
      disposed = true
      window.clearInterval(heartbeat)
      void releaseSessionLease(user, sessionId, clientId, true).catch(() => undefined)
    }
  }, [clientId, sessionId, user])

  useEffect(() => {
    const channel = createSessionChannel((event) => {
      if (event.clientId === clientId || event.user !== userRef.current) return
      queryClient.removeQueries({ queryKey: ['history', event.user, event.sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['sessions', event.user] })
      void queryClient.invalidateQueries({ queryKey: ['overview', event.user] })
      if (sessionIdRef.current === event.sessionId) {
        queryClient.removeQueries({ queryKey: ['active-session', event.user, clientId] })
        setSessionTransitioning(false)
        const current = locationRef.current
        const next = new URLSearchParams(current.search)
        next.delete('session')
        navigate({ pathname: current.pathname, search: `?${next.toString()}`, hash: current.hash }, { replace: true })
        setHistorySwitchError('当前对话已在另一个页面删除，已为本页面解除绑定。')
      }
    })
    sessionChannelRef.current = channel
    return () => {
      sessionChannelRef.current = null
      channel.close()
    }
  }, [clientId, navigate, queryClient])

  const refreshSessions = async () => (await sessionsQuery.refetch()).data

  const createNewSession = async () => {
    if (!user || chatRunning) return
    const requestedUser = user
    try {
      const result = await createSession(requestedUser, clientId)
      const current = locationRef.current
      const next = new URLSearchParams(current.search)
      const currentUser = next.get('user') || userRef.current
      if (currentUser === requestedUser) {
        next.set('user', requestedUser)
        next.set('session', result.session.session_id)
        setSessionTransitioning(false)
        navigate({ pathname: current.pathname, search: `?${next.toString()}`, hash: current.hash }, { replace: true })
      }
      void sessionsQuery.refetch()
      void overviewQuery.refetch()
      return result.session.session_id
    } catch (error) {
      setSessionTransitioning(false)
      throw error
    }
  }

  const runCommand = (action: () => void) => {
    setCommandOpen(false)
    action()
  }

  const openCommandPanel = () => {
    setHistoryDrawerOpen(false)
    ui.setDrawerOpen(false)
    setCommandMode('main')
    setCommandOpen(true)
  }

  const openContextDrawer = () => {
    setCommandOpen(false)
    setHistoryDrawerOpen(false)
    ui.setDrawerOpen(true)
  }

  const openHistoryDrawer = () => {
    setCommandOpen(false)
    ui.setDrawerOpen(false)
    setHistorySwitchError('')
    setHistoryDrawerOpen(true)
    void sessionsQuery.refetch()
  }

  const selectHistorySession = async (targetSessionId: string) => {
    if (!user || chatRunning || historySwitchingSessionId) return
    if (targetSessionId === sessionId) {
      setHistoryDrawerOpen(false)
      navigate(withContext('/chat', targetSessionId))
      return
    }
    setHistorySwitchingSessionId(targetSessionId)
    setHistorySwitchError('')
    try {
        const currentSession = sessionsQuery.data?.sessions.find((session) => session.session_id === sessionId)
        if (sessionId && currentSession?.state !== 'closed') {
          await closeSession(user, sessionId, clientId)
        }
      await sessionsQuery.refetch()
      setHistoryDrawerOpen(false)
      navigate(withContext('/chat', targetSessionId))
    } catch (error) {
      setHistorySwitchError(error instanceof Error ? error.message : '保存当前对话或切换历史对话失败')
    } finally {
      setHistorySwitchingSessionId('')
    }
  }

  const deleteHistorySession = async (targetSessionId: string) => {
    if (!user) throw new Error('当前没有可用用户')
    if (chatRunning && targetSessionId === sessionId) throw new Error('当前对话正在运行，暂时不能删除')
    await deleteSession(user, targetSessionId, clientId)
    notifySessionDeleted(targetSessionId)
    queryClient.removeQueries({ queryKey: ['history', user, targetSessionId] })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessions', user] }),
      queryClient.invalidateQueries({ queryKey: ['overview', user] }),
    ])
    if (targetSessionId === sessionId) {
      queryClient.removeQueries({ queryKey: ['active-session', user, clientId] })
      setSessionId('')
    }
  }

  const retryHistorySummary = async (targetSessionId: string) => {
    if (!user) throw new Error('当前没有可用用户')
    await retrySessionSummary(user, targetSessionId)
    await sessionsQuery.refetch()
  }

  const deleteAllHistorySessions = async () => {
    if (!user) throw new Error('当前没有可用用户')
    if (chatRunning) throw new Error('当前对话正在运行，暂时不能删除全部历史对话')
    await deleteAllSessions(user)
    queryClient.removeQueries({ queryKey: ['history', user] })
    queryClient.removeQueries({ queryKey: ['active-session', user, clientId] })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessions', user] }),
      queryClient.invalidateQueries({ queryKey: ['overview', user] }),
    ])
    if (sessionId) setSessionId('')
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

  const dispatchConversationCommand = (action: ConversationCommandAction) => {
    window.dispatchEvent(new CustomEvent<ConversationCommandAction>(CONVERSATION_COMMAND_EVENT, { detail: action }))
  }
  const conversationCommands = useMemo(() => [
    { id: 'save', label: '保存此对话，创建新对话', detail: '提取记忆、保存当前归档并开启新上下文', keywords: 'save new conversation 保存 新建', icon: Save, action: () => dispatchConversationCommand('save') },
    { id: 'clear', label: '清空此对话', detail: '删除当前归档，然后创建一个新对话', keywords: 'clear delete conversation 清空 删除', icon: Trash2, tone: 'danger', action: () => dispatchConversationCommand('clear') },
    { id: 'compress', label: '手动进行一次上下文压缩', detail: '整理当前上下文并同步提取待处理记忆', keywords: 'compress context token 压缩 上下文', icon: Zap, action: () => dispatchConversationCommand('compress') },
    { id: 'retry', label: '重新发送一次消息', detail: '撤销上一轮并重放原消息，不增加对话轮数', keywords: 'retry regenerate resend 重新发送 重新生成', icon: RotateCcw, action: () => dispatchConversationCommand('retry') },
  ], [])
  const navigationCommands = useMemo(() => [
    { id: 'chat', label: '打开对话', detail: '回到当前聊天与消息输入界面', keywords: 'chat conversation 对话', icon: Bot, action: () => navigate(withContext('/chat')) },
    { id: 'tasks', label: '查看任务中枢', detail: '计划、Cron 与执行记录', keywords: 'task plan cron 任务', icon: ListChecks, action: () => navigate(withContext('/tasks')) },
    { id: 'knowledge', label: '查询文件知识库', detail: '用户、共享与全局知识索引', keywords: 'knowledge file search 知识库', icon: BookOpen, action: () => navigate(withContext('/knowledge')) },
    { id: 'memory', label: '打开记忆管理', detail: '查看和管理全部记忆层级', keywords: 'memory improve 记忆', icon: Brain, action: () => navigate(withContext('/memory')) },
    { id: 'agents', label: '查看子智能体', detail: '内置与用户子代理', keywords: 'agents subagent 子智能体', icon: Bot, action: () => navigate(withContext('/agents')) },
    { id: 'skills', label: '查看工具与技能', detail: '工具与技能注册来源', keywords: 'skills tools 技能 工具', icon: Wrench, action: () => navigate(withContext('/skills')) },
    { id: 'sense', label: '查看全局感知', detail: '感知来源与注入闸门', keywords: 'sense perception 感知', icon: BrainCircuit, action: () => navigate(withContext('/sense')) },
    { id: 'expand', label: '查看拓展库存', detail: '用户、共享与全局拓展模块', keywords: 'expand extensions 拓展', icon: Shapes, action: () => navigate(withContext('/expand')) },
    { id: 'files', label: '浏览文件空间', detail: '上传文件、生成产物与临时目录', keywords: 'files upload download tmp 文件', icon: FolderOpen, action: () => navigate(withContext('/files')) },
    { id: 'messages', label: '查看外部消息', detail: '身份绑定与传输插件', keywords: 'message transport 外部消息', icon: RadioTower, action: () => navigate(withContext('/messages')) },
    { id: 'status', label: '打开运行状态', detail: '上下文、Provider 与后台组件', keywords: 'runtime status context 运行状态', icon: Activity, action: () => navigate(withContext('/status')) },
    { id: 'settings', label: '打开配置', detail: 'Provider、上下文、权限与运行限制', keywords: 'settings config provider 配置', icon: Settings, action: () => navigate(withContext('/settings')) },
    { id: 'profile', label: '编辑用户资料', detail: '头像、用户人格与全局人格', keywords: 'profile avatar soul 身份 人格', icon: FileSearch, action: () => navigate(withContext('/profile')) },
  ], [navigate, sessionId, user])
  const normalizedCommandQuery = commandQuery.trim().toLocaleLowerCase()
  const matchesCommand = (command: { label: string; detail: string; keywords: string }) => (
    !normalizedCommandQuery
    || `${command.label} ${command.detail} ${command.keywords}`.toLocaleLowerCase().includes(normalizedCommandQuery)
  )
  const filteredConversationCommands = conversationCommands.filter(matchesCommand)
  const filteredNavigationCommands = navigationCommands.filter(matchesCommand)
  const showSlashCommand = !normalizedCommandQuery || `查看斜杠指令 cli 外部消息 slash / 命令 参考`.toLocaleLowerCase().includes(normalizedCommandQuery)
  const filteredSlashCommands = slashCommands.filter((item) => !normalizedCommandQuery || `${item.command} ${item.description}`.toLocaleLowerCase().includes(normalizedCommandQuery))

  const overview = overviewQuery.data
  const contextWindow = overview?.context_window
  const hasContextSnapshot = overview?.context_snapshot !== undefined
  const contextTokens = hasContextSnapshot ? overview?.context_snapshot : contextWindow?.tokens
  const contextAvailable = hasContextSnapshot
    ? Boolean(overview?.context_snapshot?.available)
    : Boolean(contextWindow?.tokens && contextWindow.tokens.source !== 'unavailable')
  const contextTotal = Number(contextTokens?.total_tokens ?? 0)
  const contextLimit = Number(contextTokens?.capacity_tokens ?? 0)
  const contextPercent = Number(contextTokens?.percent ?? 0)
  const provider = overview?.provider
  const displayedReasoningEffort = reasoningEffortMutation.isPending
    ? normalizeReasoningEffort(reasoningEffortMutation.variables?.reasoningEffort)
    : normalizeReasoningEffort(provider?.reasoning_effort)

  const settingsPath = (tab: 'users' | 'provider') => {
    const path = withContext('/settings')
    return `${path}${path.includes('?') ? '&' : '?'}tab=${tab}`
  }

  return (
    <div className={`app ${ui.sidebarCollapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="sidebar" aria-label="主导航">
        <div className="sidebar-head">
          <div className="brand-mark"><img src={getLogoUrl()} width={571} height={568} alt="kemo-agent logo" onError={(event) => { event.currentTarget.onerror = null; event.currentTarget.src = '/kemo-agent.jpg' }} /></div>
          <div className="brand-copy"><strong>kemo-agent</strong></div>
          <button className="sidebar-toggle" onClick={ui.toggleSidebar} aria-label={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'} title={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'}><ChevronLeft size={16} /></button>
        </div>
        <nav className="nav-section nav-scroll">
          {navGroups.map((group) => <div className={`nav-group ${group.showLabel ? '' : 'nav-group-unlabeled'}`} key={group.label}>
            {group.showLabel ? <span className="nav-group-label">{group.label}</span> : null}
            {group.items.map(({ path, label, icon: Icon }) => {
              const badge = path === '/tasks' ? overview?.counts.active_tasks : undefined
              return <NavLink key={path} to={withContext(path)} aria-label={label} title={ui.sidebarCollapsed ? label : undefined} className={({ isActive }) => `nav-btn ${isActive ? 'active' : ''}`}>
                <span className="nav-icon"><Icon size={20} /></span><span className="nav-label">{label}</span>
                {badge ? <span className="nav-badge">{badge}</span> : null}<span className="nav-tip">{label}</span>
              </NavLink>
            })}
          </div>)}
        </nav>
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
          onOpenUserSwitch={() => navigate(settingsPath('users'))}
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
            <button className="context-button" title="查看上下文与运行状态" onClick={openContextDrawer}>
              <span className="context-main"><span className="context-icon"><CircleGauge size="1.528rem" strokeWidth={2.3} /></span><span className="context-copy"><strong>上下文窗口</strong><span>{contextAvailable && contextLimit ? `${formatTokens(contextTotal)} / ${formatTokens(contextLimit)}` : '暂不可用'}</span></span></span>
              <span className="context-mini"><b>{contextAvailable && contextLimit ? `${contextPercent}%` : '—'}</b><span className="context-track"><i style={{ width: `${contextAvailable ? contextPercent : 0}%` }} /></span></span>
            </button>
            <div className="model-wrap" ref={modelMenuRef}>
              <button className="model-btn" onClick={() => setModelMenuOpen((value) => !value)} aria-expanded={modelMenuOpen} title="查看当前 Provider">
                <span className="model-main"><span className="model-glyph"><BrainCircuit size="1em" strokeWidth={2.2} /></span><span className="model-copy"><span className="model-name">{provider?.model || 'Provider'}</span><span className="model-sub">{provider?.type || '读取中'} · {reasoningEffortLabel(displayedReasoningEffort)} · {provider?.configured ? '已配置' : '待配置'}</span></span></span><ChevronDown size="1.25rem" strokeWidth={2.2} />
              </button>
              {modelMenuOpen && <div className="model-menu show"><div className="model-menu-head"><strong>当前模型路由</strong><span>配置镜像</span></div><div className="model-current"><span className="model-glyph"><BrainCircuit size="1em" strokeWidth={2.2} /></span><span><strong>{provider?.model || '未读取模型'}</strong><small>{provider?.base_url || '未配置兼容端点'}</small></span></div><div className="model-effort-row"><span><strong>思考强度</strong><small>下一轮请求立即生效</small></span><ReasoningEffortSelect ariaLabel="顶部模型思考强度" variant="compact" value={displayedReasoningEffort} disabled={reasoningEffortMutation.isPending} onChange={(reasoningEffort) => reasoningEffortMutation.mutate({ targetUser: user, reasoningEffort })} /></div>{reasoningEffortMutation.isError ? <div className="model-effort-error" role="alert">思考强度保存失败，请重试。</div> : null}<button onClick={() => { setModelMenuOpen(false); navigate(settingsPath('provider')) }}>编辑 Provider 配置 <span>›</span></button></div>}
            </div>
            <div className="font-size-wrap" ref={fontSizeRef}>
              <button className="font-size-button" aria-expanded={fontSizeMenuOpen} aria-label="调整界面字号" title="调整界面字号" onClick={() => setFontSizeMenuOpen((value) => !value)}><span className="font-size-aa">Aa</span><span className="font-size-caption">字号</span><strong>{fontSizeLabels[ui.fontSize]}</strong><ChevronDown size="1.181rem" strokeWidth={2.2} /></button>
              {fontSizeMenuOpen && <div className="font-size-menu show" role="menu"><div className="font-size-menu-head"><strong>界面字号</strong><span>文字与顶部布局同步适配</span></div>{(['small', 'medium', 'large'] as const).map((size) => <button key={size} className={`font-size-option ${ui.fontSize === size ? 'active' : ''}`} role="menuitem" onClick={() => { ui.setFontSize(size); if (user) void patchPreferences(user, { font_size: size }); setFontSizeMenuOpen(false) }}><span className={`font-sample ${size}`}>Aa</span><span><strong>{fontSizeLabels[size]}</strong><small>{size === 'small' ? '紧凑' : size === 'medium' ? '默认' : '舒适'}</small></span><i>{ui.fontSize === size ? '✓' : ''}</i></button>)}</div>}
            </div>
            <button className="icon-btn theme-toggle-btn" onClick={() => { const theme = ui.theme === 'dark' ? 'light' : 'dark'; ui.setTheme(theme); if (user) void patchPreferences(user, { theme }) }} aria-label={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'} title={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'}>{ui.theme === 'dark' ? <Sun size="1.736rem" strokeWidth={2.1} /> : <Moon size="1.736rem" strokeWidth={2.1} />}</button>
            <button className="icon-btn" onClick={openContextDrawer} aria-label="运行状态" title="运行状态"><Activity size="1.806rem" strokeWidth={2.1} /></button>
            <button className="icon-btn" onClick={openHistoryDrawer} aria-label="搜索历史对话" title="搜索历史对话"><History size="1.736rem" strokeWidth={2.1} /></button>
          </div>
        </header>
        <section className="content"><Outlet context={{ user, userAvatarUrl: user ? getUserAvatarUrl(user, avatarRevision) : undefined, sessionId, clientId, chatRunning, setChatRunning, chatRunId, setChatRunId, setChatAbortController, abortChatRun, chatRuns, beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus, removeNextTurnMessage, finishChatRun, clearChatRun, setSessionId, detachSession, notifySessionDeleted, sessions: sessionsQuery.data?.sessions ?? [], refreshSessions, createNewSession, overview, refreshOverview: () => { void overviewQuery.refetch() }, openCommandPanel } satisfies ShellOutletContext} /></section>
      </main>

      <aside className={`drawer ${ui.drawerOpen ? 'show' : ''}`} inert={!ui.drawerOpen}>
        <div className="drawer-head"><div className="context-drawer-heading"><strong>上下文窗口</strong><span>{sessionId ? sessionLabel(sessionId) : '新会话 · 系统提示词已就绪'}</span></div><button className="icon-btn" onClick={() => ui.setDrawerOpen(false)} aria-label="关闭"><X size={17} /></button></div>
        <div className="drawer-body context-drawer-body">
          <section className="context-drawer-card context-token-card">
            <div className="context-card-head"><span><CircleGauge size={17} /><strong>Token 占用概览</strong></span><small>{!contextAvailable ? '暂不可用' : contextTokens?.measurement === 'estimated' ? '运行时估算' : contextTokens?.measurement === 'provider_reference' ? 'Provider 参考' : '当前输入窗口'}</small></div>
            <div className="context-metric-grid two-columns">
              <div className="context-metric"><span>系统提示词</span><strong>{contextAvailable ? formatTokens(contextTokens?.system_prompt_tokens ?? 0) : '—'}</strong><small>Token</small></div>
              <div className="context-metric"><span>工具定义</span><strong>{contextAvailable ? formatTokens(contextTokens?.tool_schema_tokens ?? 0) : '—'}</strong><small>Token</small></div>
              <div className="context-metric"><span>对话与摘要</span><strong>{contextAvailable ? formatTokens(Number(contextTokens?.conversation_tokens ?? contextWindow?.tokens.context_tokens ?? 0) + Number(contextTokens?.summary_tokens ?? 0)) : '—'}</strong><small>Token</small></div>
              <div className="context-metric emphasized"><span>当前总占用</span><strong>{contextAvailable ? formatTokens(contextTokens?.total_tokens ?? 0) : '—'}</strong><small>Token</small></div>
              <div className="context-metric"><span>容量上限</span><strong>{contextLimit ? formatTokens(contextTokens?.capacity_tokens ?? 0) : '—'}</strong><small>Token</small></div>
            </div>
            <div className="context-capacity"><div><span>上下文容量</span><strong>{contextAvailable ? `${Number(contextTokens?.percent ?? 0).toFixed(2)}%` : '—'}</strong></div><span className="context-capacity-track"><i style={{ width: `${contextAvailable ? contextTokens?.percent ?? 0 : 0}%` }} /></span></div>
          </section>

          <section className="context-drawer-card">
            <div className="context-card-head"><span><MessageSquarePlus size={17} /><strong>对话统计</strong></span><small>前台与归档</small></div>
            <div className="context-metric-grid three-columns">
              <div className="context-metric"><span>前台对话</span><strong>{contextWindow?.conversation.foreground_rounds ?? '—'}</strong><small>轮</small></div>
              <div className="context-metric"><span>后台归档</span><strong>{contextWindow?.conversation.archived_rounds ?? '—'}</strong><small>轮</small></div>
              <div className="context-metric"><span>当前会话总轮数</span><strong>{contextWindow?.conversation.session_total_rounds ?? '—'}</strong><small>轮</small></div>
              <div className="context-metric"><span>工具调用</span><strong>{contextWindow?.conversation.session_tool_calls ?? contextWindow?.conversation.total_tool_calls ?? '—'}</strong><small>次</small></div>
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

      <HistorySearchDrawer
        open={historyDrawerOpen}
        sessions={sessionsQuery.data?.sessions ?? []}
        activeSessionId={sessionId}
        loading={sessionsQuery.isLoading || sessionsQuery.isFetching}
        error={sessionsQuery.isError}
        chatRunning={chatRunning}
        switchingSessionId={historySwitchingSessionId}
        actionError={historySwitchError}
        onClose={() => setHistoryDrawerOpen(false)}
        onSelectSession={(targetSessionId) => { void selectHistorySession(targetSessionId) }}
        onDeleteSession={deleteHistorySession}
        onDeleteAllSessions={deleteAllHistorySessions}
        onRetrySummary={retryHistorySummary}
      />

      {commandOpen && <div className="command-layer show" onMouseDown={(event) => { if (event.target === event.currentTarget) setCommandOpen(false) }}>
        <div className="command-box" role="dialog" aria-label="全局搜索与命令" aria-modal="true">
          <div className="command-search-row">
            {commandMode === 'slash'
              ? <button type="button" className="command-back" onClick={() => { setCommandMode('main'); setCommandQuery('') }} aria-label="返回快捷指令"><ArrowLeft size={18} /></button>
              : <Search size={19} />}
            <input
              ref={commandInputRef}
              className="command-input"
              value={commandQuery}
              onChange={(event) => setCommandQuery(event.target.value)}
              placeholder={commandMode === 'slash' ? '搜索斜杠指令…' : '搜索对话操作、指令或页面…'}
              aria-label={commandMode === 'slash' ? '搜索斜杠指令' : '搜索快捷指令'}
            />
          </div>
          {commandMode === 'slash' ? <div className="command-list command-slash-list">
            <div className="command-reference-head"><span className="command-reference-icon"><Slash size={20} /></span><span><strong>斜杠指令参考</strong><small>CLI 与外部消息链路使用的“/”指令</small></span></div>
            <div className="command-card-grid command-reference-grid">
              {filteredSlashCommands.map((item) => <article className="command-slash-card" key={item.command}><code>{item.command}</code><span>{item.description}</span></article>)}
            </div>
            {!filteredSlashCommands.length && <div className="command-empty">没有匹配的斜杠指令</div>}
          </div> : <div className="command-list">
            {filteredConversationCommands.length > 0 && <section className="command-section" aria-labelledby="conversation-command-title">
              <h3 id="conversation-command-title">对话操作</h3>
              <div className="command-card-grid">{filteredConversationCommands.map((command) => {
                const Icon = command.icon
                return <button className={`command-item ${command.tone || ''}`} type="button" key={command.id} disabled={chatRunning || location.pathname !== '/chat'} onClick={() => runCommand(command.action)}><span className="command-item-icon"><Icon size={18} /></span><span><strong>{command.label}</strong><small>{location.pathname !== '/chat' ? '请先进入对话页面' : command.detail}</small></span></button>
              })}</div>
            </section>}
            {showSlashCommand && <section className="command-section" aria-labelledby="slash-command-title">
              <h3 id="slash-command-title">查看指令</h3>
              <div className="command-card-grid"><button className="command-item command-reference-entry" type="button" onClick={() => { setCommandMode('slash'); setCommandQuery(''); requestAnimationFrame(() => commandInputRef.current?.focus()) }}><span className="command-item-icon"><Slash size={18} /></span><span><strong>查看斜杠指令</strong><small>浏览 CLI 与外部消息链路使用的“/”指令</small></span></button></div>
            </section>}
            {filteredNavigationCommands.length > 0 && <section className="command-section" aria-labelledby="navigation-command-title">
              <h3 id="navigation-command-title">侧边栏功能</h3>
              <div className="command-card-grid">{filteredNavigationCommands.map((command) => {
                const Icon = command.icon
                return <button className="command-item" type="button" key={command.id} onClick={() => runCommand(command.action)}><span className="command-item-icon"><Icon size={18} /></span><span><strong>{command.label}</strong><small>{command.detail}</small></span></button>
              })}</div>
            </section>}
            {!filteredConversationCommands.length && !showSlashCommand && !filteredNavigationCommands.length && <div className="command-empty">没有匹配的快捷指令</div>}
          </div>}
        </div>
      </div>}
    </div>
  )
}
