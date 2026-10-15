import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useIsMutating, useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
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
  getHealth,
  getKemoModelCapabilities,
  getOverview,
  getPreferences,
  getSessions,
  getUserAvatarUrl,
  getUsers,
  getVersion,
  logoutAuth,
  patchPreferences,
  patchUserConfig,
  releaseSessionLease,
  retrySessionSummary,
  touchSessionLease,
  reopenSession,
  AVATAR_UPDATED_EVENT,
} from '../api/client'
import { ReferenceIcon } from '../reference/ReferenceIcon'
import { HistorySearchDrawer } from './HistorySearchDrawer'
import { ConversationFilesPopover } from './ConversationFilesPopover'
import { ApiError } from '../api/transport'
import { ReasoningEffortSelect } from './ReasoningEffortSelect'
import { UserProfileCard } from './UserProfileCard'
import { KemoLogo } from './KemoLogo'
import lifecycleStyles from './SessionLifecycle.module.css'
import type { AuthStatusResponse, ChatItem, OverviewResponse, SessionsResponse } from '../types/api'
import { useUiStore } from '../store/ui'
import { chatDraftKey, useChatDraftStore, type PendingUploadedFile } from '../store/chatDrafts'
import { createSessionChannel, getPageClientId } from '../sessionClient'
import { forgetSession, rememberSession, restoreWebSession } from '../sessionNavigation'
import { cacheCreatedSession, hasCreatedSessionLease } from '../sessionCreation'
import {
  normalizeKemoReasoningEffort,
  normalizeReasoningEffort,
  reasoningEffortLabel,
  reasoningEffortOptions,
  reasoningEffortOptionsFor,
  selectReasoningEffort,
  type ReasoningEffort,
} from '../reasoningEffort'

import type { ShellOutletContext } from './appShellTypes'
import { useChatRunRegistry } from './useChatRunRegistry'
import {
  CONVERSATION_COMMAND_EVENT,
  chatRunKey,
  fontSizeLabels,
  formatTokens,
  injectionPolicyPresentation,
  navGroups,
  pageTitles,
  persistLastActiveUser,
  readLastActiveUser,
  resolveCurrentUser,
  sessionLabel,
  slashCommands,
  type ConversationCommandAction,
} from './appShellConfig'
export * from './appShellConfig'
export type * from './appShellTypes'

export function AppShell() {
  const location = useLocation()
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const ui = useUiStore()
  const [mobileSidebarOpen, setMobileSidebarOpen] = useState(false)
  const [narrowScreen, setNarrowScreen] = useState(() => window.innerWidth <= 900)
  useEffect(() => {
    const update = () => setNarrowScreen(window.innerWidth <= 900)
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [])
  useEffect(() => { setMobileSidebarOpen(false) }, [location.pathname, location.search])
  useEffect(() => {
    if (!mobileSidebarOpen) return
    const close = (event: KeyboardEvent) => { if (event.key === 'Escape') setMobileSidebarOpen(false) }
    window.addEventListener('keydown', close)
    return () => window.removeEventListener('keydown', close)
  }, [mobileSidebarOpen])
  const queryClient = useQueryClient()
  const clientId = useMemo(() => getPageClientId(), [])
  const [sessionTransitioning, setSessionTransitioning] = useState(false)
  const creatingSessionRef = useRef<{ user: string; session: string; promise: Promise<string | undefined> } | null>(null)
  const createdLeaseRef = useRef<{ user: string; session: string; until: number } | null>(null)
  const authStatus = queryClient.getQueryData<AuthStatusResponse>(['auth-status'])
  const usersQuery = useQuery({ queryKey: ['users'], queryFn: getUsers })
  const healthQuery = useQuery({ queryKey: ['health'], queryFn: getHealth, refetchInterval: 30_000 })
  const versionQuery = useQuery({ queryKey: ['version'], queryFn: getVersion, staleTime: 60_000 })
  const [rememberedUser, setRememberedUser] = useState(readLastActiveUser)
  const user = resolveCurrentUser(params.get('user') || '', rememberedUser, usersQuery.data?.users)
  const sessionId = params.get('session') || ''
  const newUserTab = params.get('new_session') === '1' && !sessionId
  const [sessionLifecycleNotice, setSessionLifecycleNotice] = useState('')
  const [leasedConversation, setLeasedConversation] = useState('')
  useEffect(() => {
    setSessionLifecycleNotice('')
    setSessionTransitioning(false)
  }, [user])
  const sessionsQuery = useQuery({
    queryKey: ['sessions', user],
    queryFn: () => getSessions(user),
    enabled: Boolean(user),
  })
  const webSessions = (sessionsQuery.data?.sessions ?? []).filter(
    (session) => (session.source || 'web') === 'web',
  )
  const activeSessionQuery = useQuery({
    queryKey: ['active-session', user, clientId, newUserTab ? 'new-tab' : 'restore'],
    queryFn: () => newUserTab ? createSession(user, clientId) : restoreWebSession(user, clientId),
    enabled: Boolean(user) && !sessionId && !sessionTransitioning,
    staleTime: newUserTab ? Infinity : 0,
    retry: newUserTab ? false : undefined,
    refetchOnWindowFocus: !newUserTab,
    refetchOnReconnect: !newUserTab,
  })
  const overviewQuery = useQuery({
    queryKey: ['overview', user, sessionId],
    queryFn: () => getOverview(user, sessionId),
    enabled: Boolean(user && sessionId),
    refetchInterval: 30_000,
  })
  const overviewProvider = overviewQuery.data?.provider
  const providerCapabilitiesQuery = useQuery({
    queryKey: ['provider-model-capabilities', user, overviewProvider?.model || ''],
    queryFn: () => getKemoModelCapabilities(user, overviewProvider?.model || ''),
    enabled: Boolean(
      user
        && overviewProvider?.type === 'kemo'
        && overviewProvider.configured
        && overviewProvider.model,
    ),
    staleTime: 300_000,
    retry: false,
  })
  const preferencesQuery = useQuery({
    queryKey: ['preferences', user],
    queryFn: () => getPreferences(user),
    enabled: Boolean(user),
    staleTime: 60_000,
  })
  const reasoningEffortMutation = useMutation({
    mutationKey: ['provider-config', user],
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
  const providerConfigSaving = useIsMutating({ mutationKey: ['provider-config', user] }) > 0

  const [fontSizeMenuOpen, setFontSizeMenuOpen] = useState(false)
  const [modelMenuOpen, setModelMenuOpen] = useState(false)
  const [commandOpen, setCommandOpen] = useState(false)
  const [commandQuery, setCommandQuery] = useState('')
  const [commandMode, setCommandMode] = useState<'main' | 'slash'>('main')
  const [historyDrawerOpen, setHistoryDrawerOpen] = useState(false)
  const [historyArchiveDate, setHistoryArchiveDate] = useState('')
  const [historySwitchingSessionId, setHistorySwitchingSessionId] = useState('')
  const [historySwitchError, setHistorySwitchError] = useState('')
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false)
  const [logoutPending, setLogoutPending] = useState(false)
  const [avatarRevision, setAvatarRevision] = useState(0)
  const locationRef = useRef(location)
  const sessionChannelRef = useRef<ReturnType<typeof createSessionChannel> | null>(null)
  const fontSizeRef = useRef<HTMLDivElement>(null)
  const modelMenuRef = useRef<HTMLDivElement>(null)
  const commandInputRef = useRef<HTMLInputElement>(null)
  const historyDateSessionsQuery = useQuery({
    queryKey: ['history-sessions', user, historyArchiveDate],
    queryFn: () => getSessions(user, '', 50, '', 'all', historyArchiveDate),
    enabled: Boolean(user && historyDrawerOpen && historyArchiveDate),
  })
  const historySessionsData = historyArchiveDate
    ? historyDateSessionsQuery.data
    : sessionsQuery.data

  locationRef.current = location
  const {
    userRef, sessionIdRef, chatRuns, chatRunning, chatStopping, chatRunId, chatRunSessionId, chatRunToken,
    beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus,
    removeNextTurnMessage, finishChatRun, reorderNextTurnMessages, clearChatRun,
    setChatRunning, setChatRunId, setChatAbortController, setChatStopping, abortChatRun,
  } = useChatRunRegistry(user, sessionId)

  useEffect(() => {
    document.documentElement.dataset.theme = ui.theme
    document.documentElement.dataset.fontSize = ui.fontSize
    document.documentElement.dataset.fontsize = ui.fontSize === 'small' ? 'compact' : ui.fontSize === 'large' ? 'large' : 'default'
  }, [ui.theme, ui.fontSize])

  useEffect(() => {
    if (!user || !usersQuery.data?.users.some((item) => item.name === user)) return
    persistLastActiveUser(user)
    if (rememberedUser !== user) setRememberedUser(user)
  }, [rememberedUser, user, usersQuery.data?.users])

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
      // 思考选项 portal 到 body，仍属于当前模型卡；点击选项不能提前卸载它。
      const reasoningId = modelMenuRef.current?.querySelector('[role="combobox"]')?.getAttribute('aria-controls')
      const reasoningPopover = reasoningId ? document.getElementById(reasoningId) : null
      if (modelMenuRef.current && !modelMenuRef.current.contains(target)
        && !reasoningPopover?.contains(target)) setModelMenuOpen(false)
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
    if (!historyDrawerOpen && historyArchiveDate) setHistoryArchiveDate('')
  }, [historyArchiveDate, historyDrawerOpen])

  useEffect(() => {
    if (!historyDrawerOpen) return
    const sessions = historySessionsData?.sessions ?? []
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
    const timer = window.setTimeout(() => {
      if (historyArchiveDate) void historyDateSessionsQuery.refetch()
      else void sessionsQuery.refetch()
    }, delay)
    return () => window.clearTimeout(timer)
  }, [historyArchiveDate, historyDateSessionsQuery.refetch, historyDrawerOpen, historySessionsData?.sessions, sessionsQuery.refetch])

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
    persistLastActiveUser(nextUser)
    setRememberedUser(nextUser)
    const next = new URLSearchParams()
    next.set('user', nextUser)
    setParams(next)
  }

  const setSessionId = (nextSession: string) => {
    if (userRef.current !== user) return
    const current = locationRef.current
    const next = new URLSearchParams(current.search)
    if (user) next.set('user', user)
    if (nextSession && !next.get('session')) {
      useChatDraftStore.getState().moveDraft(chatDraftKey(user, ''), chatDraftKey(user, nextSession))
    }
    if (nextSession) next.delete('new_session')
    if (nextSession) next.set('session', nextSession)
    else next.delete('session')
    navigate({ pathname: current.pathname, search: `?${next}`, hash: current.hash }, { replace: true })
  }

  const detachSession = () => {
    setSessionTransitioning(true)
    queryClient.removeQueries({ queryKey: ['active-session', user, clientId] })
    setSessionId('')
  }

  useEffect(() => {
    // The router can commit after a local state update. Keep automatic restore
    // paused until the new session is actually present in the URL.
    if (sessionId) setSessionTransitioning(false)
  }, [sessionId])

  const notifySessionDeleted = (deletedSessionId: string) => {
    if (!user || !deletedSessionId) return
    forgetSession(user, deletedSessionId)
    sessionChannelRef.current?.post({
      type: 'session-deleted',
      user,
      sessionId: deletedSessionId,
      clientId,
    })
  }

  useEffect(() => {
    const active = activeSessionQuery.data?.session?.session_id
    if (!sessionId && !sessionTransitioning && !chatRunning && active
      && activeSessionQuery.isSuccess && !activeSessionQuery.isFetching) {
      if (newUserTab) {
        const result = activeSessionQuery.data!
        cacheCreatedSession(queryClient, result)
        if (hasCreatedSessionLease(result, user, clientId)) {
          createdLeaseRef.current = { user, session: active, until: Date.now() + 15_000 }
          setLeasedConversation(chatRunKey(user, active))
        }
        const next = new URLSearchParams(locationRef.current.search)
        next.delete('new_session')
        next.set('user', user)
        next.set('session', active)
        setParams(next, { replace: true })
        void queryClient.invalidateQueries({ queryKey: ['sessions', user] })
      } else {
        setSessionId(active)
        void queryClient.invalidateQueries({ queryKey: ['sessions', user] })
      }
    }
  }, [activeSessionQuery.data?.session?.session_id, activeSessionQuery.isSuccess, activeSessionQuery.isFetching,
    chatRunning, sessionId, sessionTransitioning, user, newUserTab])

  useEffect(() => {
    if (!user || !sessionId) return
    let disposed = false
    const touch = (updateLatest = false) => {
      if (!disposed) void touchSessionLease(user, sessionId, clientId).then(() => {
        if (!disposed) {
          rememberSession(user, sessionId, updateLatest && document.visibilityState !== 'hidden')
          setLeasedConversation(chatRunKey(user, sessionId))
        }
      }).catch((error: unknown) => {
        if (disposed || !(error instanceof ApiError) || error.status !== 404) return
        forgetSession(user, sessionId)
        setLeasedConversation('')
        setSessionLifecycleNotice('此对话已被清理或删除，已返回开始页。历史归档列表将自动刷新；如归档也已清理，则无法恢复。')
        queryClient.removeQueries({ queryKey: ['active-session', user, clientId] })
        queryClient.removeQueries({ queryKey: ['history', user, sessionId] })
        queryClient.removeQueries({ queryKey: ['overview', user, sessionId] })
        void queryClient.invalidateQueries({ queryKey: ['sessions', user] })
        void queryClient.invalidateQueries({ queryKey: ['history-sessions', user] })
        setSessionTransitioning(true)
        setSessionId('')
      })
    }
    const release = () => { void releaseSessionLease(user, sessionId, clientId, true).catch(() => undefined) }
    const resume = () => touch(true)
    const proof = createdLeaseRef.current
    if (proof?.user === user && proof.session === sessionId && proof.until > Date.now()) {
      createdLeaseRef.current = null
      rememberSession(user, sessionId, document.visibilityState !== 'hidden')
      setLeasedConversation(chatRunKey(user, sessionId))
    } else resume()
    const heartbeat = window.setInterval(touch, 15_000)
    window.addEventListener('pagehide', release)
    window.addEventListener('pageshow', resume)
    window.addEventListener('online', resume)
    document.addEventListener('visibilitychange', resume)
    return () => {
      disposed = true
      window.clearInterval(heartbeat)
      window.removeEventListener('pagehide', release)
      window.removeEventListener('pageshow', resume)
      window.removeEventListener('online', resume)
      document.removeEventListener('visibilitychange', resume)
      release()
    }
  }, [clientId, sessionId, user])

  useEffect(() => {
    const channel = createSessionChannel((event) => {
      if (event.clientId === clientId || event.user !== userRef.current) return
      forgetSession(event.user, event.sessionId)
      queryClient.removeQueries({ queryKey: ['history', event.user, event.sessionId] })
      void queryClient.invalidateQueries({ queryKey: ['sessions', event.user] })
      void queryClient.invalidateQueries({ queryKey: ['history-sessions', event.user] })
      void queryClient.invalidateQueries({ queryKey: ['overview', event.user] })
      if (sessionIdRef.current === event.sessionId) {
        queryClient.removeQueries({ queryKey: ['active-session', event.user, clientId] })
        setSessionTransitioning(true)
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

  const loadMoreHistorySessions = async () => {
    const current = historySessionsData
    if (!user || !current?.has_more || !current.next_cursor || historyLoadingMore) return
    setHistoryLoadingMore(true)
    try {
      const nextPage = await getSessions(user, '', 50, current.next_cursor, 'all', historyArchiveDate)
      const queryKey = historyArchiveDate
        ? ['history-sessions', user, historyArchiveDate]
        : ['sessions', user]
      queryClient.setQueryData<SessionsResponse>(queryKey, {
        ...nextPage,
        sessions: [
          ...current.sessions,
          ...nextPage.sessions.filter(
            (candidate) => !current.sessions.some(
              (existing) => (existing.source || 'web') === (candidate.source || 'web')
                && existing.session_id === candidate.session_id,
            ),
          ),
        ],
      })
    } finally {
      setHistoryLoadingMore(false)
    }
  }

  const createNewSession = (): Promise<string | undefined> => {
    if (!user || chatRunning) return Promise.resolve(undefined)
    const requestedUser = user
    const requestedSession = sessionIdRef.current
    const pending = creatingSessionRef.current
    if (pending?.user === requestedUser && pending.session === requestedSession) return pending.promise
    setSessionTransitioning(true)
    const create = async () => {
      try {
        const result = await createSession(requestedUser, clientId)
        const current = locationRef.current
        const next = new URLSearchParams(current.search)
        const currentUser = next.get('user') || userRef.current
        if (currentUser === requestedUser && sessionIdRef.current === requestedSession) {
          cacheCreatedSession(queryClient, result)
          if (hasCreatedSessionLease(result, requestedUser, clientId)) {
            createdLeaseRef.current = { user: requestedUser, session: result.session.session_id, until: Date.now() + 15_000 }
            setLeasedConversation(chatRunKey(requestedUser, result.session.session_id))
          }
          next.set('user', requestedUser)
          next.delete('new_session')
          next.set('session', result.session.session_id)
          navigate({ pathname: current.pathname, search: `?${next.toString()}`, hash: current.hash }, { replace: true })
        }
        // The new URL starts its own overview query; never queue an old/unbound
        // refresh in front of the first history/lease requests.
        void queryClient.invalidateQueries({ queryKey: ['sessions', requestedUser] })
        return result.session.session_id
      } catch (error) {
        if (userRef.current === requestedUser && sessionIdRef.current === requestedSession && requestedSession) setSessionTransitioning(false)
        throw error
      }
    }
    const promise = create()
    const operation = { user: requestedUser, session: requestedSession, promise }
    creatingSessionRef.current = operation
    const clear = () => { if (creatingSessionRef.current === operation) creatingSessionRef.current = null }
    void promise.then(clear, clear)
    return promise
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
      const currentSession = sessionsQuery.data?.sessions.find(
        (session) => (session.source || 'web') === 'web' && session.session_id === sessionId,
      )
      if (sessionId && currentSession?.state !== 'closed') {
        await closeSession(user, sessionId, clientId)
      }
      const targetSession = historySessionsData?.sessions.find(
        (session) => (session.source || 'web') === 'web' && session.session_id === targetSessionId,
      )
      if (targetSession?.state === 'closed') {
        await reopenSession(user, targetSessionId, clientId)
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
      queryClient.invalidateQueries({ queryKey: ['history-sessions', user] }),
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
    await Promise.all([
      sessionsQuery.refetch(),
      queryClient.invalidateQueries({ queryKey: ['history-sessions', user] }),
    ])
  }

  const deleteAllHistorySessions = async () => {
    if (!user) throw new Error('当前没有可用用户')
    if (chatRunning) throw new Error('当前对话正在运行，暂时不能删除全部历史对话')
    await deleteAllSessions(user)
    forgetSession(user)
    queryClient.removeQueries({ queryKey: ['history', user] })
    queryClient.removeQueries({ queryKey: ['active-session', user, clientId] })
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessions', user] }),
      queryClient.invalidateQueries({ queryKey: ['history-sessions', user] }),
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
    { id: 'compress', label: '手动进行一次上下文压缩', detail: '优先按轮数压缩，不足时按 Token 压缩；记忆转入后台', keywords: 'compress context token 压缩 上下文', icon: Zap, action: () => dispatchConversationCommand('compress') },
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
  const perceptionInjection = injectionPolicyPresentation(
    contextWindow?.injection_policy?.perception,
  )
  const expandInjection = injectionPolicyPresentation(
    contextWindow?.injection_policy?.expand,
  )
  const provider = overview?.provider
  const kemoReasoning = providerCapabilitiesQuery.data?.capabilities.reasoning
  const kemoReasoningOptions = kemoReasoning?.supported
    ? reasoningEffortOptionsFor(kemoReasoning.efforts)
    : []
  const kemoReasoningAvailable = kemoReasoningOptions.length > 0
  const configuredReasoningEffort = reasoningEffortMutation.isPending && reasoningEffortMutation.variables?.targetUser === user
    ? reasoningEffortMutation.variables?.reasoningEffort
    : provider?.reasoning_effort
  const displayedReasoningEffort = provider?.type === 'kemo' && kemoReasoningAvailable
    ? selectReasoningEffort(configuredReasoningEffort, kemoReasoning!.efforts)!
    : provider?.type === 'kemo'
      ? normalizeKemoReasoningEffort(configuredReasoningEffort)
      : normalizeReasoningEffort(configuredReasoningEffort)
  const displayedReasoningLabel = provider?.type !== 'kemo'
    ? reasoningEffortLabel(displayedReasoningEffort)
    : kemoReasoningAvailable
      ? reasoningEffortLabel(displayedReasoningEffort)
      : providerCapabilitiesQuery.isPending
        ? '读取能力中'
        : '推理未启用'
  const reasoningPolicy = providerCapabilitiesQuery.data?.capabilities.extensions.reasoning_policy
  const reasoningCapabilityNotice = providerCapabilitiesQuery.data?.warning
    || (reasoningPolicy?.collapsed ? '此模型的部分思考档位会映射到相同的上游强度。' : '')
  // 顶栏与输入坞共用能力缓存和保存合同，不创建第二套模型配置状态。
  const reasoningControl = {
    value: displayedReasoningEffort,
    options: provider?.type === 'kemo' ? kemoReasoningOptions : provider?.type === 'chat' ? reasoningEffortOptions : [],
    disabled: providerConfigSaving,
    emptyLabel: provider?.type === 'kemo' && providerCapabilitiesQuery.isFetching ? '读取能力中…' : '模型未声明推理档位',
    notice: reasoningCapabilityNotice,
    error: reasoningEffortMutation.isError && reasoningEffortMutation.variables?.targetUser === user
      ? '思考强度保存失败，请重试。'
      : provider?.type === 'kemo' && providerCapabilitiesQuery.isError ? '模型能力读取失败，请在 Provider 配置中重试。' : undefined,
    onChange: (reasoningEffort: ReasoningEffort) => reasoningEffortMutation.mutate({ targetUser: user, reasoningEffort }),
  }

  const settingsPath = (tab: 'users' | 'provider') => {
    const path = withContext('/settings')
    return `${path}${path.includes('?') ? '&' : '?'}tab=${tab}`
  }

  return (
    <div className={`app-shell ${ui.sidebarCollapsed ? 'sidebar-collapsed' : ''} ${mobileSidebarOpen ? 'mobile-sidebar-open' : ''}`}>
      {mobileSidebarOpen && <button className="mobile-nav-backdrop mobile-only" aria-label="关闭导航遮罩" onClick={() => setMobileSidebarOpen(false)} />}
      <aside className="sidebar" aria-label="主导航" inert={(narrowScreen || ui.sidebarCollapsed) && !mobileSidebarOpen} aria-hidden={(narrowScreen || ui.sidebarCollapsed) && !mobileSidebarOpen}>
        <div className="sidebar-brand">
          <div className="brand-mark"><KemoLogo /></div>
          <div className="brand-name">kemo-agent</div>
          {versionQuery.data?.version && <span className="badge badge-outline mono">v{versionQuery.data.version}</span>}
          <button className="icon-btn desktop-only" onClick={ui.toggleSidebar}
            aria-label={ui.sidebarCollapsed ? '展开侧边栏' : '收缩侧边栏'} title="折叠侧栏"><ReferenceIcon name="panelLeft" /></button>
          <button className="icon-btn mobile-only" onClick={() => setMobileSidebarOpen(false)} aria-label="关闭侧栏"><X size={18} /></button>
        </div>
        <div className="sidebar-scroll">
          {navGroups.map(group => <div className="sidebar-group" key={group.label}>
            <div className="section-label nav-section-divider" role="separator" aria-label={group.label} />
            {group.items.map(({ path, label }) => <NavLink key={path} to={withContext(path)} aria-label={label}
              title={ui.sidebarCollapsed ? label : undefined} className={({ isActive }) => `nav-item ${isActive ? 'is-active' : ''}`}>
              <ReferenceIcon name={path.slice(1)} /><span className="nav-label">{label}</span>
              {path === '/tasks' && overview?.counts.active_tasks ? <span className="nav-count">{overview.counts.active_tasks}</span> : null}
            </NavLink>)}
          </div>)}
          {location.pathname === '/chat' && <div className="sidebar-group sidebar-recent-group">
            <div className="section-label">最近对话
              <button className="icon-btn" style={{ width: 20, height: 20, marginLeft: 'auto' }} onClick={openHistoryDrawer}
                aria-label="浏览全部历史"><ReferenceIcon name="history" small /></button>
            </div>
            <div className="sidebar-session">
              <div className="sidebar-session-list">
                {webSessions.map(session => <button key={session.session_id}
                  className={`session-item ${session.session_id === sessionId ? 'is-active' : ''}`}
                  onClick={() => void selectHistorySession(session.session_id)} disabled={Boolean(historySwitchingSessionId)}
                  title={session.title || session.session_id}>
                  <ReferenceIcon name="chat" small /><span className="si-title">{session.title || '未命名对话'}</span>
                  {session.summary_status === 'failed' ? <span className="dot dot-warning" /> : null}
                  <span className="si-time">{session.rounds} 轮</span>
                </button>)}
              </div>
              <button className="btn btn-ghost btn-sm" onClick={openHistoryDrawer}><ReferenceIcon name="history" small />浏览全部历史</button>
            </div>
          </div>}
        </div>
        <div className="sidebar-foot">
          <UserProfileCard username={user || '未选择用户'} userPath={user ? `users/${user}` : 'users/—'}
            avatarUrl={user ? getUserAvatarUrl(user, avatarRevision) : undefined}
            users={usersQuery.data?.users.map(item => ({ username: item.name, userPath: `users/${item.name}`, avatarUrl: getUserAvatarUrl(item.name, avatarRevision) }))}
            compact={ui.sidebarCollapsed} logoutPending={logoutPending} switchingDisabled={chatRunning}
            switchingDisabledReason="当前对话正在运行，结束或停止后才能切换用户" onSelectUser={setUser}
            newUserTabHref={user ? `/chat?${new URLSearchParams({ user, new_session: '1' })}` : undefined}
            onOpenProfile={() => navigate(withContext('/profile'))} onOpenUserSwitch={() => navigate(settingsPath('users'))}
            onOpenSettings={() => navigate(settingsPath('provider'))} onLogout={authStatus?.enabled ? () => { void logout() } : undefined} />
        </div>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <button className="icon-btn mobile-only" onClick={() => setMobileSidebarOpen(value => !value)} aria-label="切换导航" aria-expanded={mobileSidebarOpen}><Menu size={18} /></button>
          {ui.sidebarCollapsed && <button className="icon-btn desktop-only" onClick={ui.toggleSidebar} aria-label="展开侧边栏"><ReferenceIcon name="panelLeft" /></button>}
          <div className="topbar-title">{pageTitles[location.pathname] || '对话'}
            {location.pathname === '/chat' && <span className="topbar-sub">{webSessions.find(item => item.session_id === sessionId)?.title || '新对话'}</span>}
          </div>
          <div className="topbar-actions">
            <button className="status-pill" title="查看上下文与运行状态" onClick={openContextDrawer}>
              <ReferenceIcon name="gauge" small /><span className="sp-val">{contextAvailable && contextLimit ? `${contextPercent.toFixed(1)}%` : '—'}</span>
              <span className="faint">{contextAvailable && contextLimit ? `${formatTokens(contextTotal)} / ${formatTokens(contextLimit)}` : '暂不可用'}</span>
            </button>
            <div className="reference-menu-anchor" ref={modelMenuRef}>
              <button className="status-pill" onClick={() => setModelMenuOpen(value => !value)} aria-expanded={modelMenuOpen} title="模型与思考档位">
                <span className={`dot ${provider?.configured ? 'dot-success' : 'dot-warning'}`} /><span className="mono">{provider?.model || '未配置模型'}</span>
              </button>
              {modelMenuOpen && <div className="menu reference-top-menu reference-model-menu" role="menu">
                <div className="menu-label">当前模型路由</div><div className="reference-menu-copy reference-model-route"><strong>{provider?.model || '未读取模型'}</strong><small>{provider?.base_url || '未配置兼容端点'}</small></div>
                <div className="reference-menu-copy"><span>思考强度 · {displayedReasoningLabel}</span>
                  {provider?.type === 'chat' || (provider?.type === 'kemo' && kemoReasoningAvailable)
                    ? <ReasoningEffortSelect ariaLabel="顶部模型思考强度" variant="compact" value={displayedReasoningEffort}
                        options={provider?.type === 'kemo' ? kemoReasoningOptions : undefined} disabled={providerConfigSaving}
                        onChange={reasoningEffort => reasoningEffortMutation.mutate({ targetUser: user, reasoningEffort })} />
                    : <small>{providerCapabilitiesQuery.isPending ? '读取能力中…' : '模型未声明推理档位'}</small>}
                </div>
                {reasoningCapabilityNotice && <div className="reference-menu-copy">{reasoningCapabilityNotice}</div>}
                {providerCapabilitiesQuery.isError && <div className="banner banner-warning">模型能力读取失败</div>}
                {reasoningEffortMutation.isError && <div className="banner banner-danger" role="alert">思考强度保存失败，请重试。</div>}
                <button className="menu-item" onClick={() => { setModelMenuOpen(false); navigate(settingsPath('provider')) }}>编辑 Provider 配置</button>
              </div>}
            </div>
            <div className="reference-top-divider" />
            {location.pathname === '/chat' && <ConversationFilesPopover user={user} sessionId={sessionId} liveRun={chatRuns[chatRunKey(user, sessionId)]} />}
            <button className="icon-btn" onClick={openHistoryDrawer} aria-label="搜索历史对话" title="搜索历史对话"><ReferenceIcon name="search" /></button>
            <div className="reference-menu-anchor" ref={fontSizeRef}>
              <button className="icon-btn" onClick={() => setFontSizeMenuOpen(value => !value)} aria-label="调整界面字号" title="界面比例" aria-expanded={fontSizeMenuOpen}><ReferenceIcon name="type" /></button>
              {fontSizeMenuOpen && <div className="menu reference-top-menu" role="menu"><div className="menu-label">界面字号</div>
                {(['small', 'medium', 'large'] as const).map(size => <button className={`menu-item ${ui.fontSize === size ? 'is-active' : ''}`}
                  role="menuitem" key={size} onClick={() => { ui.setFontSize(size); if (user) void patchPreferences(user, { font_size: size }); setFontSizeMenuOpen(false) }}>{fontSizeLabels[size]}{ui.fontSize === size ? ' ✓' : ''}</button>)}
              </div>}
            </div>
            <button className="icon-btn" onClick={() => { const theme = ui.theme === 'dark' ? 'light' : 'dark'; ui.setTheme(theme); if(user) void patchPreferences(user, { theme }) }}
              aria-label={ui.theme === 'dark' ? '切换为高级白主题' : '切换为高级黑主题'} title="切换主题"><ReferenceIcon name={ui.theme === 'dark' ? 'sun' : 'moon'} /></button>
            <button className="icon-btn" onClick={() => navigate(withContext('/status'))} aria-label="运行状态" title="运行诊断"><ReferenceIcon name="activity" /></button>
            <button className="icon-btn" onClick={openCommandPanel} aria-label="命令面板" title="命令面板（Ctrl+K）"><ReferenceIcon name="command" /></button>
          </div>
        </header>
        <main className="reference-outlet">
          {healthQuery.isError && <div className="banner banner-warning" role="status">Web 后端连接异常，请检查服务。</div>}
          {sessionLifecycleNotice && <div className={lifecycleStyles.notice} role="status">{sessionLifecycleNotice}<button onClick={() => setSessionLifecycleNotice('')}>知道了</button></div>}
          {historySwitchError && <div className="banner banner-danger" role="alert">{historySwitchError}</div>}
          {newUserTab || (location.pathname === '/chat' && !sessionId && !sessionTransitioning
            && (activeSessionQuery.isPending || activeSessionQuery.isFetching || activeSessionQuery.isError
              || Boolean(activeSessionQuery.data?.session?.session_id))) ? <div className={lifecycleStyles.pending} role={activeSessionQuery.isError ? 'alert' : 'status'}>
            {activeSessionQuery.isError ? <>{newUserTab ? '创建独立对话失败，请重试。' : '恢复对话失败，请重试。'}<button onClick={() => { void activeSessionQuery.refetch() }}>{newUserTab ? '重试创建对话' : '重试恢复对话'}</button></> : newUserTab ? '正在为此用户创建独立对话…' : '正在恢复最近的对话…'}
          </div> : <Outlet context={{ user, userAvatarUrl: user ? getUserAvatarUrl(user, avatarRevision) : undefined, sessionId, sessionReady: Boolean(sessionId && leasedConversation === chatRunKey(user, sessionId)), clientId, chatRunning, chatStopping, setChatRunning, setChatStopping, chatRunId, chatRunSessionId, chatRunToken, setChatRunId, setChatAbortController, abortChatRun, chatRuns, beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus, removeNextTurnMessage, reorderNextTurnMessages, finishChatRun, clearChatRun, setSessionId, detachSession, notifySessionDeleted, sessions: webSessions, refreshSessions, createNewSession, overview, reasoningControl, refreshOverview: () => { void overviewQuery.refetch() }, openCommandPanel } satisfies ShellOutletContext} />}
        </main>
      </div>

      <aside className={`drawer ${ui.drawerOpen ? 'show' : ''}`} inert={!ui.drawerOpen} role="dialog" aria-label="上下文窗口" aria-modal="true" aria-hidden={!ui.drawerOpen}>
        <div className="drawer-head"><div className="context-drawer-heading"><strong>上下文窗口</strong><span>{sessionId ? sessionLabel(sessionId) : '新会话 · 系统提示词已就绪'}</span></div><button className="icon-btn" onClick={() => ui.setDrawerOpen(false)} aria-label="关闭"><X size={17} /></button></div>
        <div className="drawer-body context-drawer-body">
          <section className="context-injection-panel" aria-label="系统提示词数据注入策略">
            <div className="context-injection-panel-head"><strong>数据注入策略</strong><small>当前用户配置 · 只读</small></div>
            <div className="context-injection-grid">
              <article className={`context-injection-bubble ${perceptionInjection.mode}`} aria-label={`感知数据注入情况：${perceptionInjection.label}`}>
                <span className="context-injection-icon"><BrainCircuit size={17} /></span>
                <span><small>感知数据注入情况</small><strong>{perceptionInjection.label}</strong><em>{perceptionInjection.detail}</em></span>
              </article>
              <article className={`context-injection-bubble ${expandInjection.mode}`} aria-label={`拓展数据注入情况：${expandInjection.label}`}>
                <span className="context-injection-icon"><Shapes size={17} /></span>
                <span><small>拓展数据注入情况</small><strong>{expandInjection.label}</strong><em>{expandInjection.detail}</em></span>
              </article>
            </div>
          </section>
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
            <div className="context-metric-grid two-columns">
              <div className="context-metric"><span>启用知识</span><strong>{contextWindow?.knowledge.enabled ?? '—'}</strong><small>项</small></div>
              <div className="context-metric"><span>禁用知识</span><strong>{contextWindow?.knowledge.disabled ?? '—'}</strong><small>项</small></div>
            </div>
          </section>

          <section className="context-drawer-card compact-card">
            <div className="context-card-head"><span><RadioTower size={17} /><strong>外部消息</strong></span><small>连接状态</small></div>
            <div className="context-single-stat"><span>已连接的外部消息模块</span><strong>{contextWindow?.messages.connected ?? '—'}<small> 个</small></strong></div>
          </section>

          <section className="context-drawer-card compact-card">
            <div className="context-card-head"><span><Shapes size={17} /><strong>拓展与感知</strong></span><small>模块接入数量</small></div>
            <div className="context-metric-grid two-columns">
              <div className="context-metric"><span>已接入拓展</span><strong>{contextWindow?.integrations.expands ?? '—'}</strong><small>个</small></div>
              <div className="context-metric"><span>已接入感知</span><strong>{contextWindow?.integrations.senses ?? '—'}</strong><small>个</small></div>
            </div>
          </section>
        </div>
      </aside>
      {ui.drawerOpen && <button className="drawer-backdrop" aria-label="关闭运行状态" onClick={() => ui.setDrawerOpen(false)} />}

      <HistorySearchDrawer
        user={user}
        open={historyDrawerOpen}
        sessions={historySessionsData?.sessions ?? []}
        activeSessionId={sessionId}
        loading={historyArchiveDate
          ? historyDateSessionsQuery.isLoading || historyDateSessionsQuery.isFetching
          : sessionsQuery.isLoading || sessionsQuery.isFetching}
        loadingMore={historyLoadingMore}
        hasMore={Boolean(historySessionsData?.has_more)}
        error={historyArchiveDate ? historyDateSessionsQuery.isError : sessionsQuery.isError}
        chatRunning={chatRunning}
        switchingSessionId={historySwitchingSessionId}
        actionError={historySwitchError}
        selectedDate={historyArchiveDate}
        onClose={() => setHistoryDrawerOpen(false)}
        onDateChange={setHistoryArchiveDate}
        onSelectSession={(targetSessionId) => { void selectHistorySession(targetSessionId) }}
        onDeleteSession={deleteHistorySession}
        onDeleteAllSessions={deleteAllHistorySessions}
        onRetrySummary={retryHistorySummary}
        onLoadMore={() => { void loadMoreHistorySessions() }}
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
