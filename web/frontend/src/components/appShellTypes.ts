import type { OverviewResponse, ChatItem, SessionsResponse } from '../types/api'
import type { PendingUploadedFile } from '../store/chatDrafts'

export interface ShellOutletContext {
  user: string
  userAvatarUrl?: string
  sessionId: string
  clientId: string
  chatRunning: boolean
  chatStopping: boolean
  setChatRunning: (running: boolean, user?: string, sessionId?: string, runId?: string) => void
  setChatStopping: (stopping: boolean, user?: string, sessionId?: string, runId?: string, logicalRunId?: string) => void
  chatRunId: string
  chatRunSessionId: string
  chatRunToken: string
  setChatRunId: (runId: string, user?: string, sessionId?: string, expectedRunId?: string) => void
  setChatAbortController: (controller: AbortController | null, user?: string, sessionId?: string, runId?: string) => void
  abortChatRun: (user?: string, sessionId?: string, runId?: string, logicalRunId?: string) => void
  chatRuns: Record<string, ChatRunSnapshot>
  beginChatRun: (user: string, sessionId: string, runId: string, historyUserMessages: number) => void
  updateChatRunItems: (user: string, sessionId: string, updater: ChatItemsUpdater) => void
  queueNextTurnMessage: (user: string, sessionId: string, message: PendingNextTurnMessage) => void
  setNextTurnMessageStatus: (user: string, sessionId: string, messageId: string, status: PendingNextTurnMessage['status'], error?: string) => void
  removeNextTurnMessage: (user: string, sessionId: string, messageId: string) => void
  reorderNextTurnMessages: (user: string, sessionId: string, messageId: string, targetId: string) => void
  finishChatRun: (user: string, sessionId: string, committed: boolean, expectedRunId?: string) => void
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
  uploadedFiles?: PendingUploadedFile[]
  historyUserMessages: number
  status: 'queued' | 'sending' | 'guiding' | 'error'
  error?: string
}

export interface ChatRunSnapshot {
  items: ChatItem[]
  phase: 'streaming' | 'awaiting_history' | 'idle'
  runId: string
  historyUserMessages: number
  nextTurnQueue: PendingNextTurnMessage[]
}
