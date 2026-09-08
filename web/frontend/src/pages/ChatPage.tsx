import { Fragment, Suspense, lazy, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BrainCircuit,
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
  Save,
  Shapes,
  TimerReset,
  Trash2,
  UserRound,
  Video,
  Workflow,
  X,
  Zap,
} from 'lucide-react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { ApiError, cancelRun, cancelSessionLongTask, closeSession, commandPlan, compressSession, deleteSession, getExpands, getHistory, getKnowledge, getSense, getSessionLongTask, getSkills, getTasks, getUserArtifactUrl, getUserAttachmentThumbnailUrl, getUserFileDownloadUrl, getUserFilePreviewUrl, retryPlanStep, setSessionLongTask, streamChat, submitGuidance, undoLastRound, uploadUserFile } from '../api/client'
import { AgentComposer } from '../components/AgentComposer'
import { buildCapabilityReferenceItems, capabilityReferenceLine, capabilityReferenceMarker } from '../components/capabilityReferences'
import { CONVERSATION_COMMAND_EVENT, chatRunKey, type ChatItemsUpdater, type ConversationCommandAction, type PendingNextTurnMessage, type ShellOutletContext } from '../components/AppShell'
import { PlainTextMessage } from '../components/Chat/PlainTextMessage'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from '../components/CapabilityReferenceDrawer'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { LongTaskBubble } from '../components/LongTaskBubble'
import { formatBytes, formatDateTime, statusLabel } from '../components/ModuleUi'
import { RecentActivityCard, type ScheduledTaskItem, type SenseDataItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import { UserMessageNavigator, type UserMessageMarker } from '../components/UserMessageNavigator'
import type { ChatItem, CronTaskSummary, HistoryResponse, InputAttachment, KnowledgeDocumentSummary, LongTaskResponse, LongTaskState, MediaArtifact, PlanSummary, RunEvent, SenseSourceSummary } from '../types/api'
import { copyText } from '../utils/clipboard'
import { playUserCompletionSound, playUserFailureSound } from '../utils/completionSound'
import { randomUUID } from '../randomId'
import { chatDraftKey, EMPTY_CHAT_DRAFT, useChatDraftStore, type PendingUploadedFile } from '../store/chatDrafts'
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
  shouldShowLongTaskBubble,
  type RunErrorNotice,
  type RunRetryNotice,
} from './chatRunSupport'
export { isFailedRunCompletion, isSuccessfulRunCompletion, removeSubmittedUploads, shouldShowLongTaskBubble } from './chatRunSupport'
import {
  buildScheduledTaskItems,
  buildSenseDataItems,
  ContextCompressionBubble,
  greetingLabel,
  GuidanceMessage,
  MediaArtifactCard,
  PendingAttachmentTray,
  quickStartCards,
  TaskPlanRecord,
  UserAttachmentCard,
  UserMessageAvatar,
  type GuidanceDisplayItem,
  type GuidanceItem,
} from './chatPresentation'
export { buildScheduledTaskItems, buildSenseDataItems, ContextCompressionBubble, formatSenseUpdateInterval, mediaArtifactUrl } from './chatPresentation'
import { createPlanWorkflow, createSendHandler } from './chatWorkflows'
import { ChatPageView } from './ChatPageView'

const MarkdownMessage = lazy(async () => ({
  default: (await import('../components/Chat/MarkdownMessage')).MarkdownMessage,
}))

export function ChatPage() {
  const { user, userAvatarUrl, sessionId, clientId, chatRunning: chatRunningForSession, setChatRunning: setRunning, chatRunId: activeRunId, chatRunSessionId, setChatRunId: setActiveRunId, setChatAbortController, abortChatRun, chatRuns, beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus, removeNextTurnMessage, finishChatRun, clearChatRun, setSessionId, detachSession, notifySessionDeleted, sessions, refreshSessions, createNewSession, overview, refreshOverview, openCommandPanel } = useOutletContext<ShellOutletContext>()
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
  } | null>(null)
  const [editedSources, setEditedSources] = useState<Set<string>>(() => new Set())
  const [copiedItem, setCopiedItem] = useState('')
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false)
  const [knowledgeDrawerOpen, setKnowledgeDrawerOpen] = useState(false)
  const [capabilityDrawerOpen, setCapabilityDrawerOpen] = useState(false)
  const [conversationBusy, setConversationBusy] = useState<'save' | 'clear' | 'compress' | 'retry' | 'edit' | ''>('')
  const [conversationFeedback, setConversationFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [longTaskBusy, setLongTaskBusy] = useState(false)
  const [activeTaskOpen, setActiveTaskOpen] = useState(false)
  const [collapsedPlans, setCollapsedPlans] = useState<Set<string>>(() => new Set())
  const [planOverrides, setPlanOverrides] = useState<Record<string, PlanSummary>>({})
  const [planMutationNotices, setPlanMutationNotices] = useState<Record<string, string>>({})
  const [showFollowOutput, setShowFollowOutput] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [runRetryNotice, setRunRetryNotice] = useState<RunRetryNotice | null>(null)
  const [runErrorNotice, setRunErrorNotice] = useState<RunErrorNotice | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const composerPlanDockRef = useRef<HTMLDivElement | null>(null)
  const followOutputRef = useRef(true)
  const loadingEarlierRef = useRef(false)
  const prependSnapshotRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null)
  const submittedUploadsRef = useRef(new Map<string, PendingUploadedFile[]>())
  const completionSoundRunIdsRef = useRef(new Set<string>())
  const failureSoundRunIdsRef = useRef(new Set<string>())
  const consumingNextTurnRef = useRef(false)
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
  const activeCompression = [...liveItems].reverse().find(
    (item): item is Extract<ChatItem, { kind: 'context_compression' }> => item.kind === 'context_compression' && (!item.runId || item.runId === effectiveRunId),
  )
  const setLiveItems = (updater: ChatItemsUpdater) => {
    if (liveSessionId) updateChatRunItems(user, liveSessionId, updater)
  }
  const hasCommitted = useMemo(() => {
    if (!sessionId) return false
    return sessionId === locallyCommittedSessionRef.current
      || sessions.some((session) => session.session_id === sessionId)
  }, [sessionId, sessions])
  const historyQuery = useInfiniteQuery({
    queryKey: ['history', user, sessionId],
    queryFn: ({ pageParam }) => getHistory(user, sessionId, {
      limit: HISTORY_PAGE_SIZE,
      before: pageParam,
    }),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.pagination?.has_more_before
      ? lastPage.pagination.next_before ?? undefined
      : undefined,
    enabled: Boolean(user && sessionId && hasCommitted),
    retry: false,
  })
  const longTaskQuery = useQuery({
    queryKey: ['long-task', user, sessionId],
    queryFn: () => getSessionLongTask(user, sessionId),
    enabled: Boolean(user && sessionId && hasCommitted),
    retry: false,
    refetchInterval: (query) => {
      const status = query.state.data?.long_task.status
      return ['running', 'pausing', 'cancelling'].includes(String(status || '')) ? 1000 : false
    },
  })
  const tasksQuery = useQuery({
    queryKey: ['tasks', user, sessionId],
    queryFn: () => getTasks(user, sessionId),
    enabled: Boolean(user),
    refetchInterval: (query) => query.state.data?.plans.some((plan) => ['approved', 'running'].includes(plan.status)) ? 1200 : false,
  })
  useEffect(() => {
    if (!sessionId || !tasksQuery.dataUpdatedAt) return
    void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
  }, [queryClient, sessionId, tasksQuery.dataUpdatedAt, user])
  const senseQuery = useQuery({
    queryKey: ['sense', user],
    queryFn: () => getSense(user),
    enabled: Boolean(user),
  })
  const knowledgeQuery = useQuery({
    queryKey: ['knowledge', user],
    queryFn: () => getKnowledge(user),
    enabled: Boolean(user && knowledgeDrawerOpen),
  })
  const expandsQuery = useQuery({
    queryKey: ['expands', user],
    queryFn: () => getExpands(user),
    enabled: Boolean(user && capabilityDrawerOpen),
  })
  const skillsQuery = useQuery({
    queryKey: ['skills', user],
    queryFn: () => getSkills(user),
    enabled: Boolean(user && capabilityDrawerOpen),
  })
  const capabilityItems = useMemo(
    () => buildCapabilityReferenceItems(expandsQuery.data, skillsQuery.data),
    [expandsQuery.data, skillsQuery.data],
  )

  const historyData = useMemo(
    () => mergeHistoryPages(historyQuery.data?.pages),
    [historyQuery.data?.pages],
  )
  const historyItems = useMemo<ChatItem[]>(() => buildHistoryItems(historyData), [historyData])
  const persistedUserMessages = historyData?.pagination?.total_rounds
    ?? historyData?.messages.filter((message) => message.role === 'user').length
    ?? 0
  const handoffReady = liveRun?.phase === 'awaiting_history' && persistedUserMessages > liveRun.historyUserMessages
  const visibleLiveItems = handoffReady ? [] : liveItems
  const items = [...historyItems, ...visibleLiveItems]
  useEffect(() => {
    const conversationKey = `${user}\u0000${sessionId}`
    if (conversationKeyRef.current === conversationKey) return
    conversationKeyRef.current = conversationKey
    if (!running) lastAttemptSessionRef.current = sessionId
    followOutputRef.current = true
    loadingEarlierRef.current = false
    prependSnapshotRef.current = null
    setShowFollowOutput(false)
    setEditingSource(null)
    undoneRoundBaselineRef.current = null
    setEditedSources(new Set())
    setCopiedItem('')
    if (!running) setActiveRunId('', user, sessionId)
    setKnowledgeDrawerOpen(false)
    setCapabilityDrawerOpen(false)
    setConversationBusy('')
    setConversationFeedback(null)
    setLongTaskBusy(false)
    setStopping(false)
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
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: running ? 'auto' : 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [items.length, liveItems, running])

  useLayoutEffect(() => {
    const snapshot = prependSnapshotRef.current
    const element = scrollRef.current
    if (!snapshot || !element) return
    element.scrollTop = snapshot.scrollTop + (element.scrollHeight - snapshot.scrollHeight)
    prependSnapshotRef.current = null
    loadingEarlierRef.current = false
  }, [historyQuery.data?.pages.length])

  const send = createSendHandler({
    draft, user, running, stopping, uploading, sessionId, draftKey, clientId,
    persistedUserMessages, editingSource, lastAttemptSessionRef, currentUserRef,
    currentLiveSessionRef, submittedUploadsRef, undoneRoundBaselineRef,
    followOutputRef, locallyCommittedSessionRef, queryClient, beginChatRun,
    setRunRetryNoticeFor, setRunErrorNoticeFor, setDraft, setPendingUploads,
    setRunning, setActiveRunId, setConversationMenuOpen, setShowFollowOutput,
    setEditedSources, updateChatRunItems, setEditingSource, setChatAbortController,
    playCompletionSoundOnce, playFailureSoundOnce, setDraftUploads,
    refreshSessions, isCurrentConversation, moveDraft, setSessionId,
    refreshOverview, setDraftText, finishChatRun, setStopping,
  })

  useEffect(() => {
    const pending = liveRun?.nextTurnQueue[0]
    if (!pending || pending.status !== 'queued' || running || uploading || stopping || consumingNextTurnRef.current || !user || !liveSessionId) return
    consumingNextTurnRef.current = true
    setNextTurnMessageStatus(user, liveSessionId, pending.id, 'sending')
    void send(pending.content, {
      sessionId: liveSessionId,
      historyUserMessages: pending.historyUserMessages,
      uploadedFiles: pending.uploadedFiles,
      internalNextTurn: true,
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
  }, [liveRun?.nextTurnQueue, liveSessionId, removeNextTurnMessage, running, setNextTurnMessageStatus, stopping, uploading, user])

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
    abortChatRun(user, liveSessionId, effectiveRunId)
    await createNewSession()
    clearDraft(previousDraftKey)
    if (liveSessionId) clearChatRun(user, liveSessionId)
    setConversationMenuOpen(false)
  }

  const saveAndNewConversation = async () => {
    if (running || conversationBusy) return
    setConversationBusy('save')
    setConversationFeedback(null)
    const previousSessionId = sessionId
    let previousSessionClosed = false
    detachSession()
    try {
      if (previousSessionId) {
        await closeSession(user, previousSessionId, clientId)
        previousSessionClosed = true
      }
      await newConversation()
    } catch (error) {
      if (previousSessionId && !previousSessionClosed) setSessionId(previousSessionId)
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '保存当前对话失败' })
    } finally {
      setConversationBusy('')
    }
  }

  const clearConversation = async () => {
    if (running || conversationBusy) return
    if (sessionId && hasCommitted && !window.confirm('清空此对话将删除当前归档，并立即创建一个新对话。是否继续？')) return
    setConversationBusy('clear')
    setConversationFeedback(null)
    const previousSessionId = sessionId
    let previousSessionRemoved = false
    detachSession()
    try {
      if (previousSessionId && hasCommitted) {
        await deleteSession(user, previousSessionId, clientId)
        previousSessionRemoved = true
        notifySessionDeleted(previousSessionId)
        queryClient.removeQueries({ queryKey: ['history', user, previousSessionId] })
        if (locallyCommittedSessionRef.current === previousSessionId) locallyCommittedSessionRef.current = ''
        void refreshSessions()
        refreshOverview()
      } else if (previousSessionId) {
        await closeSession(user, previousSessionId, clientId)
        previousSessionRemoved = true
      }
      await newConversation()
    } catch (error) {
      if (previousSessionId && !previousSessionRemoved) setSessionId(previousSessionId)
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '清空当前对话失败' })
    } finally {
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
    try {
      const result = await compressSession(user, sessionId)
      const compressionText = result.compressed
        ? `上下文压缩完成，已整理 ${result.rounds_removed} 轮历史。`
        : '当前上下文较短，暂时无需压缩。'
      const memory = result.memory
      const memoryText = memory.status === 'queued'
        ? (Number(memory.pending_rounds || 0) > 0
            ? `记忆提取已转入后台，共有 ${Number(memory.pending_rounds)} 轮待处理。`
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
                : '当前没有可提取的完整对话轮次。')
          : memory.error?.message
            ? `记忆提取任务登记失败：${memory.error.message}`
            : '记忆提取未完成，已保留待后台重试。'
      setConversationFeedback({
        tone: memory.status === 'failed' ? 'error' : 'success',
        text: `${compressionText}${memoryText}`,
      })
      refreshOverview()
    } catch (error) {
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '手动上下文压缩失败' })
    } finally {
      setConversationBusy('')
    }
  }

  const editAndResend = async (id: string, content: string) => {
    if (running || conversationBusy || !lastUserMessage || lastUserMessage.id !== id) return
    const targetSession = sessionId || lastAttemptSessionRef.current
    const persistedRounds = persistedUserMessages
    const liveRounds = visibleLiveItems.filter((item) => item.kind === 'message' && item.role === 'user').length
    const expectedRound = persistedRounds + liveRounds
    if (!targetSession || expectedRound < 1) return
    setConversationBusy('edit')
    setConversationFeedback(null)
    try {
      const undo = await undoLastRound(user, targetSession, expectedRound, content)
      undoneRoundBaselineRef.current = {
        sessionId: targetSession,
        remainingRounds: Math.max(0, undo.remaining_rounds),
      }
      setLiveItems((current) => dropLastLiveRound(current))
      if (sessionId) {
        void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] }).catch(() => undefined)
      }
      const editableContent = undo.prompt || content
      setDraft(editableContent)
      setEditingSource({
        id,
        content,
        sessionId: targetSession,
        remainingRounds: Math.max(0, undo.remaining_rounds),
      })
      setConversationFeedback({ tone: 'success', text: '最新一轮已撤销，原问题已放回输入框，可修改后重新发送。' })
      window.requestAnimationFrame(() => {
        const input = document.querySelector<HTMLTextAreaElement>('textarea[aria-label="消息内容"]')
        if (!input) return
        input.focus()
        input.setSelectionRange(input.value.length, input.value.length)
      })
    } catch (error) {
      setConversationFeedback({
        tone: 'error',
        text: error instanceof Error ? error.message : '撤销最新一轮并进入编辑状态失败',
      })
    } finally {
      setConversationBusy('')
    }
  }

  const cancelEditAndResend = () => {
    setEditingSource(null)
    setDraft('')
    setConversationFeedback({
      tone: 'success',
      text: '已取消编辑；被撤销的轮次不会恢复，下一条消息将直接作为新一轮发送。',
    })
    if (sessionId) {
      void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] }).catch(() => undefined)
    }
  }

  const copyMessage = async (id: string, content: string) => {
    await copyText(content)
    setCopiedItem(id)
    window.setTimeout(() => setCopiedItem((current) => current === id ? '' : current), 1200)
  }

  const sendGuidance = async () => {
    const guidance = draft.trim()
    const uploadedFiles = pendingUploads.map((file) => ({ ...file }))
    if ((!guidance && !uploadedFiles.length) || !user || !running || !effectiveRunId || uploading) return
    const targetUser = user
    const targetSession = liveSessionId
    const targetRunId = effectiveRunId
    if (!targetSession) return
    const id = eventId('guidance')
    const attachments = uploadedFiles.map(pendingInputAttachment)
    const displayText = guidance || `附件引导：${uploadedFiles.map((file) => file.name).join('、')}`
    const clearSubmittedInput = () => {
      setDraft('')
      if (uploadedFiles.length) {
        setDraftUploads(draftKey, (current) => removeSubmittedUploads(current, uploadedFiles))
      }
    }
    const queueForNextTurn = () => {
      if (!targetSession) return false
      setLiveItems((current) => current.filter((item) => item.id !== id))
      const message: PendingNextTurnMessage = {
        id,
        content: guidance,
        uploadedFiles,
        historyUserMessages: Math.max(
          persistedUserMessages,
          (liveRun?.historyUserMessages ?? persistedUserMessages) + 1,
        ),
        status: 'queued',
      }
      queueNextTurnMessage(targetUser, targetSession, message)
      clearSubmittedInput()
      return true
    }
    if (stopping) {
      queueForNextTurn()
      return
    }
    setLiveItems((current) => [...current, {
      id,
      kind: 'guidance',
      guidanceId: id,
      content: displayText,
      attachments,
      status: 'queued',
    }])
    clearSubmittedInput()
    try {
      const result = await submitGuidance(targetUser, targetRunId, guidance, {
        sessionId: targetSession,
        guidanceId: id,
        uploadedFiles: uploadedFiles.map((file) => file.path),
      })
      if (result.status === 'queued_next_turn') {
        queueForNextTurn()
      } else {
        clearSubmittedInput()
      }
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        if (queueForNextTurn()) return
      }
      setLiveItems((current) => current.map((item) => item.kind === 'guidance' && item.id === id ? { ...item, status: 'error' } : item))
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '运行中引导提交失败' }])
    }
  }

  const activePlan = overview?.active_plan
  const lastUserMessage = [...items].reverse().find((item) => item.kind === 'message' && item.role === 'user')
  const latestRunningGuidance = running
    ? [...visibleLiveItems].reverse().find((item): item is GuidanceItem => item.kind === 'guidance' && !item.finalized)
    : undefined
  const pendingNextTurn = [...(liveRun?.nextTurnQueue ?? [])]
    .reverse()
    .find((item) => item.status === 'queued' || item.status === 'error')
  const guidancePreviewItem: GuidanceDisplayItem | undefined = pendingNextTurn
    ? {
      id: pendingNextTurn.id,
      kind: 'guidance',
      content: pendingNextTurn.content,
      guidanceId: pendingNextTurn.id,
      attachments: pendingNextTurn.uploadedFiles?.map(pendingInputAttachment),
      status: pendingNextTurn.status === 'error' ? 'next_turn_error' : 'next_turn',
    }
    : latestRunningGuidance
  const regenerateLastResponse = async () => {
    if (running || conversationBusy || !lastUserMessage || lastUserMessage.kind !== 'message') return
    const prompt = lastUserMessage.content
    const targetSession = sessionId || lastAttemptSessionRef.current
    const persistedRounds = persistedUserMessages
    const liveRounds = visibleLiveItems.filter((item) => item.kind === 'message' && item.role === 'user').length
    const expectedRound = persistedRounds + liveRounds
    if (expectedRound < 1) return
    setConversationBusy('retry')
    setConversationFeedback(null)
    try {
      const undo = targetSession
        ? await undoLastRound(user, targetSession, expectedRound, prompt)
        : null
      setLiveItems((current) => dropLastLiveRound(current))
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
      }
      await send(prompt, {
        sessionId: targetSession || undefined,
        content: undo?.content?.length ? undo.content : undefined,
        // 重新生成会先撤销一轮再补回一轮，最终历史轮数不会增长。
        // 接管基线必须使用撤销后的轮数，否则持久化历史与流式缓存会同时显示。
        historyUserMessages: Math.max(0, expectedRound - 1),
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
      if (action === 'save') void saveAndNewConversation()
      else if (action === 'clear') void clearConversation()
      else if (action === 'compress') void compressCurrentConversation()
      else if (action === 'retry') void regenerateLastResponse()
    }
    window.addEventListener(CONVERSATION_COMMAND_EVENT, handleConversationCommand)
    return () => window.removeEventListener(CONVERSATION_COMMAND_EVENT, handleConversationCommand)
  }, [clearConversation, compressCurrentConversation, regenerateLastResponse, saveAndNewConversation])
  const recentTasks = useMemo(() => buildScheduledTaskItems(tasksQuery.data?.cron_tasks || []), [tasksQuery.data])
  const recentSenseData = useMemo(() => buildSenseDataItems(senseQuery.data?.sources || []), [senseQuery.data])
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
  const persistedPlans = tasksQuery.data?.plans || []
  const persistedPlanById = new Map(persistedPlans.map((plan) => [plan.plan_id, plan]))
  const resolvePlan = (plan: PlanSummary) => {
    const candidates = [plan, persistedPlanById.get(plan.plan_id), planOverrides[plan.plan_id]].filter((value): value is PlanSummary => Boolean(value))
    return candidates.reduce((latest, candidate) => candidate.revision > latest.revision ? candidate : latest)
  }
  const renderedPlanIds = new Set(items.filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan').map((item) => item.plan.plan_id))
  const persistedSessionPlans = persistedPlans.filter((plan) => plan.session_id === sessionId && !renderedPlanIds.has(plan.plan_id)).map(resolvePlan)
  const renderedSessionPlans = items
    .filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan')
    .map((item) => resolvePlan(item.plan))
  const dockedPlan = selectDockedPlan(renderedSessionPlans) ?? selectDockedPlan(persistedSessionPlans)
  const stopCurrentRun = async () => {
    if (dockedPlan?.status === 'running') {
      void commandPlanStatus(dockedPlan, 'pause')
    }
    const targetUser = user
    const targetSession = liveSessionId
    const targetRunId = effectiveRunId
    if (!targetUser || !targetSession || !targetRunId || stopping) return
    setStopping(true)
    const longTaskActive = ['running', 'pausing', 'cancelling'].includes(String(longTaskQuery.data?.long_task.status || ''))
    await executeStopRequest(
      () => longTaskActive
        ? cancelSessionLongTask(targetUser, targetSession)
        : cancelRun(targetUser, targetRunId, targetSession),
      (error) => {
        abortChatRun(targetUser, targetSession, targetRunId)
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
  }
  const toggleLongTask = async () => {
    if (!user || !sessionId || longTaskBusy) return
    setLongTaskBusy(true)
    setConversationFeedback(null)
    try {
      const response = await setSessionLongTask(user, sessionId, !longTaskQuery.data?.long_task.enabled)
      queryClient.setQueryData(['long-task', user, sessionId], response)
    } catch (error) {
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '长任务开关更新失败' })
    } finally {
      setLongTaskBusy(false)
    }
  }
  const stopLongTask = async () => {
    if (!user || !sessionId || longTaskBusy) return
    setLongTaskBusy(true)
    try {
      const response = await cancelSessionLongTask(user, sessionId)
      queryClient.setQueryData(['long-task', user, sessionId], response)
      setStopping(true)
    } catch (error) {
      setConversationFeedback({ tone: 'error', text: error instanceof Error ? error.message : '停止长任务失败' })
    } finally {
      setLongTaskBusy(false)
    }
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
    window.requestAnimationFrame(() => composerPlanDockRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }))
  }
  const userRoundCount = items.filter((item) => item.kind === 'message' && item.role === 'user').length
  const roundLimit = Math.max(1, Number(overview?.context.round_limit || 30))
  const reportedContextRound = Number(overview?.context.rounds)
  const currentRound = Math.max(
    1,
    Number.isFinite(reportedContextRound)
      ? reportedContextRound + (running ? 1 : 0)
      : Math.min(userRoundCount, roundLimit),
  )
  const totalRounds = Math.max(
    currentRound,
    Number(overview?.context.session_total_rounds || 0) + (running ? 1 : 0),
  )
  const loadEarlierHistory = async () => {
    const element = scrollRef.current
    if (!element || !historyQuery.hasNextPage || historyQuery.isFetchingNextPage || loadingEarlierRef.current) return
    loadingEarlierRef.current = true
    followOutputRef.current = false
    setShowFollowOutput(true)
    prependSnapshotRef.current = {
      scrollHeight: element.scrollHeight,
      scrollTop: element.scrollTop,
    }
    const previousPageCount = historyQuery.data?.pages.length ?? 0
    try {
      const result = await historyQuery.fetchNextPage()
      if ((result.data?.pages.length ?? 0) === previousPageCount) {
        prependSnapshotRef.current = null
        loadingEarlierRef.current = false
      }
    } catch {
      prependSnapshotRef.current = null
      loadingEarlierRef.current = false
    }
  }
  const handleChatScroll = () => {
    const element = scrollRef.current
    if (!element) return
    if (element.scrollTop <= 120 && historyQuery.hasNextPage) {
      followOutputRef.current = false
      setShowFollowOutput(true)
      void loadEarlierHistory()
      return
    }
    const following = isNearScrollBottom(element)
    followOutputRef.current = following
    setShowFollowOutput(!following)
  }
  const jumpToUserMessage = (id: string) => {
    const element = scrollRef.current
    if (!element) return
    const target = Array.from(element.querySelectorAll<HTMLElement>('[data-user-message-id]'))
      .find((candidate) => candidate.dataset.userMessageId === id)
    if (!target) return
    followOutputRef.current = false
    setShowFollowOutput(true)
    const containerRect = element.getBoundingClientRect()
    const targetRect = target.getBoundingClientRect()
    const top = targetRect.top - containerRect.top + element.scrollTop
      - Math.max(24, (element.clientHeight - targetRect.height) / 2)
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: Math.max(0, top), behavior: 'smooth' })
    else element.scrollTop = Math.max(0, top)
  }
  const resumeFollowingOutput = () => {
    const element = scrollRef.current
    if (!element) return
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
  const conversationItems = archiveTerminalPlansInConversation(
    items.filter((item) => item.kind !== 'context_compression'),
    [
      ...persistedPlans
        .filter((plan) => plan.session_id === sessionId)
        .map(resolvePlan),
      ...renderedSessionPlans,
    ],
  )
  const conversationBlocks = groupConversationItems(conversationItems)
  const userMessageMarkers = buildUserMessageMarkers(conversationItems, historyData?.pagination?.first_round ?? 1)
  const showWelcome = items.length === 0 && !editingSource

  return <ChatPageView {...{
    showWelcome, conversationMenuOpen, scrollRef, handleChatScroll, sessionId, historyQuery,
    user, overview, activePlan, dockedPlan, activeTaskOpen, setActiveTaskOpen, navigate, setDraft,
    recentTasks, recentSenseData, liveItems, items, historyData, conversationBlocks, userAvatarUrl,
    editedSources, running, lastUserMessage, editAndResend, conversationBusy, copyMessage, copiedItem,
    resolvePlan, revealPlan, showFollowOutput, resumeFollowingOutput, userMessageMarkers, totalRounds,
    loadEarlierHistory, jumpToUserMessage, runRetryNotice, setRunErrorNotice, runErrorNotice,
    activeCompression, longTaskQuery, longTaskBusy, stopLongTask, composerPlanDockRef, collapsedPlans,
    planActions, guidancePreviewItem, pendingNextTurn, liveSessionId, removeNextTurnMessage,
    setNextTurnMessageStatus, setConversationMenuOpen, draft, stopping, currentRound, roundLimit, pendingUploads, uploading,
    uploadFeedback, setUploadFeedback, setPendingUploads, editingSource, cancelEditAndResend,
    saveAndNewConversation, clearConversation, compressCurrentConversation, hasCommitted,
    toggleLongTask, regenerateLastResponse, conversationFeedback, uploadFiles, setCapabilityDrawerOpen,
    setKnowledgeDrawerOpen, openCommandPanel, sendGuidance, send, stopCurrentRun, knowledgeDrawerOpen,
    knowledgeQuery, referenceKnowledge, capabilityDrawerOpen, capabilityItems, expandsQuery, skillsQuery,
    referenceCapability,
  }} />

}
