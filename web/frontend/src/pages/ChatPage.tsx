import { Fragment, Suspense, lazy, type MouseEvent, useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  ChevronDown,
  ListChecks,
  Copy,
  Download,
  File as FileIcon,
  FileX2,
  Image as ImageIcon,
  Music2,
  Pencil,
  RotateCcw,
  Trash2,
  UserRound,
  Video,
  Workflow,
  X,
} from 'lucide-react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { ApiError, cancelRun, closeSession, commandPlan, compressSession, deleteSession, getExpands, getHistory, getKnowledge, getRuntimeStatus, getSkills, getTasks, getUserArtifactUrl, getUserAttachmentThumbnailUrl, getUserFileDownloadUrl, getUserFilePreviewUrl, retryPlanStep, streamChat, submitGuidance, withdrawIntegration, uploadUserFile } from '../api/client'
import { AgentComposer } from '../components/AgentComposer'
import { GlobalConfirmDialog } from '../components/GlobalConfirmDialog'
import { buildCapabilityReferenceItems, capabilityReferenceLine, capabilityReferenceMarker } from '../components/capabilityReferences'
import { CONVERSATION_COMMAND_EVENT, chatRunKey, type ChatItemsUpdater, type ConversationCommandAction, type PendingNextTurnMessage, type ShellOutletContext } from '../components/AppShell'
import { PlainTextMessage } from '../components/Chat/PlainTextMessage'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from '../components/CapabilityReferenceDrawer'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { formatBytes, formatDateTime, statusLabel } from '../components/ModuleUi'
import type { ScheduledTaskItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import { UserMessageNavigator, type UserMessageMarker } from '../components/UserMessageNavigator'
import type { ChatItem, CronTaskSummary, HistoryResponse, InputAttachment, KnowledgeDocumentSummary, MediaArtifact, PlanSummary, RunEvent } from '../types/api'
import { copyText } from '../utils/clipboard'
import { playUserCompletionSound, playUserFailureSound } from '../utils/completionSound'
import { randomUUID } from '../randomId'
import { t , useLanguage } from '../i18n'
import { chatDraftKey, EMPTY_CHAT_DRAFT, useChatDraftStore, type PendingUploadedFile } from '../store/chatDrafts'
import { useCompressionStatus } from '../store/compressionStatus'
import { useScratchSender } from '../store/scratchNotes'
import { shouldPrefetchEarlierHistory } from './chatHistory'
import {
  archiveTerminalPlansInConversation,
  buildHistoryItems,
  buildUserMessageMarkers,
  compactPlanAssistantText,
  createDeltaEventBatcher,
  dropLastLiveRound,
  executeStopRequest,
  extractPlanSummary,
  finalizeCurrentRoundItems,
  groupConversationItems,
  isNearScrollBottom,
  isProvisionalRunError,
  isRetryAttemptProgress,
  mergeHistoryPages,
  partitionAssistantTurnItems,
  prepareRunUserMessage,
  reduceRunEvent,
  resetCurrentRoundItemsForRetry,
  resolveHistoryUserMessages,
  selectDockedPlan,
} from './chatState'
export {
  archiveTerminalPlansInConversation,
  buildHistoryItems,
  buildUserMessageMarkers,
  compactPlanAssistantText,
  createDeltaEventBatcher,
  dropLastLiveRound,
  executeStopRequest,
  groupConversationItems,
  isNearScrollBottom,
  mergeHistoryPages,
  partitionAssistantTurnItems,
  prepareRunUserMessage,
  reduceRunEvent,
  resetCurrentRoundItemsForRetry,
  resolveHistoryUserMessages,
  selectDockedPlan,
  extractPlanSummary,
  finalizeCurrentRoundItems,
  isRetryAttemptProgress,
} from './chatState'

export type { PendingUploadedFile } from '../store/chatDrafts'
import {
  createSessionId,
  EMPTY_CHAT_ITEMS,
  eventId,
  HISTORY_PAGE_SIZE,
  isFailedRunCompletion,
  isSuccessfulRunCompletion,
  pendingInputAttachment,
  removeSubmittedUploads,
  type RunErrorNotice,
  type RunRetryNotice,
} from './chatRunSupport'
export { isFailedRunCompletion, isSuccessfulRunCompletion, removeSubmittedUploads } from './chatRunSupport'
import {
  buildScheduledTaskItems,
  buildSenseDataItems,
  greetingLabel,
  GuidanceMessage,
  MediaArtifactCard,
  PendingAttachmentTray,
  TaskPlanRecord,
  UserAttachmentCard,
  UserMessageAvatar,
  type GuidanceDisplayItem,
  type GuidanceItem,
} from './chatPresentation'
export { buildScheduledTaskItems, buildSenseDataItems, formatSenseUpdateInterval, mediaArtifactUrl } from './chatPresentation'
import { createPlanWorkflow, createSendHandler } from './chatWorkflows'
import { ChatPageView } from './ChatPageView'

const MarkdownMessage = lazy(async () => ({
  default: (await import('../components/Chat/MarkdownMessage')).MarkdownMessage,
}))

export function ChatPage() {
  useLanguage()
  const { reasoningControl, sessionReady } = useOutletContext<ShellOutletContext>()
  const { user, userAvatarUrl, sessionId, clientId, chatRunning: chatRunningForSession, chatStopping: stopping, setChatRunning: setRunning, setChatStopping: setStopping, chatRunId: activeRunId, chatRunSessionId, chatRunToken, setChatRunId: setActiveRunId, setChatAbortController, abortChatRun, chatRuns, beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus, removeNextTurnMessage, finishChatRun, clearChatRun, setSessionId, detachSession, notifySessionDeleted, sessions, refreshSessions, createNewSession, overview, refreshOverview } = useOutletContext<ShellOutletContext>()
  // AppShell resolves controls for the displayed conversation.  Keep the
  // session check as a defensive guard for an in-flight route transition.
  const running = Boolean(
    chatRunningForSession
    && chatRunSessionId
    && (!sessionId || chatRunSessionId === sessionId),
  )
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const draftKey = chatDraftKey(user, sessionId)
  const draftSnapshot = useChatDraftStore((state) => state.drafts[draftKey] ?? EMPTY_CHAT_DRAFT)
  const setDraftText = useChatDraftStore((state) => state.setText)
  const setDraftUploads = useChatDraftStore((state) => state.setPendingUploads)
  const setDraftUploadFeedback = useChatDraftStore((state) => state.setUploadFeedback)
  const setDraftUploading = useChatDraftStore((state) => state.setUploading)
  const moveDraft = useChatDraftStore((state) => state.moveDraft)
  const clearDraft = useChatDraftStore((state) => state.clearDraft)
  const { text: draft, uploadFeedback, pendingUploads, uploading } = draftSnapshot
  const setDraft = (value: string | ((current: string) => string)) => setDraftText(draftKey, value)
  const setUploadFeedback = (value: typeof uploadFeedback) => setDraftUploadFeedback(draftKey, value)
  const setPendingUploads = (value: PendingUploadedFile[] | ((current: PendingUploadedFile[]) => PendingUploadedFile[])) => setDraftUploads(draftKey, value)
  const [editingSource, setEditingSource] = useState<{
    id: string
    content: string
    sessionId: string
    remainingRounds: number
    previousDraft: string
  } | null>(null)
  const [editedSources, setEditedSources] = useState<Set<string>>(() => new Set())
  const [copiedItem, setCopiedItem] = useState('')
  const [knowledgeDrawerOpen, setKnowledgeDrawerOpen] = useState(false)
  const [capabilityDrawerOpen, setCapabilityDrawerOpen] = useState(false)
  const [conversationBusy, setConversationBusy] = useState<ConversationCommandAction | 'edit' | ''>('')
  const [conversationFeedback, setConversationFeedback] = useState<{
    tone: 'success' | 'error'; text: string; retryScope?: string
  } | null>(null)
  const [clearTarget, setClearTarget] = useState<{ user: string; sessionId: string; committed: boolean } | null>(null)
  const [clearPending, setClearPending] = useState(false)
  const [clearError, setClearError] = useState('')
  const clearExecutingRef = useRef(false)
  const saveExecutingRef = useRef(false)
  const newExecutingRef = useRef(false)
  const tasksHistoryScopeRef = useRef('')
  const clearRemovedRef = useRef(false)
  const [activeTaskOpen, setActiveTaskOpen] = useState(false)
  const [collapsedPlans, setCollapsedPlans] = useState<Set<string>>(() => new Set())
  const [planOverrides, setPlanOverrides] = useState<Record<string, PlanSummary>>({})
  const [planMutationNotices, setPlanMutationNotices] = useState<Record<string, string>>({})
  const [showFollowOutput, setShowFollowOutput] = useState(false)
  const [runRetryNotice, setRunRetryNotice] = useState<RunRetryNotice | null>(null)
  const [runErrorNotice, setRunErrorNotice] = useState<RunErrorNotice | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const taskPlanDockRef = useRef<HTMLDivElement | null>(null)
  const followOutputRef = useRef(true)
  const lastScrollTopRef = useRef(0)
  const loadingEarlierRef = useRef(false)
  const [navigationTarget, setNavigationTarget] = useState<{ id: string; scope: string } | null>(null)
  const navigationTargetRef = useRef(navigationTarget)
  navigationTargetRef.current = navigationTarget
  const prependSnapshotRef = useRef<{
    scrollHeight: number; scrollTop: number; anchor: HTMLElement | null; anchorTop: number
  } | null>(null)
  const submittedUploadsRef = useRef(new Map<string, PendingUploadedFile[]>())
  const completionSoundRunIdsRef = useRef(new Set<string>())
  const failureSoundRunIdsRef = useRef(new Set<string>())
  const consumingNextTurnRef = useRef(false)
  const creatingSendSessionRef = useRef(false)
  const guidingFollowUpsRef = useRef(new Set<string>())
  const { reorderNextTurnMessages } = useOutletContext<ShellOutletContext>()
  const locallyCommittedSessionRef = useRef('')
  const undoneRoundBaselineRef = useRef<{
    sessionId: string
    remainingRounds: number
  } | null>(null)
  const lastAttemptSessionRef = useRef(sessionId)
  const conversationKeyRef = useRef(`${user}\u0000${sessionId}`)
  const liveSessionId = running && chatRunSessionId
    ? chatRunSessionId
    : sessionId || lastAttemptSessionRef.current || chatRunSessionId
  const liveRun = liveSessionId ? chatRuns[chatRunKey(user, liveSessionId)] : undefined
  const effectiveRunId = activeRunId || liveRun?.runId || ''
  const liveItems = liveRun?.items ?? EMPTY_CHAT_ITEMS
  const currentUserRef = useRef(user)
  const currentLiveSessionRef = useRef(liveSessionId)
  currentUserRef.current = user
  currentLiveSessionRef.current = liveSessionId
  const isCurrentConversation = (targetUser: string, targetSession: string) => (
    currentUserRef.current === targetUser
    && currentLiveSessionRef.current === targetSession
  )
  const setRunRetryNoticeFor = (targetUser: string, targetSession: string, value: RunRetryNotice | null) => {
    if (isCurrentConversation(targetUser, targetSession)) setRunRetryNotice(value)
  }
  const setRunErrorNoticeFor = (targetUser: string, targetSession: string, value: RunErrorNotice | null) => {
    if (isCurrentConversation(targetUser, targetSession)) setRunErrorNotice(value)
  }

  const playCompletionSoundOnce = (completedRunId: string, event: RunEvent) => {
    if (!isSuccessfulRunCompletion(event)) return
    const played = completionSoundRunIdsRef.current
    if (played.has(completedRunId)) return
    if (played.size >= 100) played.clear()
    played.add(completedRunId)
    void playUserCompletionSound(user)
  }
  const playFailureSoundOnce = (failedRunId: string, event: RunEvent) => {
    if (!isFailedRunCompletion(event)) return
    const played = failureSoundRunIdsRef.current
    if (played.has(failedRunId)) return
    if (played.size >= 100) played.clear()
    played.add(failedRunId)
    void playUserFailureSound(user)
  }
  const setLiveItems = (updater: ChatItemsUpdater) => {
    if (liveSessionId) updateChatRunItems(user, liveSessionId, updater)
  }
  const hasCommitted = useMemo(() => {
    if (!sessionId) return false
    return sessionReady || sessionId === locallyCommittedSessionRef.current
      || sessions.some((session) => session.session_id === sessionId)
  }, [sessionId, sessionReady, sessions])
  const historyQuery = useInfiniteQuery({
    queryKey: ['history', user, sessionId],
    queryFn: ({ pageParam, signal }) => getHistory(user, sessionId, {
      limit: pageParam !== undefined && navigationTargetRef.current?.scope === `${user}\u0000${sessionId}`
        ? 100 : HISTORY_PAGE_SIZE,
      before: pageParam,
    }, signal),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.pagination?.has_more_before
      ? lastPage.pagination.next_before ?? undefined
      : undefined,
    enabled: Boolean(user && sessionId && (hasCommitted || sessionReady)),
    retry: false,
  })
  const tasksQuery = useQuery({
    queryKey: ['tasks', user, sessionId],
    queryFn: ({ signal }) => getTasks(user, sessionId, signal),
    enabled: Boolean(user && sessionId),
    refetchInterval: (query) => query.state.data?.plans.some((plan) => ['approved', 'running'].includes(plan.status)) ? 1200 : false,
  })
  const runtimeTokensQuery = useQuery({
    queryKey: ['runtime-welcome-tokens', user, sessionId],
    queryFn: ({ signal }) => getRuntimeStatus(user, sessionId, ['tokens'], signal),
    enabled: Boolean(user && sessionId),
    staleTime: 30_000,
  })
  useEffect(() => {
    if (!sessionId || !tasksQuery.dataUpdatedAt) return
    const scope = chatDraftKey(user, sessionId)
    if (tasksHistoryScopeRef.current !== scope) {
      tasksHistoryScopeRef.current = scope
      // A truly new/empty plan list cannot have committed a background step.
      // Existing plans still need reconciliation if one completed during load.
      if (!tasksQuery.data?.plans.length) return
    }
    void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
  }, [queryClient, sessionId, tasksQuery.dataUpdatedAt, user])
  const knowledgeQuery = useQuery({
    queryKey: ['knowledge', user],
    queryFn: ({ signal }) => getKnowledge(user, signal),
    enabled: Boolean(user && knowledgeDrawerOpen),
  })
  const expandsQuery = useQuery({
    queryKey: ['expands', user],
    queryFn: ({ signal }) => getExpands(user, signal, false),
    enabled: Boolean(user && capabilityDrawerOpen),
    staleTime: 30_000,
  })
  const skillsQuery = useQuery({
    queryKey: ['skills', user],
    queryFn: ({ signal }) => getSkills(user, signal),
    enabled: Boolean(user && capabilityDrawerOpen),
    staleTime: 30_000,
  })
  const capabilityItems = useMemo(
    () => buildCapabilityReferenceItems(expandsQuery.data, skillsQuery.data),
    [expandsQuery.data, skillsQuery.data],
  )

  const historyData = useMemo(
    () => mergeHistoryPages(historyQuery.data?.pages),
    [historyQuery.data?.pages],
  )
  const persistedUserMessages = historyData?.pagination?.integration_count
    ?? historyData?.messages.filter((message) => message.role === 'user').length
    ?? 0
  const handoffReady = liveRun?.phase === 'awaiting_history' && persistedUserMessages > liveRun.historyUserMessages
  const visibleLiveItems = handoffReady ? EMPTY_CHAT_ITEMS : liveItems
  const liveIntegrationNumbers = useMemo(() => new Set(visibleLiveItems.flatMap((item) => (
    (item.kind === 'execution_marker' || item.kind === 'message' && item.role === 'user')
      && item.integrationNumber !== undefined
      ? [item.integrationNumber] : []
  ))), [visibleLiveItems])
  const historyItems = useMemo<ChatItem[]>(
    () => buildHistoryItems(historyData, liveIntegrationNumbers),
    [historyData, liveIntegrationNumbers],
  )
  const conversationIntegrationCount = Math.max(persistedUserMessages, ...liveIntegrationNumbers)
  const items = useMemo(() => [...historyItems, ...visibleLiveItems], [historyItems, visibleLiveItems])
  useEffect(() => {
    const conversationKey = `${user}\u0000${sessionId}`
    if (conversationKeyRef.current === conversationKey) return
    conversationKeyRef.current = conversationKey
    if (!running) lastAttemptSessionRef.current = sessionId
    followOutputRef.current = true
    lastScrollTopRef.current = 0
    loadingEarlierRef.current = false
    prependSnapshotRef.current = null
    navigationTargetRef.current = null
    setNavigationTarget(null)
    setShowFollowOutput(false)
    setEditingSource(null)
    undoneRoundBaselineRef.current = null
    setEditedSources(new Set())
    setCopiedItem('')
    if (!running) setActiveRunId('', user, sessionId)
    setKnowledgeDrawerOpen(false)
    setCapabilityDrawerOpen(false)
    setConversationBusy('')
    // Save temporarily detaches the URL. A rejected close restores the same
    // space: retain its retry feedback, but never carry it to another space/user.
    setConversationFeedback(current => current?.retryScope && (
      current.retryScope === conversationKey
      || (!sessionId && current.retryScope.startsWith(`${user}\u0000`))
    ) ? current : null)
    setPlanOverrides({})
    setPlanMutationNotices({})
    setRunRetryNotice(null)
    setRunErrorNotice(null)
  }, [abortChatRun, user, sessionId])

  useEffect(() => {
    if (!runErrorNotice) return
    const timer = window.setTimeout(() => setRunErrorNotice(null), 10_000)
    return () => window.clearTimeout(timer)
  }, [runErrorNotice])

  useEffect(() => {
    if (!handoffReady || !liveSessionId) return
    clearChatRun(user, liveSessionId)
  }, [clearChatRun, handoffReady, liveSessionId, user])

  useEffect(() => {
    const prompt = searchParams.get('prompt')
    if (!prompt) return
    setDraft(prompt)
    const next = new URLSearchParams(searchParams)
    next.delete('prompt')
    setSearchParams(next, { replace: true })
  }, [searchParams, setSearchParams])

  useEffect(() => {
    const element = scrollRef.current
    if (!element || !followOutputRef.current || prependSnapshotRef.current) return
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: 'instant' })
    else element.scrollTop = element.scrollHeight
  }, [items.length, liveItems, running])

  useLayoutEffect(() => {
    const snapshot = prependSnapshotRef.current
    const element = scrollRef.current
    if (!snapshot || !element) return
    // Keep the item being read in place, including when earlier RAW cards are
    // inserted inside an already expanded process rather than above the turn.
    const delta = snapshot.anchor?.isConnected
      ? snapshot.anchor.getBoundingClientRect().top - snapshot.anchorTop
      : element.scrollHeight - snapshot.scrollHeight
    element.scrollTop = snapshot.scrollTop + delta
    lastScrollTopRef.current = element.scrollTop
    prependSnapshotRef.current = null
    loadingEarlierRef.current = false
  }, [historyQuery.data?.pages.length])

  const send = createSendHandler({
    draft, user, running, stopping, uploading, sessionId, draftKey, clientId,
    persistedUserMessages, editingSource, lastAttemptSessionRef, currentUserRef,
    currentLiveSessionRef, submittedUploadsRef, undoneRoundBaselineRef,
    followOutputRef, locallyCommittedSessionRef, queryClient, beginChatRun,
    setRunRetryNoticeFor, setRunErrorNoticeFor, setConversationBusy, setDraft, setPendingUploads,
    setRunning, setActiveRunId, setShowFollowOutput,
    setEditedSources, updateChatRunItems, setEditingSource, setChatAbortController,
    playCompletionSoundOnce, playFailureSoundOnce, setDraftUploads,
    refreshSessions, isCurrentConversation, moveDraft, setSessionId,
    refreshOverview, setDraftText, finishChatRun, setStopping, createNewSession, creatingSendSessionRef,
  })

  const scratchSendRef = useRef<(text: string) => Promise<boolean>>(async () => false)
  const scratchScopeRef = useRef(draftKey)
  scratchScopeRef.current = draftKey
  scratchSendRef.current = async text => {
    if (!user || uploading || editingSource || creatingSendSessionRef.current) return false
    if (running || stopping) {
      if (!liveSessionId) return false
      queueNextTurnMessage(user, liveSessionId, {
        id: eventId('follow_up'), content: text, uploadedFiles: [],
        historyUserMessages: Math.max(persistedUserMessages, (liveRun?.historyUserMessages ?? persistedUserMessages) + 1),
        status: 'queued',
      })
      return true
    }
    return send(text, { preserveDraft: true })
  }
  useEffect(() => useScratchSender.getState().bind(draftKey, text =>
    scratchScopeRef.current === draftKey ? scratchSendRef.current(text) : Promise.resolve(false)), [draftKey])

  useEffect(() => {
    const pending = liveRun?.nextTurnQueue[0]
    if (!pending || pending.status !== 'queued' || running || uploading || stopping || editingSource || consumingNextTurnRef.current || !user || !liveSessionId) return
    consumingNextTurnRef.current = true
    setNextTurnMessageStatus(user, liveSessionId, pending.id, 'sending')
    void send(pending.content, {
      sessionId: liveSessionId,
      historyUserMessages: Math.max(pending.historyUserMessages, persistedUserMessages, liveRun?.historyUserMessages ?? 0),
      uploadedFiles: pending.uploadedFiles,
      internalNextTurn: true,
      preserveDraft: true,
      userMessageId: `next_turn_${pending.id}`,
    }).then((committed) => {
      if (committed) removeNextTurnMessage(user, liveSessionId, pending.id)
      else setNextTurnMessageStatus(user, liveSessionId, pending.id, 'error', '下一轮未成功提交')
    }).catch((error) => {
      setNextTurnMessageStatus(
        user,
        liveSessionId,
        pending.id,
        'error',
        error instanceof Error ? error.message : '下一轮自动发送失败',
      )
    }).finally(() => {
      consumingNextTurnRef.current = false
    })
  }, [liveRun?.nextTurnQueue, liveSessionId, removeNextTurnMessage, running, setNextTurnMessageStatus, stopping, uploading, user, editingSource])

  const uploadFiles = async (files: File[]) => {
    if (!user || uploading || !files.length) return
    const remainingSlots = Math.max(0, 20 - pendingUploads.length)
    if (!remainingSlots) {
      setUploadFeedback({ tone: 'error', text: '每轮最多附加 20 个文件，请先移除部分附件' })
      return
    }
    const accepted = files.slice(0, remainingSlots)
    const skipped = files.length - accepted.length
    const batchDraftKey = draftKey
    const uploadUser = user
    const results: Array<PendingUploadedFile | Error | undefined> = new Array(accepted.length)
    let cursor = 0
    setDraftUploading(batchDraftKey, true)
    setDraftUploadFeedback(batchDraftKey, { tone: 'pending', text: `正在上传 ${accepted.length} 个文件…` })
    try {
      const worker = async () => {
        while (cursor < accepted.length) {
          const index = cursor
          cursor += 1
          const file = accepted[index]
          try {
            const result = await uploadUserFile(uploadUser, 'file_upload', file.name, file)
            const path = result.path || file.name
            results[index] = {
              path,
              name: path.split('/').at(-1) || file.name,
              size: result.size ?? file.size,
              mimeType: result.mime_type || file.type || 'application/octet-stream',
              mediaKind: result.media_kind || (file.type.startsWith('image/')
                ? 'image'
                : file.type.startsWith('audio/')
                  ? 'audio'
                  : file.type.startsWith('video/')
                    ? 'video'
                    : 'file'),
              checksumSha256: result.checksum_sha256,
            }
          } catch (error) {
            results[index] = error instanceof Error ? error : new Error('上传失败')
          }
        }
      }
      await Promise.all(Array.from({ length: Math.min(3, accepted.length) }, () => worker()))
      const uploaded = results.filter((item): item is PendingUploadedFile => Boolean(item) && !(item instanceof Error))
      const failures = results.filter((item): item is Error => item instanceof Error)
      if (uploaded.length) setDraftUploads(batchDraftKey, (current) => [...current, ...uploaded].slice(0, 20))
      const messages: string[] = []
      if (failures.length) messages.push(`${failures.length} 个文件上传失败：${failures[0].message}`)
      if (skipped) messages.push(`${skipped} 个文件因每轮 20 项限制未上传`)
      setDraftUploadFeedback(batchDraftKey, messages.length ? { tone: 'error', text: messages.join('；') } : null)
      if (uploaded.length) await queryClient.invalidateQueries({ queryKey: ['user-files', uploadUser, 'file_upload'] })
    } finally {
      setDraftUploading(batchDraftKey, false)
    }
  }
  const newConversation = async () => {
    const previousDraftKey = draftKey
    abortChatRun(user, liveSessionId, effectiveRunId, chatRunToken)
    const created = await createNewSession()
    if (!created) throw new Error('新对话未创建，请重试。')
    clearDraft(previousDraftKey)
    if (liveSessionId) clearChatRun(user, liveSessionId)
  }

  const directlyNewConversation = async () => {
    if (!user || running || stopping || conversationBusy || newExecutingRef.current) return
    newExecutingRef.current = true
    setConversationBusy('new')
    setConversationFeedback(null)
    const targetUser = user
    const targetSession = liveSessionId
    try {
      // Create first; switching the URL releases only this page's old lease.
      // Do not close, delete or cancel the old session used by other windows.
      const created = await createNewSession()
      if (!created) throw new Error('新对话未创建，请重试。')
    } catch (error) {
      if (isCurrentConversation(targetUser, targetSession)) {
        setConversationFeedback({
          tone: 'error', text: error instanceof Error ? error.message : t('直接新建对话失败，请重试。'),
        })
      }
    } finally {
      newExecutingRef.current = false
      if (isCurrentConversation(targetUser, targetSession)) setConversationBusy('')
    }
  }

  const saveAndNewConversation = async () => {
    if (running || conversationBusy || saveExecutingRef.current) return
    saveExecutingRef.current = true
    setConversationBusy('save')
    setConversationFeedback(null)
    const previousSessionId = sessionId
    let previousSessionClosed = false
    detachSession()
    try {
      if (previousSessionId) {
        const result = await closeSession(user, previousSessionId, clientId)
        if (result.closed !== true) {
          if (result.deferred && typeof result.retry_after_seconds === 'number'
            && result.retry_after_seconds > 0) {
            throw new Error(t('另有 {count} 个页面或客户端仍在使用此对话，尚未关闭，也未启动后台提取。请关闭其他页面后重试；旧页面停止心跳后，租约预计最多 {seconds} 秒过期。',
              { count: result.active_clients ?? 1, seconds: Math.ceil(result.retry_after_seconds) }))
          }
          throw new Error(result.deferred
            ? t('此对话仍被其他页面或客户端使用，尚未关闭，也未启动后台提取。请关闭其他页面后重试；异常断开的页面可能需要等待在线租约过期。')
            : t('当前对话尚未确认关闭，未创建新对话，请重试。'))
        }
        previousSessionClosed = true
      }
      await newConversation()
    } catch (error) {
      if (previousSessionId && !previousSessionClosed) setSessionId(previousSessionId)
      setConversationFeedback({
        tone: 'error', text: error instanceof Error ? error.message : '保存当前对话失败',
        retryScope: previousSessionId && !previousSessionClosed ? `${user}\u0000${previousSessionId}` : undefined,
      })
    } finally {
      saveExecutingRef.current = false
      setConversationBusy('')
    }
  }

  const clearConversation = async () => {
    if (running || conversationBusy || clearExecutingRef.current) return
    clearRemovedRef.current = false
    setClearError('')
    setClearTarget({ user, sessionId, committed: hasCommitted })
  }

  useEffect(() => {
    if (!clearTarget || clearExecutingRef.current) return
    if (clearTarget.user !== user || (sessionId !== clearTarget.sessionId && !clearRemovedRef.current)) {
      setClearTarget(null)
      setClearError('')
    }
  }, [user, sessionId, clearTarget])

  const confirmClearConversation = async () => {
    if (!clearTarget || running || conversationBusy || clearExecutingRef.current) return
    if (clearTarget.user !== user || (!clearRemovedRef.current && clearTarget.sessionId !== sessionId)) return
    // 同步锁防止连点；目标在打开确认时固定，重试新建失败时不能再次删除归档。
    clearExecutingRef.current = true
    setClearPending(true)
    setClearError('')
    setConversationBusy('clear')
    setConversationFeedback(null)
    const previousSessionId = clearTarget.sessionId
    detachSession()
    try {
      if (previousSessionId && clearTarget.committed && !clearRemovedRef.current) {
        await deleteSession(user, previousSessionId, clientId)
        clearRemovedRef.current = true
        notifySessionDeleted(previousSessionId)
        queryClient.removeQueries({ queryKey: ['history', user, previousSessionId] })
        if (locallyCommittedSessionRef.current === previousSessionId) locallyCommittedSessionRef.current = ''
        void refreshSessions()
        refreshOverview()
      } else if (previousSessionId && !clearRemovedRef.current) {
        await closeSession(user, previousSessionId, clientId)
        clearRemovedRef.current = true
      }
      const created = await createNewSession()
      if (!created) throw new Error('新对话未创建，请重试。')
      clearDraft(chatDraftKey(clearTarget.user, previousSessionId))
      if (previousSessionId) clearChatRun(clearTarget.user, previousSessionId)
      setClearTarget(null)
    } catch (error) {
      if (previousSessionId && !clearRemovedRef.current) setSessionId(previousSessionId)
      const message = error instanceof Error ? error.message : '清空当前对话失败'
      setClearError(clearRemovedRef.current
        ? `原对话已清理，但新对话创建失败：${message}。重试只创建新对话，不会重复删除。`
        : message)
    } finally {
      clearExecutingRef.current = false
      setClearPending(false)
      setConversationBusy('')
    }
  }

  const compressCurrentConversation = async () => {
    if (running || conversationBusy) return
    if (!sessionId || !hasCommitted) {
      setConversationFeedback({ tone: 'error', text: '当前对话尚未归档，暂时无法压缩。' })
      return
    }
    setConversationBusy('compress')
    setConversationFeedback(null)
    useCompressionStatus.getState().setStatus(user, sessionId, {
      status: 'started', trigger: 'manual', historyTokensBefore: 0,
      historyTokensAfter: 0, absorbedCards: 0, content: '',
    })
    try {
      const result = await compressSession(user, sessionId)
      const compressionText = result.compressed
        ? `上下文压缩完成，已整理 ${result.absorbed_cards} 张历史卡片。`
        : '当前上下文暂时无需压缩。'
      const memory = result.memory
      const memoryText = memory.status === 'queued'
        ? (Number(memory.pending_cards || 0) > 0
            ? `记忆提取已转入后台，共有 ${Number(memory.pending_cards)} 张卡片待处理。`
            : '记忆提取已转入后台。')
        : memory.status === 'completed'
          ? (memory.candidates > 0
            ? `已同步提取 ${memory.candidates} 条记忆候选。`
            : '记忆提取已完成，本次没有需要保存的新记忆。')
        : memory.status === 'skipped'
          ? (memory.reason === 'already_processed'
              ? '记忆已是最新状态。'
              : memory.reason === 'memory_extraction_disabled'
                ? '记忆提取已按配置关闭。'
                : '当前没有可提取的历史卡片。')
          : memory.error?.message
            ? `记忆提取任务登记失败：${memory.error.message}`
            : '记忆提取未完成，已保留待后台重试。'
      setConversationFeedback({
        tone: memory.status === 'failed' ? 'error' : 'success',
        text: `${compressionText}${memoryText}`,
      })
      useCompressionStatus.getState().setStatus(user, sessionId, {
        status: 'ready', trigger: 'manual',
        historyTokensBefore: Number(result.context?.history_tokens_before || 0),
        historyTokensAfter: Number(result.context?.history_tokens_after || 0),
        absorbedCards: Number(result.absorbed_cards || 0), content: compressionText,
      })
      refreshOverview()
    } catch (error) {
      const message = error instanceof Error ? error.message : '手动上下文压缩失败'
      useCompressionStatus.getState().setStatus(user, sessionId, {
        status: 'failed', trigger: 'manual',
        historyTokensBefore: 0, historyTokensAfter: 0, absorbedCards: 0, content: message,
      })
      setConversationFeedback({ tone: 'error', text: message })
    } finally {
      setConversationBusy('')
    }
  }

  const editAndResend = (id: string, content: string) => {
    if (running || conversationBusy || !lastUserMessage || lastUserMessage.id !== id) return
    const targetSession = sessionId || lastAttemptSessionRef.current
    const expectedIntegrationCount = conversationIntegrationCount
    if (!targetSession || expectedIntegrationCount < 1) return
    setConversationFeedback(null)
    setEditingSource({
      id,
      content,
      sessionId: targetSession,
      remainingRounds: expectedIntegrationCount,
      previousDraft: editingSource?.previousDraft ?? draft,
    })
    setDraft(content)
    window.requestAnimationFrame(() => {
      const input = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')
      if (!input) return
      input.focus()
      input.setSelectionRange(input.value.length, input.value.length)
    })
  }

  const cancelEditAndResend = () => {
    setDraft(editingSource?.previousDraft ?? '')
    setEditingSource(null)
    setConversationFeedback(null)
  }

  const copyMessage = async (id: string, content: string) => {
    await copyText(content)
    setCopiedItem(id)
    window.setTimeout(() => setCopiedItem((current) => current === id ? '' : current), 1200)
  }

  const queueFollowUp = () => {
    const content = draft.trim()
    const uploadedFiles = pendingUploads.map((file) => ({ ...file }))
    if ((!content && !uploadedFiles.length) || !user || uploading) return
    if (!running && !stopping) {
      void send(content, { uploadedFiles })
      return
    }
    if (!liveSessionId) return
    queueNextTurnMessage(user, liveSessionId, {
      id: eventId('follow_up'), content, uploadedFiles,
      historyUserMessages: Math.max(persistedUserMessages, (liveRun?.historyUserMessages ?? persistedUserMessages) + 1),
      status: 'queued',
    })
    setDraft('')
    if (uploadedFiles.length) setDraftUploads(draftKey, (current) => removeSubmittedUploads(current, uploadedFiles))
  }

  const sendGuidance = async (message: PendingNextTurnMessage) => {
    if (!user || !running || stopping || !effectiveRunId || !liveSessionId || !['queued', 'error'].includes(message.status)) return
    const targetUser = user
    const targetSession = liveSessionId
    const targetRunId = effectiveRunId
    const id = message.id
    const claimKey = `${targetUser}\u0000${targetSession}\u0000${id}`
    if (guidingFollowUpsRef.current.has(claimKey)) return
    guidingFollowUpsRef.current.add(claimKey)
    const guidance = message.content
    const uploadedFiles = message.uploadedFiles ?? []
    const attachments = uploadedFiles.map(pendingInputAttachment)
    const displayText = guidance || `附件引导：${uploadedFiles.map((file) => file.name).join('、')}`
    const removePendingGuidance = () => updateChatRunItems(targetUser, targetSession, (current) => current.filter((item) => item.id !== id))
    setNextTurnMessageStatus(targetUser, targetSession, id, 'guiding')
    updateChatRunItems(targetUser, targetSession, (current) => [...current, {
      id,
      kind: 'guidance',
      guidanceId: id,
      content: displayText,
      attachments,
      status: 'queued',
    }])
    try {
      const result = await submitGuidance(targetUser, targetRunId, guidance, {
        sessionId: targetSession,
        guidanceId: id,
        uploadedFiles: uploadedFiles.map((file) => file.path),
      })
      if (result.status === 'queued_next_turn') {
        removePendingGuidance()
        setNextTurnMessageStatus(targetUser, targetSession, id, 'queued')
      } else {
        removeNextTurnMessage(targetUser, targetSession, id)
      }
    } catch (error) {
      removePendingGuidance()
      if (error instanceof ApiError && error.status === 404) {
        setNextTurnMessageStatus(targetUser, targetSession, id, 'queued')
      } else {
        setNextTurnMessageStatus(targetUser, targetSession, id, 'error', error instanceof Error ? error.message : '本轮引导提交失败')
      }
    } finally {
      guidingFollowUpsRef.current.delete(claimKey)
    }
  }

  const guideDraft = () => {
    const content = draft.trim()
    const uploadedFiles = pendingUploads.map((file) => ({ ...file }))
    if (
      (!content && !uploadedFiles.length)
      || !user
      || uploading
      || !running
      || stopping
      || !effectiveRunId
      || !liveSessionId
    ) return
    const message: PendingNextTurnMessage = {
      id: eventId('guidance'),
      content,
      uploadedFiles,
      historyUserMessages: Math.max(
        persistedUserMessages,
        (liveRun?.historyUserMessages ?? persistedUserMessages) + 1,
      ),
      status: 'queued',
    }
    queueNextTurnMessage(user, liveSessionId, message)
    setDraft('')
    if (uploadedFiles.length) {
      setDraftUploads(draftKey, (current) => removeSubmittedUploads(current, uploadedFiles))
    }
    void sendGuidance(message)
  }

  const activePlan = sessionId
    && hasCommitted
    && overview?.session_id === sessionId
    ? overview.active_plan
    : null
  const lastUserMessage = [...items].reverse().find((item) => item.kind === 'message' && item.role === 'user')
  const latestRunningGuidance = running
    ? [...visibleLiveItems].reverse().find((item): item is GuidanceItem => item.kind === 'guidance' && !item.finalized)
    : undefined
  const followUpQueue = liveRun?.nextTurnQueue ?? []
  const guidancePreviewItem: GuidanceDisplayItem | undefined = latestRunningGuidance
    && !followUpQueue.some((message) => message.id === latestRunningGuidance.id && message.status !== 'guiding')
    ? latestRunningGuidance : undefined
  const regenerateLastResponse = async () => {
    if (running || conversationBusy || editingSource || !lastUserMessage || lastUserMessage.kind !== 'message') return
    const prompt = lastUserMessage.content
    const targetSession = sessionId || lastAttemptSessionRef.current
    const expectedIntegrationCount = conversationIntegrationCount
    if (expectedIntegrationCount < 1) return
    setConversationBusy('retry')
    setConversationFeedback(null)
    try {
      const undo = targetSession
        ? await withdrawIntegration(user, targetSession, expectedIntegrationCount, prompt)
        : null
      setLiveItems((current) => dropLastLiveRound(current))
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
      }
      await send(prompt, {
        sessionId: targetSession || undefined,
        content: undo?.content?.length ? undo.content : undefined,
        historyUserMessages: undo?.integration_count ?? expectedIntegrationCount,
      })
    } catch (error) {
      setConversationFeedback({
        tone: 'error',
        text: error instanceof Error ? error.message : '撤销上一轮并重新发送失败',
      })
    } finally {
      setConversationBusy('')
    }
  }
  useEffect(() => {
    const handleConversationCommand = (event: Event) => {
      const action = (event as CustomEvent<ConversationCommandAction>).detail
      if (action === 'new') void directlyNewConversation()
      else if (action === 'save') void saveAndNewConversation()
      else if (action === 'clear') void clearConversation()
      else if (action === 'compress') void compressCurrentConversation()
      else if (action === 'retry') void regenerateLastResponse()
    }
    window.addEventListener(CONVERSATION_COMMAND_EVENT, handleConversationCommand)
    return () => window.removeEventListener(CONVERSATION_COMMAND_EVENT, handleConversationCommand)
  }, [clearConversation, compressCurrentConversation, directlyNewConversation, regenerateLastResponse, saveAndNewConversation])
  const recentTasks = useMemo(() => buildScheduledTaskItems(tasksQuery.data?.cron_tasks || []), [tasksQuery.data])
  const { commandPlanStatus, planActions } = createPlanWorkflow({
    user, running, sessionId, clientId, persistedUserMessages, planMutationNotices,
    lastAttemptSessionRef, currentUserRef, currentLiveSessionRef, followOutputRef,
    locallyCommittedSessionRef, queryClient, setLiveItems, setPlanOverrides,
    setPlanMutationNotices, beginChatRun, setRunRetryNoticeFor, setRunErrorNoticeFor,
    updateChatRunItems, setRunning, setActiveRunId, setShowFollowOutput,
    setChatAbortController, playCompletionSoundOnce, playFailureSoundOnce,
    refreshSessions, isCurrentConversation, setSessionId, refreshOverview,
    finishChatRun, setStopping, setCollapsedPlans, navigate,
  })
  const persistedPlans = useMemo(() => tasksQuery.data?.plans ?? [], [tasksQuery.data?.plans])
  const persistedPlanById = useMemo(
    () => new Map(persistedPlans.map((plan) => [plan.plan_id, plan])),
    [persistedPlans],
  )
  const resolvePlan = useCallback((plan: PlanSummary) => {
    const candidates = [plan, persistedPlanById.get(plan.plan_id), planOverrides[plan.plan_id]].filter((value): value is PlanSummary => Boolean(value))
    return candidates.reduce((latest, candidate) => candidate.revision > latest.revision ? candidate : latest)
  }, [persistedPlanById, planOverrides])
  const renderedPlanIds = useMemo(
    () => new Set(items.filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan').map((item) => item.plan.plan_id)),
    [items],
  )
  const persistedSessionPlans = useMemo(
    () => persistedPlans.filter((plan) => plan.session_id === sessionId && !renderedPlanIds.has(plan.plan_id)).map(resolvePlan),
    [persistedPlans, renderedPlanIds, resolvePlan, sessionId],
  )
  const renderedSessionPlans = useMemo(
    () => items
      .filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan')
      .map((item) => resolvePlan(item.plan)),
    [items, resolvePlan],
  )
  const requestedPlan = persistedPlans.find(plan => plan.plan_id === searchParams.get('plan_id')
    && plan.session_id === sessionId && (plan.source || 'web') === 'web'
    && ['pending', 'approved', 'paused', 'failed'].includes(plan.status))
  const dockedPlan = requestedPlan ? resolvePlan(requestedPlan) : selectDockedPlan(renderedSessionPlans) ?? selectDockedPlan(persistedSessionPlans)
  const stopCurrentRun = async (cancelSubagents = false) => {
    if (dockedPlan?.status === 'running') {
      void commandPlanStatus(dockedPlan, 'pause')
    }
    const targetUser = user
    const targetSession = liveSessionId
    const targetRunId = effectiveRunId
    if (!targetUser || !targetSession || !targetRunId || stopping) return
    setStopping(true, targetUser, targetSession, targetRunId, chatRunToken)
    const accepted = await executeStopRequest(
      () => cancelRun(targetUser, targetRunId, targetSession, 'web', cancelSubagents),
      (error) => {
        abortChatRun(targetUser, targetSession, targetRunId, chatRunToken)
        const message = error instanceof Error
          ? `紧急停止请求失败：${error.message}`
          : '紧急停止请求失败，已断开当前响应'
        if (!isCurrentConversation(targetUser, targetSession)) return
        setLiveItems((current) => [
          ...finalizeCurrentRoundItems(current, {
            message: '紧急停止请求失败，当前响应已在前端断开',
            exception_type: 'StopRequestFailed',
            cancelled: true,
          }),
          { id: eventId('error'), kind: 'error', content: message },
        ])
      },
    )
    if (!accepted) setStopping(false, targetUser, targetSession, targetRunId, chatRunToken)
  }
  const revealPlan = (plan: PlanSummary) => {
    if (plan.plan_id !== dockedPlan?.plan_id) {
      navigate(`/tasks?user=${encodeURIComponent(user)}`)
      return
    }
    setCollapsedPlans((current) => {
      const next = new Set(current)
      next.delete(plan.plan_id)
      return next
    })
    window.requestAnimationFrame(() => taskPlanDockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }
  const totalRounds = Math.max(
    persistedUserMessages,
    Number(overview?.context.integration_count || 0),
  )
  const captureHistoryScroll = (element: HTMLDivElement) => {
    const bounds = element.getBoundingClientRect()
    const anchor = Array.from(element.querySelectorAll<HTMLElement>(
      '.msg.is-user, .assistant-response, .tool-call, .trace, .guidance-message',
    )).find((candidate) => {
      const rect = candidate.getBoundingClientRect()
      return rect.height > 0 && rect.bottom > bounds.top && rect.top < bounds.bottom
    }) ?? null
    return {
      scrollHeight: element.scrollHeight, scrollTop: element.scrollTop,
      anchor, anchorTop: anchor?.getBoundingClientRect().top ?? 0,
    }
  }
  const loadEarlierHistory = async () => {
    const element = scrollRef.current
    if (!element || !historyQuery.hasNextPage || historyQuery.isFetchingNextPage || loadingEarlierRef.current) return
    loadingEarlierRef.current = true
    followOutputRef.current = false
    setShowFollowOutput(true)
    prependSnapshotRef.current = captureHistoryScroll(element)
    const previousPageCount = historyQuery.data?.pages.length ?? 0
    try {
      const result = await historyQuery.fetchNextPage({ cancelRefetch: false, throwOnError: true })
      if (!isCurrentConversation(user, sessionId)) return
      if ((result.data?.pages.length ?? 0) === previousPageCount) {
        prependSnapshotRef.current = null
        loadingEarlierRef.current = false
      }
    } catch (error) {
      if (!isCurrentConversation(user, sessionId)) return
      prependSnapshotRef.current = null
      loadingEarlierRef.current = false
      if (navigationTargetRef.current?.scope === `${user}\u0000${sessionId}`) {
        navigationTargetRef.current = null
        setNavigationTarget(null)
        setConversationFeedback({
          tone: 'error',
          text: `消息定位失败：${error instanceof Error ? error.message : '历史读取失败'}。请重新选择轮次重试。`,
        })
      }
    }
  }
  const handleChatScroll = () => {
    const element = scrollRef.current
    if (!element) return
    const movingUp = element.scrollTop < lastScrollTopRef.current
    lastScrollTopRef.current = element.scrollTop
    if (navigationTargetRef.current?.scope === `${user}\u0000${sessionId}`) return
    if (prependSnapshotRef.current) {
      // The reader may keep scrolling while the next page is in flight.
      prependSnapshotRef.current = captureHistoryScroll(element)
      return
    }
    const bounds = element.getBoundingClientRect()
    const viewportCenter = bounds.top + element.clientHeight / 2
    const visibleUserMessages = Array.from(element.querySelectorAll<HTMLElement>('[data-integration-number]'))
    const activeRound = visibleUserMessages.reduce((closest, candidate) => {
      const rect = candidate.getBoundingClientRect()
      const distance = Math.abs(rect.top + rect.height / 2 - viewportCenter)
      return distance < closest.distance
        ? { round: Number(candidate.dataset.integrationNumber || 0), distance }
        : closest
    }, { round: 0, distance: Number.POSITIVE_INFINITY }).round
    const firstLoadedRound = historyData?.pagination?.first_integration_number ?? 0
    const nearLoadedHistoryEdge = shouldPrefetchEarlierHistory(
      activeRound, firstLoadedRound, Boolean(historyQuery.hasNextPage), 2,
    )
    if ((movingUp && element.scrollTop <= 120) || nearLoadedHistoryEdge) {
      followOutputRef.current = false
      setShowFollowOutput(true)
      void loadEarlierHistory()
      return
    }
    const following = isNearScrollBottom(element)
    followOutputRef.current = following
    setShowFollowOutput(!following)
  }
  const handleChatContentClick = (event: MouseEvent<HTMLDivElement>) => {
    const target = event.target as Element
    if (!target.closest('.assistant-process-toggle, .tool-call-head, .trace-head')) return
    followOutputRef.current = false
    setShowFollowOutput(true)
  }
  const jumpToUserMessage = (id: string) => {
    followOutputRef.current = false
    setShowFollowOutput(true)
    setConversationFeedback(null)
    const target = { id, scope: `${user}\u0000${sessionId}` }
    navigationTargetRef.current = target
    setNavigationTarget(target)
  }
  useEffect(() => {
    if (!navigationTarget || navigationTarget.scope !== `${user}\u0000${sessionId}`) return
    const element = scrollRef.current
    if (!element) return
    const target = Array.from(element.querySelectorAll<HTMLElement>('[data-user-message-id]'))
      .find((candidate) => candidate.dataset.userMessageId === navigationTarget.id)
    if (!target) {
      if (historyQuery.isFetching || loadingEarlierRef.current) return
      if (historyQuery.hasNextPage) {
        void loadEarlierHistory()
      } else {
        navigationTargetRef.current = null
        setNavigationTarget(null)
        setConversationFeedback({ tone: 'error', text: '消息定位失败：这条发言已不在当前对话中，请刷新后重试。' })
      }
      return
    }
    const containerRect = element.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const top = targetRect.top - containerRect.top + element.scrollTop
      - Math.max(24, (element.clientHeight - targetRect.height) / 2)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    else element.scrollTop = Math.max(0, top)
    navigationTargetRef.current = null
    setNavigationTarget(null)
  })
  const resumeFollowingOutput = () => {
    const element = scrollRef.current
    if (!element) return
    navigationTargetRef.current = null
    setNavigationTarget(null)
    followOutputRef.current = true
    setShowFollowOutput(false)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
    else element.scrollTop = element.scrollHeight
  }
  const referenceKnowledge = (document: KnowledgeDocumentSummary) => {
    const referenceId = `${document.scope}:${document.relative_path}`
    const reference = `[知识库引用 ${referenceId}] ${document.title}`
    setDraft((current) => {
      if (current.includes(`[知识库引用 ${referenceId}]`)) return current
      const existing = current.trimEnd()
      return existing ? `${existing}\n${reference}` : reference
    })
    setKnowledgeDrawerOpen(false)
  }
  const referenceKnowledgeDirectory = (scope: 'user' | 'shared' | 'global', relativePath: string, name: string) => {
    const referenceId = `${scope}:${relativePath}`
    const marker = `[知识库目录引用 ${referenceId}]`
    setDraft((current) => {
      if (current.includes(marker)) return current
      const existing = current.trimEnd()
      return existing ? `${existing}\n${marker} ${name}` : `${marker} ${name}`
    })
    setKnowledgeDrawerOpen(false)
  }
  const referenceCapability = (item: CapabilityReferenceItem) => {
    const marker = capabilityReferenceMarker(item)
    const reference = capabilityReferenceLine(item)
    setDraft((current) => {
      if (current.includes(marker)) return current
      const existing = current.trimEnd()
      return existing ? `${existing}\n${reference}` : reference
    })
    setCapabilityDrawerOpen(false)
  }
  const conversationItems = useMemo(() => archiveTerminalPlansInConversation(
    items.filter((item) => item.kind !== 'context_compression' && item.kind !== 'subagent_progress'),
    [
      ...persistedPlans
        .filter((plan) => plan.session_id === sessionId)
        .map(resolvePlan),
      ...renderedSessionPlans,
    ],
  ), [items, persistedPlans, renderedSessionPlans, resolvePlan, sessionId])
  const conversationBlocks = useMemo(() => groupConversationItems(conversationItems), [conversationItems])
  const userMessageMarkers = useMemo(
    () => buildUserMessageMarkers(
      conversationItems, historyData?.pagination?.first_integration_number ?? 1, historyData?.user_messages,
    ),
    [conversationItems, historyData?.pagination?.first_integration_number, historyData?.user_messages],
  )
  const showWelcome = items.length === 0 && !editingSource

  return <><ChatPageView {...{
    showWelcome, scrollRef, handleChatScroll, handleChatContentClick, sessionId, historyQuery,
    user, overview, reasoningControl, activePlan, dockedPlan, activeTaskOpen, setActiveTaskOpen, navigate, setDraft,
    recentTasks,
    tokenTotal: runtimeTokensQuery.data?.tokens.total_tokens,
    tokenCacheRate: runtimeTokensQuery.data?.tokens.cache_rate,
    outputTokens: runtimeTokensQuery.data?.tokens.received_tokens,
    liveItems, items, historyData, conversationBlocks, userAvatarUrl,
    editedSources, running, lastUserMessage, editAndResend, conversationBusy, conversationFeedback, setConversationFeedback, copyMessage, copiedItem,
    resolvePlan, revealPlan, showFollowOutput, resumeFollowingOutput, userMessageMarkers, totalRounds,
    navigatingMessage: Boolean(navigationTarget), jumpToUserMessage, runRetryNotice, setRunErrorNotice, runErrorNotice,
    taskPlanDockRef, collapsedPlans,
    planActions, guidancePreviewItem, followUpQueue, queueFollowUp, guideDraft, reorderNextTurnMessages, liveSessionId, removeNextTurnMessage,
    setNextTurnMessageStatus, draft, stopping, pendingUploads, uploading,
    uploadFeedback, setUploadFeedback, setPendingUploads, editingSource, cancelEditAndResend,
    regenerateLastResponse,
    uploadFiles, setCapabilityDrawerOpen,
    setKnowledgeDrawerOpen, sendGuidance, send, stopCurrentRun, knowledgeDrawerOpen,
    knowledgeQuery, referenceKnowledge, referenceKnowledgeDirectory, capabilityDrawerOpen, capabilityItems, expandsQuery, skillsQuery,
    referenceCapability,
  }} />
    <GlobalConfirmDialog open={Boolean(clearTarget)} title="清空此对话？"
      description={clearRemovedRef.current
        ? '原对话已清理，继续操作只会创建新对话。'
        : '将删除当前对话归档并创建一个新对话，此操作无法撤销。仅清除当前会话，不删除已上传文件。'}
      detail={clearTarget?.sessionId || '当前空白对话'} error={clearError || undefined}
      icon={<Trash2 size={22} />} tone="danger"
      confirmLabel={clearRemovedRef.current ? '重试创建新对话' : '确认清空'}
      pendingLabel="正在清空…" pending={clearPending}
      onConfirm={() => { void confirmClearConversation() }}
      onCancel={() => { if (!clearExecutingRef.current) { setClearTarget(null); setClearError('') } }} />
  </>

}
