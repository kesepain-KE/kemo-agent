import { useCallback, useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { followRun, getCurrentRun, type PersistedRunStatus } from '../api/streamClient'
import { reduceRecoveredRunEvent, restoreRunSnapshot } from '../pages/runRecovery'

import { reorderFollowUps } from './followUpQueue'
import { chatRunKey } from './appShellConfig'
import type { ChatItemsUpdater, ChatRunSnapshot, PendingNextTurnMessage } from './appShellTypes'

interface ChatRunControl {
  user: string
  sessionId: string
  runId: string
  logicalRunId: string
  running: boolean
  stopping: boolean
  controller: AbortController | null
}

export function useChatRunRegistry(user: string, sessionId: string) {
  const queryClient = useQueryClient()
  const [, setChatControlRevision] = useState(0)
  const chatRunControlsRef = useRef(new Map<string, ChatRunControl>())
  const chatDraftRunKeysRef = useRef(new Map<string, string>())
  const observedFinishedRunsRef = useRef(new Map<string, string>())
  const discoverRef = useRef<(() => void) | null>(null)
  const refreshChatRun = useCallback(() => discoverRef.current?.(), [])
  const [persistedRun, setPersistedRun] = useState<{ key: string; status: PersistedRunStatus }>()
  const [chatRuns, setChatRuns] = useState<Record<string, ChatRunSnapshot>>({})
  const userRef = useRef(user)
  const sessionIdRef = useRef(sessionId)
  userRef.current = user
  sessionIdRef.current = sessionId
  const bumpChatControlRevision = () => setChatControlRevision((value) => value + 1)
  const getCurrentChatRunControl = () => {
    const currentUser = userRef.current
    if (!currentUser) return undefined
    const key = sessionIdRef.current
      ? chatRunKey(currentUser, sessionIdRef.current)
      : chatDraftRunKeysRef.current.get(currentUser) || ''
    return key ? chatRunControlsRef.current.get(key) : undefined
  }
  const currentChatRunControl = getCurrentChatRunControl()
  const chatRunning = Boolean(currentChatRunControl?.running)
  const chatStopping = Boolean(currentChatRunControl?.stopping)
  const chatRunId = currentChatRunControl?.runId || ''
  const chatRunSessionId = currentChatRunControl?.sessionId || ''
  const chatRunToken = currentChatRunControl?.logicalRunId || ''
  const persistedRunStatus = !chatRunning && persistedRun?.key === chatRunKey(user, sessionId) ? persistedRun.status : undefined

  const beginChatRun = useCallback((runUser: string, runSessionId: string, runId: string, historyUserMessages: number) => {
    const key = chatRunKey(runUser, runSessionId)
    const current = chatRunControlsRef.current.get(key)
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
    const logicalRunId = current?.running ? current.logicalRunId : runId
    chatRunControlsRef.current.set(key, {
      user: runUser, sessionId: runSessionId, runId, logicalRunId,
      running: true, stopping: current?.running ? current.stopping : false,
      controller: current?.running ? current.controller : null,
    })
    if (!sessionIdRef.current && userRef.current === runUser) chatDraftRunKeysRef.current.set(runUser, key)
    bumpChatControlRevision()
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

  const finishChatRun = useCallback((runUser: string, runSessionId: string, committed: boolean, expectedRunId = '') => {
    const key = chatRunKey(runUser, runSessionId)
    if (committed && expectedRunId) {
      const seen = observedFinishedRunsRef.current
      seen.delete(key)
      seen.set(key, expectedRunId)
      if (seen.size > 64) seen.delete(seen.keys().next().value!)
    }
    setChatRuns((current) => {
      const existing = current[key]
      if (!existing) return current
      // A late terminal callback from an older Run must not seal the
      // snapshot that a continuation or a newer send has already claimed.
      if (expectedRunId && existing.runId !== expectedRunId) return current
      return { ...current, [key]: { ...existing, historyBefore: undefined,
        historyUpdatedAfter: Date.now(), connectionNotice: '', phase: committed ? 'awaiting_history' : 'idle' } }
    })
  }, [])

  const reorderNextTurnMessages = useCallback((runUser: string, runSessionId: string, messageId: string, targetId: string) => {
    const key = chatRunKey(runUser, runSessionId)
    setChatRuns((current) => {
      const existing = current[key]
      if (!existing) return current
      const nextTurnQueue = reorderFollowUps(existing.nextTurnQueue, messageId, targetId)
      return nextTurnQueue === existing.nextTurnQueue ? current : { ...current, [key]: { ...existing, nextTurnQueue } }
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

  const restoreChatRun = useCallback((runUser: string, runSessionId: string, value: ChatRunSnapshot) => {
    const key = chatRunKey(runUser, runSessionId)
    const control = chatRunControlsRef.current.get(key)
    if (control && control.runId !== value.runId) return
    setChatRuns(current => ({
      ...current, [key]: { ...value, nextTurnQueue: current[key]?.nextTurnQueue ?? [] },
    }))
  }, [])

  useEffect(() => {
    if (!user || !sessionId) return
    const controller = new AbortController()
    const key = chatRunKey(user, sessionId)
    let timer: ReturnType<typeof setTimeout>
    let ownedRunId = ''
    let discovering = false
    let lastStatus: PersistedRunStatus | undefined
    const notice = (message: string) => setChatRuns(current => current[key] ? {
      ...current, [key]: { ...current[key], connectionNotice: message },
    } : current)
    const discover = async () => {
      if (discovering || controller.signal.aborted) return
      clearTimeout(timer)
      discovering = true
      try {
        if (chatRunControlsRef.current.get(key)?.running) return
        const { run: snapshot, status } = await getCurrentRun(user, sessionId, controller.signal)
        if (controller.signal.aborted || chatRunControlsRef.current.get(key)?.running) return
        const nextStatus = snapshot ? undefined : status
        setPersistedRun(current => nextStatus
          ? current?.key === key && current.status === nextStatus ? current : { key, status: nextStatus }
          : current?.key === key ? undefined : current)
        if (lastStatus && lastStatus !== nextStatus) {
          void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
        }
        lastStatus = nextStatus
        if (!snapshot) return
        if (snapshot.finished && observedFinishedRunsRef.current.get(key) === snapshot.run_id) return
        ownedRunId = snapshot.run_id
        chatRunControlsRef.current.set(key, {
          user, sessionId, runId: ownedRunId, logicalRunId: ownedRunId,
          running: true, stopping: snapshot.stopping, controller,
        })
        bumpChatControlRevision()
        await followRun({
          user, sessionId, runId: ownedRunId, signal: controller.signal,
          onReset: value => restoreRunSnapshot(queryClient, value, restoreChatRun, controller.signal),
          onConnectionChange: connected => notice(connected ? '' : '连接已断开，正在恢复后台运行进度…'),
          onEvent: event => updateChatRunItems(user, sessionId, current => reduceRecoveredRunEvent(current, event)),
        }, snapshot)
        finishChatRun(user, sessionId, true, ownedRunId)
        await queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
        void queryClient.invalidateQueries({ queryKey: ['overview', user, sessionId] })
        void queryClient.invalidateQueries({ queryKey: ['sessions', user] })
        void queryClient.invalidateQueries({ queryKey: ['runtime-welcome-tokens', user] })
      } catch (error) {
        if (!controller.signal.aborted && ownedRunId) {
          finishChatRun(user, sessionId, false, ownedRunId)
          notice(error instanceof Error ? error.message : '运行进度恢复失败，请重新连接。')
          void queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
        }
      } finally {
        const control = chatRunControlsRef.current.get(key)
        if (ownedRunId && control?.controller === controller) {
          chatRunControlsRef.current.delete(key)
          bumpChatControlRevision()
        }
        ownedRunId = ''
        discovering = false
        if (!controller.signal.aborted) timer = setTimeout(discover, 5000)
      }
    }
    const refresh = () => { void discover() }
    discoverRef.current = refresh
    refresh()
    return () => {
      if (discoverRef.current === refresh) discoverRef.current = null
      clearTimeout(timer)
      controller.abort()
      const runId = ownedRunId
      if (runId) setChatRuns(current => {
        const existing = current[key]
        if (!existing?.recovered || existing.runId !== runId) return current
        return { ...current, [key]: { ...existing, items: [], phase: 'idle', recovered: false,
          historyBefore: undefined, connectionNotice: '' } }
      })
    }
  }, [user, sessionId, queryClient, restoreChatRun, updateChatRunItems, finishChatRun])

  const resolveRunScope = (runUser?: string, runSessionId?: string, runId = '') => ({
    user: runUser ?? userRef.current,
    sessionId: runSessionId ?? sessionIdRef.current,
    runId,
  })

  const setChatRunning = (running: boolean, runUser?: string, runSessionId?: string, runId = '') => {
    const scope = resolveRunScope(runUser, runSessionId, runId)
    if (!scope.user || !scope.sessionId) return
    const key = chatRunKey(scope.user, scope.sessionId)
    const current = chatRunControlsRef.current.get(key)
    if (!running) {
      // Compare-and-set by Run ID.  Do not let a late stop/finally callback
      // clear the control belonging to a continuation or a newer Run.
      if (!current || (scope.runId && current.runId !== scope.runId)) return
      chatRunControlsRef.current.delete(key)
      if (chatDraftRunKeysRef.current.get(scope.user) === key) {
        chatDraftRunKeysRef.current.delete(scope.user)
      }
      bumpChatControlRevision()
      return
    }
    const sameRun = Boolean(current && (!scope.runId || current.runId === scope.runId))
    chatRunControlsRef.current.set(key, {
      user: scope.user,
      sessionId: scope.sessionId,
      runId: scope.runId || current?.runId || '',
      logicalRunId: current?.logicalRunId || scope.runId,
      running: true,
      stopping: sameRun ? current?.stopping ?? false : false,
      controller: sameRun ? current?.controller || null : null,
    })
    if (!sessionIdRef.current && userRef.current === scope.user) {
      chatDraftRunKeysRef.current.set(scope.user, key)
    }
    bumpChatControlRevision()
  }

  const setChatRunId = (runId: string, runUser?: string, runSessionId?: string, expectedRunId = '') => {
    const scope = resolveRunScope(runUser, runSessionId, runId)
    if (!scope.user || !scope.sessionId) return
    const key = chatRunKey(scope.user, scope.sessionId)
    const current = chatRunControlsRef.current.get(key)
    if (!runId) {
      if (!current || (expectedRunId && current.runId !== expectedRunId)) return
      chatRunControlsRef.current.set(key, { ...current, runId: '' })
      bumpChatControlRevision()
      return
    }
    if (expectedRunId && (!current || current.runId !== expectedRunId)) return
    chatRunControlsRef.current.set(key, {
      user: scope.user,
      sessionId: scope.sessionId,
      runId,
      logicalRunId: current?.logicalRunId || runId,
      running: current?.running ?? true,
      stopping: current?.stopping ?? false,
      controller: current?.controller || null,
    })
    bumpChatControlRevision()
  }

  const setChatAbortController = (
    controller: AbortController | null,
    runUser?: string,
    runSessionId?: string,
    runId = '',
  ) => {
    const scope = resolveRunScope(runUser, runSessionId, runId)
    if (!scope.user || !scope.sessionId) return
    const key = chatRunKey(scope.user, scope.sessionId)
    const current = chatRunControlsRef.current.get(key)
    if (controller) {
      chatRunControlsRef.current.set(key, {
        user: scope.user,
        sessionId: scope.sessionId,
        runId: runId || current?.runId || '',
        logicalRunId: current?.logicalRunId || runId,
        running: current?.running ?? true,
        stopping: current?.stopping ?? false,
        controller,
      })
      bumpChatControlRevision()
      return
    }
    if (!current || (runId && current.runId !== runId)) return
    chatRunControlsRef.current.set(key, { ...current, controller: null })
    bumpChatControlRevision()
  }

  const setChatStopping = (stopping: boolean, runUser?: string, runSessionId?: string, runId = '', logicalRunId = '') => {
    const scope = resolveRunScope(runUser, runSessionId, runId)
    if (!scope.user || !scope.sessionId) return
    const key = chatRunKey(scope.user, scope.sessionId)
    const current = chatRunControlsRef.current.get(key)
    // Stopping is part of the Run control rather than page-local state.  A
    // delayed pause/cancel response may only update the Run it targeted.
    if (!current || (scope.runId && current.runId !== scope.runId && current.logicalRunId !== logicalRunId)) return
    if (current.stopping === stopping) return
    chatRunControlsRef.current.set(key, { ...current, stopping })
    bumpChatControlRevision()
  }

  const abortChatRun = (runUser?: string, runSessionId?: string, runId?: string, logicalRunId = '') => {
    const current = runUser === undefined && runSessionId === undefined && runId === undefined
      ? getCurrentChatRunControl()
      : (() => {
          const scope = resolveRunScope(runUser, runSessionId, runId || '')
          if (!scope.user || !scope.sessionId) return undefined
          return chatRunControlsRef.current.get(chatRunKey(scope.user, scope.sessionId))
        })()
    if (!current || (runId && current.runId !== runId && current.logicalRunId !== logicalRunId)) return
    current.controller?.abort()
  }

  return {
    userRef, sessionIdRef, chatRuns, chatRunning, chatStopping, chatRunId, chatRunSessionId, chatRunToken,
    refreshChatRun, persistedRunStatus,
    beginChatRun, updateChatRunItems, queueNextTurnMessage, setNextTurnMessageStatus,
    removeNextTurnMessage, finishChatRun, reorderNextTurnMessages, clearChatRun, restoreChatRun,
    setChatRunning, setChatRunId, setChatAbortController, setChatStopping, abortChatRun,
  }
}
