import type { QueryClient } from '@tanstack/react-query'
import { getHistory } from '../api/client'
import type { RunSnapshot } from '../api/streamClient'
import type { ChatItem, RunEvent } from '../types/api'
import type { ShellOutletContext } from '../components/appShellTypes'
import { chatHistoryKey } from './chatHistory'
import { isProvisionalRunError, reduceRunEvent, resetCurrentRoundItemsForRetry, resetProviderResponseForRetry } from './chatState'

export function reduceRecoveredRunEvent(current: ChatItem[], event: RunEvent): ChatItem[] {
  if (isProvisionalRunError(event)) return current
  if (event.type === 'retrying') {
    const failed = Math.max(1, Number(event.metadata?.failed_attempt || 1))
    const next = Math.max(failed + 1, Number(event.metadata?.next_attempt || failed + 1))
    return event.metadata?.phase === 'provider'
      ? resetProviderResponseForRetry(current, event)
      : resetCurrentRoundItemsForRetry(current, failed, next, Math.max(1, Number(event.metadata?.run_attempt || next)))
  }
  return reduceRunEvent(current, event)
}

export async function restoreRunSnapshot(
  client: QueryClient,
  snapshot: RunSnapshot,
  restore: ShellOutletContext['restoreChatRun'],
  signal?: AbortSignal,
) {
  const before = snapshot.finished ? undefined : snapshot.history_seq + 1
  const history = await getHistory(snapshot.user, snapshot.session_id, { limit: 20, before }, signal)
  signal?.throwIfAborted()
  client.setQueryData(chatHistoryKey(snapshot.user, snapshot.session_id, before), {
    pages: [history], pageParams: [undefined],
  })
  const items = snapshot.finished ? [] : snapshot.events.reduce<ChatItem[]>(
    (current, event) => reduceRecoveredRunEvent(current, event as RunEvent), [],
  )
  restore(snapshot.user, snapshot.session_id, {
    runId: snapshot.run_id, items, phase: 'streaming', nextTurnQueue: [],
    historyUserMessages: Math.max(0, (history.pagination?.integration_count || 0) - 1),
    recovered: true, historyBefore: before,
    connectionNotice: snapshot.snapshot_incomplete
      ? '部分实时内容超过恢复缓冲，完整结果将在本轮保存后显示。' : '',
  })
}
