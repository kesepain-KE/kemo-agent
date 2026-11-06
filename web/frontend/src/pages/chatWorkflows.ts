import type { QueryClient } from '@tanstack/react-query'
import { ApiError, commandPlan, retryPlanStep, streamChat, withdrawIntegration } from '../api/client'
import { t as tr } from '../i18n'
import type { ChatItem, OverviewResponse, PlanSummary, RunEvent } from '../types/api'
import { chatDraftKey, type PendingUploadedFile } from '../store/chatDrafts'
import { useCompressionStatus } from '../store/compressionStatus'
import { randomUUID } from '../randomId'
import {
  createDeltaEventBatcher,
  dropLastLiveRound,
  extractPlanSummary,
  finalizeCurrentRoundItems,
  isProvisionalRunError,
  isRetryAttemptProgress,
  prepareRunUserMessage,
  reduceRunEvent,
  resetCurrentRoundItemsForRetry,
  resetProviderResponseForRetry,
  resolveHistoryUserMessages,
} from './chatState'
import {
  createSessionId,
  eventId,
  isFailedRunCompletion,
  isSuccessfulRunCompletion,
  pendingInputAttachment,
  removeSubmittedUploads,
  type RunErrorNotice,
  type RunRetryNotice,
} from './chatRunSupport'

type AnyFn = (...args: any[]) => any
type MutableValue<T = any> = { current: T }

export interface SendWorkflowDependencies {
  draft: string
  user: string
  running: boolean
  stopping: boolean
  uploading: boolean
  sessionId: string
  draftKey: string
  clientId: string
  persistedUserMessages: number
  editingSource: { id: string; content: string; sessionId: string; remainingRounds: number } | null
  lastAttemptSessionRef: MutableValue<string>
  currentUserRef: MutableValue<string>
  currentLiveSessionRef: MutableValue<string>
  submittedUploadsRef: MutableValue<Map<string, PendingUploadedFile[]>>
  undoneRoundBaselineRef: MutableValue<{ sessionId: string; remainingRounds: number } | null>
  followOutputRef: MutableValue<boolean>
  locallyCommittedSessionRef: MutableValue<string>
  queryClient: QueryClient
  beginChatRun: AnyFn
  setRunRetryNoticeFor: (user: string, sessionId: string, value: RunRetryNotice | null) => void
  setRunErrorNoticeFor: (user: string, sessionId: string, value: RunErrorNotice | null) => void
  setConversationBusy: AnyFn
  setDraft: AnyFn
  setPendingUploads: AnyFn
  setRunning: AnyFn
  setActiveRunId: AnyFn
  setShowFollowOutput: AnyFn
  setEditedSources: AnyFn
  updateChatRunItems: AnyFn
  setEditingSource: AnyFn
  setChatAbortController: AnyFn
  playCompletionSoundOnce: AnyFn
  playFailureSoundOnce: AnyFn
  setDraftUploads: AnyFn
  refreshSessions: AnyFn
  isCurrentConversation: AnyFn
  moveDraft: AnyFn
  setSessionId: AnyFn
  refreshOverview: AnyFn
  setDraftText: AnyFn
  finishChatRun: AnyFn
  setStopping: AnyFn
  createNewSession: () => Promise<string | undefined>
  creatingSendSessionRef: MutableValue<boolean>
}

export function createSendHandler(dependencies: SendWorkflowDependencies) {
  const {
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
  } = dependencies

return async (
  promptOverride?: string,
  options: {
    sessionId?: string
    content?: Array<Record<string, unknown>>
    historyUserMessages?: number
    uploadedFiles?: PendingUploadedFile[]
    internalNextTurn?: boolean
    preserveDraft?: boolean
    userMessageId?: string
  } = {},
) => {
  const prompt = (promptOverride ?? draft).trim()
  const uploadedFiles = options.uploadedFiles ?? []
  const hasContent = Boolean(options.content?.length)
  if ((!prompt && !hasContent && !uploadedFiles.length) || !user || (!options.internalNextTurn && (running || stopping)) || uploading) return false
  if (creatingSendSessionRef.current) return false
  let activeSession = options.sessionId || sessionId
  if (!activeSession) {
    creatingSendSessionRef.current = true
    try {
      activeSession = await createNewSession() || ''
      if (!activeSession) return false
    } catch (error) {
      setRunErrorNoticeFor(user, currentLiveSessionRef.current, {
        id: eventId('run-error'),
        message: error instanceof Error ? error.message : '创建对话失败，请重试',
      })
      return false
    } finally { creatingSendSessionRef.current = false }
  }
  let historyUserMessages = resolveHistoryUserMessages(
    activeSession,
    sessionId,
    persistedUserMessages,
    options.historyUserMessages,
    editingSource,
    undoneRoundBaselineRef.current,
  )
  if (editingSource?.sessionId === activeSession) {
    creatingSendSessionRef.current = true
    setConversationBusy('edit')
    try {
      const undo = await withdrawIntegration(
        user, activeSession, editingSource.remainingRounds, editingSource.content,
      )
      historyUserMessages = undo.integration_count
      undoneRoundBaselineRef.current = { sessionId: activeSession, remainingRounds: historyUserMessages }
      updateChatRunItems(user, activeSession, (current: ChatItem[]) => dropLastLiveRound(current))
      await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
    } catch (error) {
      setRunErrorNoticeFor(user, activeSession, {
        id: eventId('run-error'),
        message: error instanceof Error ? error.message : tr('撤销最新一轮失败，请重试'),
      })
      return false
    } finally {
      creatingSendSessionRef.current = false
      setConversationBusy('')
    }
  }
  const targetUser = user
  const submissionDraftKey = chatDraftKey(user, activeSession)
  const finalDraftKey = submissionDraftKey
  lastAttemptSessionRef.current = activeSession
  currentUserRef.current = targetUser
  currentLiveSessionRef.current = activeSession
  const runId = `run_${randomUUID().replaceAll('-', '')}`
  let activeRunIdForScope = runId
  if (uploadedFiles.length) {
    submittedUploadsRef.current.set(runId, uploadedFiles.map((file) => ({ ...file })))
  }
  beginChatRun(targetUser, activeSession, runId, historyUserMessages)
  setRunRetryNoticeFor(targetUser, activeSession, null)
  setRunErrorNoticeFor(targetUser, activeSession, null)
  if (!options.preserveDraft) setDraft('')
  if (uploadedFiles.length) {
    setPendingUploads((current: any) => removeSubmittedUploads(current, uploadedFiles))
  }
  if (draftKey !== submissionDraftKey) moveDraft(draftKey, submissionDraftKey)
  setRunning(true, targetUser, activeSession, runId)
  setActiveRunId(runId, targetUser, activeSession)
  followOutputRef.current = true
  setShowFollowOutput(false)
  if (editingSource) setEditedSources((current: any) => new Set(current).add(editingSource.id))
  const displayedPrompt = prompt
  const userMessageId = options.userMessageId || eventId('user')
  const userItem: Extract<ChatItem, { kind: 'message' }> = {
    id: userMessageId,
    kind: 'message',
    role: 'user',
    content: displayedPrompt,
    attachments: uploadedFiles.map(pendingInputAttachment),
    edited: Boolean(editingSource),
    originalContent: editingSource?.content,
  }
  updateChatRunItems(
    user,
    activeSession,
    (current: any) => prepareRunUserMessage(current, userItem, Boolean(options.internalNextTurn)),
  )
  setEditingSource(null)
  const controller = new AbortController()
  setChatAbortController(controller, targetUser, activeSession, runId)
  let committed = false
  let successful = false
  let restoreDraftAfterFailure = false
  let terminalReceived = false
  const deltaBatcher = createDeltaEventBatcher((events) => {
    updateChatRunItems(user, activeSession, (current: any) => (
      events.reduce((next, event) => reduceRunEvent(next, event), current)
    ))
  })
  try {
    await streamChat({
      user,
      sessionId: activeSession,
      clientId,
      prompt: options.content?.length ? '' : prompt,
      content: options.content,
      uploadedFiles: uploadedFiles.map((file) => file.path),
      runId,
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === 'usage') {
          const rate = event.metadata?.session_cache_hit_rate
          if (rate === null || typeof rate === 'number') {
            const queryKey = ['overview', user, activeSession]
            void queryClient.cancelQueries({ queryKey, exact: true }, { revert: false })
            const updated = queryClient.setQueryData<OverviewResponse>(queryKey, (current) => current ? {
              ...current,
              context: { ...current.context, usage: { ...current.context.usage, cache_hit_rate: rate } },
            } : current)
            if (!updated) void queryClient.invalidateQueries({ queryKey, exact: true })
          }
        }
        if (event.type === 'context_compression') {
          const status = String(event.metadata?.status || 'started')
          useCompressionStatus.getState().setStatus(user, activeSession, {
            status: status === 'ready' || status === 'failed' ? status : 'started',
            trigger: String(event.metadata?.trigger || 'automatic'),
            historyTokensBefore: Math.max(0, Number(event.metadata?.history_tokens_before || 0)),
            historyTokensAfter: Math.max(0, Number(event.metadata?.history_tokens_after || 0)),
            absorbedCards: Math.max(0, Number(event.metadata?.absorbed_cards || 0)),
            content: String(event.content || ''),
          })
        }
        if (event.type === 'retrying') {
          const failedAttempt = Math.max(1, Number(event.metadata?.failed_attempt || 1))
          const nextAttempt = Math.max(failedAttempt + 1, Number(event.metadata?.next_attempt || failedAttempt + 1))
          const maxAttempts = Math.max(nextAttempt, Number(event.metadata?.max_attempts || 6))
          const boundaryKey = Math.max(1, Number(event.metadata?.run_attempt || nextAttempt))
          // Apply deltas from the failed attempt before sealing it as a
          // snapshot. Otherwise a pending batch can be flushed after the
          // boundary and mix failed-attempt text with the next attempt.
          deltaBatcher.flush()
          setRunRetryNoticeFor(targetUser, activeSession, { failedAttempt, nextAttempt, maxAttempts })
          setRunErrorNoticeFor(targetUser, activeSession, null)
          updateChatRunItems(user, activeSession, (current: ChatItem[]) => event.metadata?.phase === 'provider'
            ? resetProviderResponseForRetry(current, event)
            : resetCurrentRoundItemsForRetry(current, failedAttempt, nextAttempt, boundaryKey))
          return
        }
        if (isProvisionalRunError(event)) return
        if (event.type === 'text_delta' || event.type === 'reasoning_delta') {
          setRunRetryNoticeFor(targetUser, activeSession, null)
          deltaBatcher.push(event)
          return
        }
        if (isRetryAttemptProgress(event)) setRunRetryNoticeFor(targetUser, activeSession, null)
        deltaBatcher.flush()
        if (event.type === 'done') {
          terminalReceived = true
          committed = event.metadata?.committed !== false
          successful = isSuccessfulRunCompletion(event)
          setRunRetryNoticeFor(targetUser, activeSession, null)
          if (isFailedRunCompletion(event)) {
            const failure = event.metadata?.failure && typeof event.metadata.failure === 'object'
              ? event.metadata.failure as Record<string, unknown>
              : undefined
            setRunErrorNoticeFor(targetUser, activeSession, {
              id: eventId('run-error'),
              message: `最终错误：${String(event.error?.message || failure?.message || '智能体运行失败')}`,
            })
          } else {
            setRunErrorNoticeFor(targetUser, activeSession, null)
          }
          playCompletionSoundOnce(runId, event)
          playFailureSoundOnce(runId, event)
          const terminalStatus = String(event.metadata?.status || 'completed').toLowerCase()
          restoreDraftAfterFailure = ['failed', 'error'].includes(terminalStatus)
          const submitted = submittedUploadsRef.current.get(runId) ?? []
          submittedUploadsRef.current.delete(runId)
          if (successful && submitted.length) {
            setDraftUploads(submissionDraftKey, (current: any) => removeSubmittedUploads(current, submitted))
          }
        } else if (event.type === 'error') {
          terminalReceived = true
          committed = event.metadata?.committed !== false
          restoreDraftAfterFailure = true
          setRunRetryNoticeFor(targetUser, activeSession, null)
          setRunErrorNoticeFor(targetUser, activeSession, {
            id: eventId('run-error'),
            message: `最终错误：${String(event.error?.message || '智能体运行失败')}`,
          })
          playFailureSoundOnce(runId, event)
          submittedUploadsRef.current.delete(runId)
        }
        updateChatRunItems(user, activeSession, (current: any) => reduceRunEvent(current, event))
      },
    })
    deltaBatcher.flush()
    if (!terminalReceived) {
      terminalReceived = true
      restoreDraftAfterFailure = true
      const interruptionMessage = '响应流在终态事件到达前结束，未收到最终状态'
      const missingEvent: RunEvent = {
        type: 'error',
        error: { message: interruptionMessage, exception_type: 'MissingTerminalEvent' },
        metadata: { status: 'interrupted', terminal: false },
      }
      setRunRetryNoticeFor(targetUser, activeSession, null)
      setRunErrorNoticeFor(targetUser, activeSession, { id: eventId('run-error'), message: interruptionMessage })
      updateChatRunItems(user, activeSession, (current: any) => [
        ...finalizeCurrentRoundItems(current, {
          message: interruptionMessage,
          exception_type: 'MissingTerminalEvent',
        }),
        { id: eventId('error'), kind: 'error', content: interruptionMessage },
      ])
    }
    await refreshSessions()
    if (isCurrentConversation(targetUser, activeSession)) {
      locallyCommittedSessionRef.current = activeSession
    }
    if (
      committed
      && undoneRoundBaselineRef.current?.sessionId === activeSession
    ) undoneRoundBaselineRef.current = null
    if (committed) await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
    await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    refreshOverview()
  } catch (error) {
    deltaBatcher.flush()
    const aborted = (error as Error).name === 'AbortError'
    const missingTerminal = error instanceof ApiError && error.code === 'missing_terminal'
    const message = missingTerminal
      ? '响应流在终态事件到达前结束，未收到最终状态'
      : error instanceof Error ? error.message : '聊天失败'
    const interruptionMessage = missingTerminal
      ? message
      : '响应连接已中断，未收到最终状态'
    if (aborted) {
      setRunRetryNoticeFor(targetUser, activeSession, null)
      setRunErrorNoticeFor(targetUser, activeSession, null)
    }
    if (!terminalReceived) {
      updateChatRunItems(user, activeSession, (current: any) => {
        const finalized = finalizeCurrentRoundItems(current, {
          message: aborted
            ? '当前响应连接已中断'
            : interruptionMessage,
          exception_type: aborted
            ? 'ClientStreamAborted'
            : missingTerminal ? 'MissingTerminalEvent' : 'ClientStreamInterrupted',
          ...(aborted ? { cancelled: true } : {}),
        })
        return aborted
          ? finalized
          : [...finalized, { id: eventId('error'), kind: 'error', content: interruptionMessage }]
      })
    }
    if (!aborted && !terminalReceived) {
      setRunRetryNoticeFor(targetUser, activeSession, null)
      setRunErrorNoticeFor(targetUser, activeSession, { id: eventId('run-error'), message: interruptionMessage })
    }
    if (!terminalReceived && !aborted) {
      restoreDraftAfterFailure = true
    }
  } finally {
    deltaBatcher.dispose()
    submittedUploadsRef.current.delete(runId)
    if (restoreDraftAfterFailure && promptOverride === undefined && prompt) {
      setDraftText(finalDraftKey, (current: any) => current || prompt)
    }
    finishChatRun(targetUser, activeSession, committed, activeRunIdForScope)
    setChatAbortController(null, targetUser, activeSession, activeRunIdForScope)
    setStopping(false, targetUser, activeSession, activeRunIdForScope)
    setRunning(false, targetUser, activeSession, activeRunIdForScope)
  }
  return committed
}
}

export interface PlanWorkflowDependencies {
  user: string
  running: boolean
  sessionId: string
  clientId: string
  persistedUserMessages: number
  planMutationNotices: Record<string, string>
  lastAttemptSessionRef: MutableValue<string>
  currentUserRef: MutableValue<string>
  currentLiveSessionRef: MutableValue<string>
  followOutputRef: MutableValue<boolean>
  locallyCommittedSessionRef: MutableValue<string>
  queryClient: QueryClient
  setLiveItems: AnyFn
  setPlanOverrides: AnyFn
  setPlanMutationNotices: AnyFn
  beginChatRun: AnyFn
  setRunRetryNoticeFor: AnyFn
  setRunErrorNoticeFor: AnyFn
  updateChatRunItems: AnyFn
  setRunning: AnyFn
  setActiveRunId: AnyFn
  setShowFollowOutput: AnyFn
  setChatAbortController: AnyFn
  playCompletionSoundOnce: AnyFn
  playFailureSoundOnce: AnyFn
  refreshSessions: AnyFn
  isCurrentConversation: AnyFn
  setSessionId: AnyFn
  refreshOverview: AnyFn
  finishChatRun: AnyFn
  setStopping: AnyFn
  setCollapsedPlans: AnyFn
  navigate: AnyFn
}

export function createPlanWorkflow(dependencies: PlanWorkflowDependencies) {
  const {
    user, running, sessionId, clientId, persistedUserMessages, planMutationNotices,
    lastAttemptSessionRef, currentUserRef, currentLiveSessionRef, followOutputRef,
    locallyCommittedSessionRef, queryClient, setLiveItems, setPlanOverrides,
    setPlanMutationNotices, beginChatRun, setRunRetryNoticeFor, setRunErrorNoticeFor,
    updateChatRunItems, setRunning, setActiveRunId, setShowFollowOutput,
    setChatAbortController, playCompletionSoundOnce, playFailureSoundOnce,
    refreshSessions, isCurrentConversation, setSessionId, refreshOverview,
    finishChatRun, setStopping, setCollapsedPlans, navigate,
  } = dependencies

const commandPlanStatus = async (plan: PlanSummary, action: 'pause' | 'cancel') => {
  try {
    const response = await commandPlan(
      user,
      plan.plan_id,
      action,
      plan.session_id,
      plan.source || 'web',
    )
    const updated = extractPlanSummary(response.plan)
    if (updated) setPlanOverrides((current: any) => ({ ...current, [updated.plan_id]: updated }))
    await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
  } catch (error) {
    setLiveItems((current: any) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划更新失败' }])
  }
}

const retryFailedPlanStep = async (plan: PlanSummary, stepId: string) => {
  try {
    const response = await retryPlanStep(user, plan.plan_id, stepId, plan.revision, plan.session_id)
    const updated = extractPlanSummary(response.plan)
    if (updated) setPlanOverrides((current: any) => ({ ...current, [updated.plan_id]: updated }))
    setPlanMutationNotices((current: any) => ({
      ...current,
      [plan.plan_id]: response.activated
        ? '失败步骤已重置，计划已自动恢复执行。'
        : response.reason === 'fix_incomplete'
          ? '当前步骤已重置，仍有其他失败步骤需要修正。'
          : '失败步骤已重置，计划等待继续。',
    }))
    await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
  } catch (error) {
    setLiveItems((current: any) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划步骤重试失败' }])
  }
}

const executePlan = async (plan: PlanSummary) => {
  if (!user || running) return
  const activeSession = plan.session_id || sessionId || createSessionId()
  const targetUser = user
  lastAttemptSessionRef.current = activeSession
  currentUserRef.current = targetUser
  currentLiveSessionRef.current = activeSession
  const runId = `run_${randomUUID().replaceAll('-', '')}`
  const historyUserMessages = activeSession === sessionId ? persistedUserMessages : 0
  beginChatRun(targetUser, activeSession, runId, historyUserMessages)
  if (!sessionId) setSessionId(activeSession)
  setRunRetryNoticeFor(targetUser, activeSession, null)
  setRunErrorNoticeFor(targetUser, activeSession, null)
  updateChatRunItems(targetUser, activeSession, (current: any) => [
    ...current,
    { id: eventId('plan_execution'), kind: 'execution_marker', planId: plan.plan_id },
  ])
  setPlanOverrides((current: any) => ({
    ...current,
    [plan.plan_id]: { ...plan, status: 'running', revision: plan.revision + 1 },
  }))
  setRunning(true, targetUser, activeSession, runId)
  setActiveRunId(runId, targetUser, activeSession)
  followOutputRef.current = true
  setShowFollowOutput(false)
  const controller = new AbortController()
  setChatAbortController(controller, targetUser, activeSession, runId)
  let committed = false
  let refreshedRunningPlan = false
  let terminalReceived = false
  const deltaBatcher = createDeltaEventBatcher((events) => {
    updateChatRunItems(user, activeSession, (current: any) => (
      events.reduce((next, event) => reduceRunEvent(next, event), current)
    ))
  })
  try {
    await streamChat({
      user,
      sessionId: activeSession,
      clientId,
      prompt: '',
      planId: plan.plan_id,
      runId,
      signal: controller.signal,
      onEvent: (event) => {
        if (event.type === 'context_compression') {
          const status = String(event.metadata?.status || 'started')
          useCompressionStatus.getState().setStatus(user, activeSession, {
            status: status === 'ready' || status === 'failed' ? status : 'started',
            trigger: String(event.metadata?.trigger || 'automatic'),
            historyTokensBefore: Math.max(0, Number(event.metadata?.history_tokens_before || 0)),
            historyTokensAfter: Math.max(0, Number(event.metadata?.history_tokens_after || 0)),
            absorbedCards: Math.max(0, Number(event.metadata?.absorbed_cards || 0)),
            content: String(event.content || ''),
          })
        }
        if (!refreshedRunningPlan) {
          refreshedRunningPlan = true
          void queryClient.invalidateQueries({ queryKey: ['tasks', user] })
        }
        if (event.type === 'retrying') {
          const failedAttempt = Math.max(1, Number(event.metadata?.failed_attempt || 1))
          const nextAttempt = Math.max(failedAttempt + 1, Number(event.metadata?.next_attempt || failedAttempt + 1))
          const maxAttempts = Math.max(nextAttempt, Number(event.metadata?.max_attempts || 6))
          const boundaryKey = Math.max(1, Number(event.metadata?.run_attempt || nextAttempt))
          // Keep the retry boundary ordered with the buffered failed-attempt
          // deltas so they cannot be applied after the snapshot is sealed.
          deltaBatcher.flush()
          setRunRetryNoticeFor(targetUser, activeSession, { failedAttempt, nextAttempt, maxAttempts })
          setRunErrorNoticeFor(targetUser, activeSession, null)
          updateChatRunItems(user, activeSession, (current: ChatItem[]) => event.metadata?.phase === 'provider'
            ? resetProviderResponseForRetry(current, event)
            : resetCurrentRoundItemsForRetry(current, failedAttempt, nextAttempt, boundaryKey))
          return
        }
        if (isProvisionalRunError(event)) return
        if (event.type === 'text_delta' || event.type === 'reasoning_delta') {
          setRunRetryNoticeFor(targetUser, activeSession, null)
          deltaBatcher.push(event)
          return
        }
        if (isRetryAttemptProgress(event)) setRunRetryNoticeFor(targetUser, activeSession, null)
        deltaBatcher.flush()
        if (event.type === 'done') {
          terminalReceived = true
          committed = event.metadata?.committed !== false
          setRunRetryNoticeFor(targetUser, activeSession, null)
          if (isFailedRunCompletion(event)) {
            const failure = event.metadata?.failure && typeof event.metadata.failure === 'object'
              ? event.metadata.failure as Record<string, unknown>
              : undefined
            setRunErrorNoticeFor(targetUser, activeSession, { id: eventId('run-error'), message: `最终错误：${String(event.error?.message || failure?.message || '任务计划执行失败')}` })
          } else {
            setRunErrorNoticeFor(targetUser, activeSession, null)
          }
          playCompletionSoundOnce(runId, event)
          playFailureSoundOnce(runId, event)
        } else if (event.type === 'error') {
          terminalReceived = true
          committed = event.metadata?.committed !== false
          setRunRetryNoticeFor(targetUser, activeSession, null)
          setRunErrorNoticeFor(targetUser, activeSession, { id: eventId('run-error'), message: `最终错误：${String(event.error?.message || '任务计划执行失败')}` })
          playFailureSoundOnce(runId, event)
        }
        const updated = event.type === 'tool_call_result' ? extractPlanSummary(event.result) : null
        if (updated) setPlanOverrides((current: any) => ({ ...current, [updated.plan_id]: updated }))
        updateChatRunItems(user, activeSession, (current: any) => reduceRunEvent(current, event))
      },
    })
    deltaBatcher.flush()
    if (!terminalReceived) {
      terminalReceived = true
      const interruptionMessage = '任务计划响应流在终态事件到达前结束，未收到最终状态'
      const missingEvent: RunEvent = {
        type: 'error',
        error: { message: interruptionMessage, exception_type: 'MissingTerminalEvent' },
        metadata: { status: 'interrupted', terminal: false },
      }
      setRunRetryNoticeFor(targetUser, activeSession, null)
      setRunErrorNoticeFor(targetUser, activeSession, { id: eventId('run-error'), message: interruptionMessage })
      updateChatRunItems(user, activeSession, (current: any) => [
        ...finalizeCurrentRoundItems(current, {
          message: interruptionMessage,
          exception_type: 'MissingTerminalEvent',
        }),
        { id: eventId('error'), kind: 'error', content: interruptionMessage },
      ])
    }
    await refreshSessions()
    if (!sessionId && isCurrentConversation(targetUser, activeSession)) {
      locallyCommittedSessionRef.current = activeSession
      setSessionId(activeSession)
    }
    if (committed) await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
    await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    refreshOverview()
  } catch (error) {
    deltaBatcher.flush()
    const aborted = (error as Error).name === 'AbortError'
    const missingTerminal = error instanceof ApiError && error.code === 'missing_terminal'
    const message = missingTerminal
      ? '任务计划响应流在终态事件到达前结束，未收到最终状态'
      : error instanceof Error ? error.message : '任务计划执行失败'
    const interruptionMessage = missingTerminal
      ? message
      : '任务计划响应连接已中断，未收到最终状态'
    if (aborted) {
      setRunRetryNoticeFor(targetUser, activeSession, null)
      setRunErrorNoticeFor(targetUser, activeSession, null)
    }
    if (!terminalReceived) {
      updateChatRunItems(user, activeSession, (current: any) => {
        const finalized = finalizeCurrentRoundItems(current, {
          message: aborted
            ? '任务计划响应连接已中断'
            : interruptionMessage,
          exception_type: aborted
            ? 'ClientStreamAborted'
            : missingTerminal ? 'MissingTerminalEvent' : 'ClientStreamInterrupted',
          ...(aborted ? { cancelled: true } : {}),
        })
        return aborted
          ? finalized
          : [...finalized, { id: eventId('error'), kind: 'error', content: interruptionMessage }]
      })
    }
    if (!aborted && !terminalReceived) {
      setRunRetryNoticeFor(targetUser, activeSession, null)
      setRunErrorNoticeFor(targetUser, activeSession, { id: eventId('run-error'), message: interruptionMessage })
    }
    await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
  } finally {
    deltaBatcher.dispose()
    finishChatRun(targetUser, activeSession, committed, runId)
    setChatAbortController(null, targetUser, activeSession, runId)
    setStopping(false, targetUser, activeSession, runId)
    setRunning(false, targetUser, activeSession, runId)
  }
}

const planActions = (plan: PlanSummary) => ({
  onToggleCollapse: () => setCollapsedPlans((current: any) => { const next = new Set(current); if (next.has(plan.plan_id)) next.delete(plan.plan_id); else next.add(plan.plan_id); return next }),
  onReject: () => void commandPlanStatus(plan, 'cancel'),
  onModify: () => navigate(`/tasks?user=${encodeURIComponent(user)}`),
  onApprove: () => void executePlan(plan),
  onPause: () => void commandPlanStatus(plan, 'pause'),
  onRetry: () => void executePlan(plan),
  onRetryStep: (stepId: string) => void retryFailedPlanStep(plan, stepId),
  activationNotice: planMutationNotices[plan.plan_id],
})

  return { commandPlanStatus, planActions }
}
