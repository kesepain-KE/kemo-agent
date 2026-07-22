import { Fragment, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { useInfiniteQuery, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Activity,
  BrainCircuit,
  Check,
  ChevronDown,
  ListChecks,
  Copy,
  Pencil,
  RotateCcw,
  Save,
  Shapes,
  TimerReset,
  Trash2,
  UserRound,
  Zap,
} from 'lucide-react'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { closeSession, commandPlan, compressSession, deleteSession, getExpands, getHistory, getKnowledge, getSense, getTasks, streamChat, submitGuidance, undoLastRound, uploadUserFile } from '../api/client'
import { AgentComposer } from '../components/AgentComposer'
import { CONVERSATION_COMMAND_EVENT, chatRunKey, type ChatItemsUpdater, type ConversationCommandAction, type ShellOutletContext } from '../components/AppShell'
import { MarkdownMessage } from '../components/Chat/MarkdownMessage'
import { ExpandReferenceDrawer } from '../components/ExpandReferenceDrawer'
import { KnowledgeReferenceDrawer } from '../components/KnowledgeReferenceDrawer'
import { formatBytes, formatDateTime, statusLabel } from '../components/ModuleUi'
import { RecentActivityCard, type ScheduledTaskItem, type SenseDataItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import type { ChatItem, CronTaskSummary, ExpandModuleSummary, HistoryResponse, KnowledgeDocumentSummary, PlanSummary, RunEvent, SenseSourceSummary } from '../types/api'
import { copyText } from '../utils/clipboard'

function createSessionId() {
  return `web_${crypto.randomUUID()}`
}

function eventId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`
}

const EMPTY_CHAT_ITEMS: ChatItem[] = []
const PLAN_EXECUTION_PROMPT_PREFIX = '【任务计划连续执行】'
const HISTORY_PAGE_SIZE = 20

export function isNearScrollBottom(
  metrics: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold = 96,
) {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
}

export type ConversationBlock =
  | { id: string; kind: 'user'; item: Extract<ChatItem, { kind: 'message' }> }
  | { id: string; kind: 'assistant'; items: ChatItem[] }

export function groupConversationItems(items: ChatItem[]): ConversationBlock[] {
  const blocks: ConversationBlock[] = []
  let activeAssistant: Extract<ConversationBlock, { kind: 'assistant' }> | null = null
  let currentUserId = 'opening'
  let assistantSequence = 0

  const flushAssistant = () => {
    if (!activeAssistant?.items.length) return
    blocks.push(activeAssistant)
    activeAssistant = null
  }

  for (const item of items) {
    if (item.kind === 'execution_marker') {
      flushAssistant()
      currentUserId = item.id
      continue
    }
    if (item.kind === 'message' && item.role === 'user') {
      flushAssistant()
      currentUserId = item.id
      blocks.push({ id: item.id, kind: 'user', item })
      continue
    }
    if (!activeAssistant) {
      assistantSequence += 1
      activeAssistant = {
        id: `assistant_turn_${currentUserId}_${assistantSequence}`,
        kind: 'assistant',
        items: [],
      }
    }
    activeAssistant.items.push(item)
  }
  flushAssistant()
  return blocks
}

function insertCurrentRoundItem(
  items: ChatItem[],
  item: ChatItem,
  insertBefore: (candidate: ChatItem) => boolean,
) {
  let lastUserIndex = -1
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const candidate = items[index]
    if (candidate.kind === 'message' && candidate.role === 'user') {
      lastUserIndex = index
      break
    }
  }
  const roundStart = lastUserIndex + 1
  const relativeIndex = items.slice(roundStart).findIndex(insertBefore)
  const insertionIndex = relativeIndex < 0 ? items.length : roundStart + relativeIndex
  return [...items.slice(0, insertionIndex), item, ...items.slice(insertionIndex)]
}

export function reduceRunEvent(items: ChatItem[], event: RunEvent): ChatItem[] {
  if (event.type === 'text_delta') {
    const index = [...items].reverse().findIndex((item) => item.kind === 'message' && item.role === 'assistant' && item.streaming)
    if (index >= 0) {
      const actual = items.length - 1 - index
      return items.map((item, position) => position === actual && item.kind === 'message' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return [...items, { id: eventId('assistant'), kind: 'message', role: 'assistant', content: event.content || '', streaming: true }]
  }
  if (event.type === 'reasoning_delta') {
    const index = [...items].reverse().findIndex((item) => item.kind === 'reasoning' && item.streaming)
    if (index >= 0) {
      const actual = items.length - 1 - index
      return items.map((item, position) => position === actual && item.kind === 'reasoning' ? { ...item, content: item.content + (event.content || '') } : item)
    }
    return insertCurrentRoundItem(
      items,
      { id: eventId('reasoning'), kind: 'reasoning', content: event.content || '', streaming: true },
      (candidate) => candidate.kind !== 'reasoning',
    )
  }
  if (event.type === 'tool_call_start') {
    return insertCurrentRoundItem(
      items,
      {
        id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'),
        name: event.tool_name || '未知工具', arguments: event.arguments, status: 'running',
      },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant'
        || candidate.kind === 'usage'
        || candidate.kind === 'error',
    )
  }
  if (event.type === 'tool_call_result') {
    const result = event.result && typeof event.result === 'object' ? event.result as Record<string, unknown> : undefined
    const backendStatus = String(event.metadata?.status || '')
    const failed = Boolean(event.error) || backendStatus === 'failed' || result?.ok === false
    const toolStatus: 'error' | 'success' = failed ? 'error' : 'success'
    const elapsedMs = event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms)
    const found = items.some((item) => item.kind === 'tool' && item.callId === event.tool_call_id)
    const withTool = found ? items.map((item) => item.kind === 'tool' && item.callId === event.tool_call_id
      ? { ...item, name: event.tool_name || item.name, result: event.result, status: toolStatus, elapsedMs }
      : item) : insertCurrentRoundItem(
        items,
        { id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'), name: event.tool_name || '未知工具', result: event.result, status: toolStatus, elapsedMs },
        (candidate) => candidate.kind === 'message' && candidate.role === 'assistant' || candidate.kind === 'usage' || candidate.kind === 'error',
      )
    const plan = extractPlanSummary(event.result)
    if (!plan) return withTool
    if (withTool.some((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id)) {
      return withTool.map((item) => item.kind === 'task_plan' && item.plan.plan_id === plan.plan_id ? { ...item, plan } : item)
    }
    return insertCurrentRoundItem(
      withTool,
      { id: `task_plan_${plan.plan_id}`, kind: 'task_plan', plan },
      (candidate) => candidate.kind === 'message' && candidate.role === 'assistant' || candidate.kind === 'usage' || candidate.kind === 'error',
    )
  }
  if (event.type === 'guidance_applied') {
    const pending = Array.isArray(event.metadata?.guidance)
      ? event.metadata.guidance.map((value) => String(value))
      : []
    return items.map((item) => {
      if (item.kind !== 'guidance' || item.status !== 'queued') return item
      const matched = pending.indexOf(item.content)
      if (matched < 0) return item
      pending.splice(matched, 1)
      return { ...item, status: 'accepted' as const }
    })
  }
  if (event.type === 'error') {
    return [
      ...items.map((item) => {
        if (item.kind === 'message' || item.kind === 'reasoning') return { ...item, streaming: false }
        if (item.kind === 'guidance') {
          return {
            ...item,
            status: item.status === 'queued' ? 'not_applied' as const : item.status === 'accepted' ? 'completed' as const : item.status,
            finalized: true,
          }
        }
        return item
      }),
      { id: eventId('error'), kind: 'error', content: String(event.error?.message || '聊天执行失败') },
    ]
  }
  if (event.type === 'done') {
    let guidanceRemaining = Number(event.metadata?.guidance_count || 0)
    const completed = items.map((item) => {
      if (item.kind === 'message' || item.kind === 'reasoning') return { ...item, streaming: false }
      if (item.kind === 'guidance') {
        if (item.status === 'accepted') {
          if (guidanceRemaining > 0) guidanceRemaining -= 1
          return { ...item, status: 'completed' as const, finalized: true }
        }
        if (item.status === 'queued') {
          if (guidanceRemaining > 0) {
            guidanceRemaining -= 1
            return { ...item, status: 'completed' as const, finalized: true }
          }
          return { ...item, status: 'not_applied' as const, finalized: true }
        }
        return { ...item, finalized: true }
      }
      return item
    })
    return event.usage ? [...completed, {
      id: eventId('usage'), kind: 'usage', usage: event.usage,
      elapsedMs: event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms),
      toolCalls: event.metadata?.tool_calls === undefined ? undefined : Number(event.metadata.tool_calls),
      providerRequestCount: event.usage.provider_request_count === undefined ? undefined : Number(event.usage.provider_request_count),
    }] : completed
  }
  return items
}

function historyToolStatus(status: string): 'running' | 'success' | 'error' {
  if (status === 'running') return 'running'
  if (status === 'error') return 'error'
  return 'success'
}

export function buildHistoryItems(history: HistoryResponse | undefined): ChatItem[] {
  const metrics = new Map((history?.round_metrics || []).map((item) => [item.round, item]))
  const traces = new Map((history?.round_traces || []).map((item) => [item.round, item]))
  const result: ChatItem[] = []
  let round = Math.max(0, Number(history?.pagination?.first_round || 1) - 1)
  let messagePosition = 0

  for (const message of history?.messages ?? []) {
    if (message.role !== 'user' && message.role !== 'assistant') continue
    if (message.role === 'user') {
      round += 1
      messagePosition = 0
      if (message.content.startsWith(PLAN_EXECUTION_PROMPT_PREFIX)) {
        result.push({ id: `history_execution_${round}`, kind: 'execution_marker', planId: message.content.split('\n')[1]?.replace('计划 ID：', '').trim() || '' })
        continue
      }
      result.push({ id: `history_${round}_user`, kind: 'message', role: 'user', content: message.content })
      continue
    }

    messagePosition += 1
    const trace = traces.get(round)
    if (trace?.reasoning) {
      result.push({
        id: `history_reasoning_${round}`,
        kind: 'reasoning',
        content: trace.reasoning,
        streaming: false,
      })
    }
    trace?.tools.forEach((tool, toolIndex) => {
      result.push({ id: `history_tool_${round}_${toolIndex}`, kind: 'tool', callId: tool.call_id || `history-call-${round}-${toolIndex + 1}`, name: tool.name, status: historyToolStatus(tool.status), elapsedMs: tool.elapsed_ms, argumentsText: tool.arguments_text, argumentsTruncated: tool.arguments_truncated, resultText: tool.result_text, resultTruncated: tool.result_truncated })
      if (!tool.result_truncated) {
        try {
          const plan = extractPlanSummary(JSON.parse(tool.result_text))
          if (plan) result.push({ id: `history_task_plan_${plan.plan_id}_${round}`, kind: 'task_plan', plan })
        } catch { /* historical tool output need not be JSON */ }
      }
    })
    result.push({ id: `history_${round}_assistant_${messagePosition}`, kind: 'message', role: 'assistant', content: message.content })

    const selected = metrics.get(round)
    if (selected) {
      selected.guidance.forEach((content, guidanceIndex) => result.push({ id: `history_guidance_${round}_${guidanceIndex}`, kind: 'guidance', content, status: 'completed', finalized: true }))
      result.push({
        id: `history_usage_${round}`, kind: 'usage', usage: selected.usage,
        elapsedMs: selected.elapsed_ms, toolCalls: selected.tool_calls, round,
        providerRequestCount: selected.usage.provider_request_count === undefined
          ? undefined
          : Number(selected.usage.provider_request_count),
      })
    }
  }
  return result
}

export function mergeHistoryPages(pages: HistoryResponse[] | undefined): HistoryResponse | undefined {
  if (!pages?.length) return undefined
  if (pages.length === 1) return pages[0]
  const ordered = [...pages].reverse()
  const earliest = ordered[0]
  const latest = pages[0]
  return {
    ...latest,
    messages: ordered.flatMap((page) => page.messages),
    round_metrics: ordered.flatMap((page) => page.round_metrics),
    round_traces: ordered.flatMap((page) => page.round_traces),
    pagination: {
      limit: latest.pagination?.limit ?? HISTORY_PAGE_SIZE,
      total_rounds: latest.pagination?.total_rounds
        ?? ordered.reduce((total, page) => total + page.messages.filter((message) => message.role === 'user').length, 0),
      first_round: earliest.pagination?.first_round ?? 1,
      last_round: latest.pagination?.last_round
        ?? latest.pagination?.total_rounds
        ?? ordered.reduce((total, page) => total + page.messages.filter((message) => message.role === 'user').length, 0),
      has_more_before: earliest.pagination?.has_more_before ?? false,
      next_before: earliest.pagination?.next_before ?? null,
    },
  }
}

const quickStartCards = [
  { prompt: '查询 kemo-agent 当前感知情况', icon: BrainCircuit, title: '查询感知情况', desc: '查看感知来源、采集数据与当前注入状态', tone: 'sense' },
  { prompt: '查询 kemo-agent 当前拓展情况', icon: Shapes, title: '查询拓展情况', desc: '查看拓展模块、采集能力与注入状态', tone: 'expand' },
  { prompt: '查询 kemo-agent 当前运行状态', icon: Activity, title: '查询运行状态', desc: '汇总核心模块、Provider 与外接服务状态', tone: 'status' },
  { prompt: '为当前用户创建一个定时任务', icon: TimerReset, title: '创建定时任务', desc: '通过对话描述时间、内容与执行目标', tone: 'timer' },
]

function greetingLabel() {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

export function compactPlanAssistantText(content: string, hasPlanBubble: boolean) {
  if (!hasPlanBubble) return content
  const markers = ['以下是计划详情', '以下是计划的详细信息', '新计划已生成', '任务计划已生成', '计划已生成', '计划包含', '计划 ID', '## 任务计划', '📋']
  const cut = markers.map((marker) => content.indexOf(marker)).filter((index) => index >= 0).sort((left, right) => left - right)[0]
  return cut === undefined ? content : '任务计划已创建，请在发送框上方查看并确认。'
}

export function dropLastLiveRound(items: ChatItem[]) {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    const item = items[index]
    if (item.kind === 'message' && item.role === 'user') return items.slice(0, index)
  }
  return items
}

const terminalPlanStatuses = new Set(['completed', 'rejected', 'cancelled'])

export function selectDockedPlan(plans: PlanSummary[]) {
  return [...plans].reverse().find((plan) => !terminalPlanStatuses.has(plan.status))
}

function TaskPlanRecord({ plan, docked, onOpen }: { plan: PlanSummary; docked: boolean; onOpen: () => void }) {
  return (
    <article className="task-plan-record" aria-label={`已创建任务计划：${plan.title}`}>
      <span className="task-plan-record-icon"><ListChecks size={17} /></span>
      <span className="task-plan-record-copy">
        <small>已创建任务计划</small>
        <strong>{plan.title}</strong>
      </span>
      <span className={`task-plan-record-status status-${plan.status}`}>{statusLabel(plan.status)}</span>
      <span className="task-plan-record-progress">{plan.progress.completed}/{plan.progress.total}</span>
      <button type="button" onClick={onOpen}>{docked ? '查看当前计划' : '任务中枢'}</button>
    </article>
  )
}

function cronScheduleLabel(task: CronTaskSummary) {
  if (task.type === 'daily') return `每天 ${task.time || '—'}`
  if (task.type === 'once') return `单次 · ${formatDateTime(task.next_run_at)}`
  if (task.type === 'recurring') {
    const seconds = Number(task.interval_seconds || 0)
    return seconds >= 3600 ? `每 ${Math.round(seconds / 3600)} 小时` : `每 ${Math.max(1, Math.round(seconds / 60))} 分钟`
  }
  return '未配置调度'
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null
}

export function extractPlanSummary(value: unknown): PlanSummary | null {
  let payload = objectValue(value)
  const wrapped = objectValue(payload?.result)
  if (payload?.ok === true && wrapped) payload = wrapped
  const raw = objectValue(payload?.plan) || (payload?.plan_id ? payload : null)
  if (!raw || typeof raw.plan_id !== 'string' || typeof raw.title !== 'string' || !Array.isArray(raw.steps)) return null
  const steps = raw.steps.map((value) => objectValue(value)).filter((step): step is Record<string, unknown> => Boolean(step)).map((step) => ({
    step_id: String(step.step_id || ''), title: String(step.title || ''), description: String(step.description || ''), status: String(step.status || 'pending'),
    depends_on: Array.isArray(step.depends_on) ? step.depends_on.map(String) : [], critical: Boolean(step.critical ?? true), tool_name: String(step.tool_name || ''), started_at: String(step.started_at || ''), finished_at: String(step.finished_at || ''),
  }))
  const completed = steps.filter((step) => step.status === 'completed' || step.status === 'skipped').length
  return { plan_id: raw.plan_id, title: raw.title, description: String(raw.description || ''), status: String(raw.status || 'pending'), auto_accept: Boolean(raw.auto_accept), reminder: String(raw.reminder || ''), source: String(raw.source || ''), session_id: String(raw.session_id || ''), current_step: String(raw.current_step || ''), revision: Number(raw.revision || 1), created_at: String(raw.created_at || ''), updated_at: String(raw.updated_at || ''), progress: { completed, total: steps.length, percent: steps.length ? Math.round(completed * 100 / steps.length) : 0 }, steps }
}

function senseIconFor(source: SenseSourceSummary): SenseDataItem['icon'] {
  const text = `${source.name} ${source.display_name}`.toLowerCase()
  if (text.includes('温度') || text.includes('temperature')) return 'temperature'
  if (text.includes('湿度') || text.includes('humidity')) return 'humidity'
  if (text.includes('天气') || text.includes('weather')) return 'weather'
  return 'radio'
}

type GuidanceItem = Extract<ChatItem, { kind: 'guidance' }>

function GuidanceMessage({ item, placement }: { item: GuidanceItem; placement: 'current' | 'completed' }) {
  const title = item.status === 'queued'
    ? '正在引导'
    : item.status === 'not_applied'
      ? '本轮未生效'
      : item.status === 'error'
        ? '引导失败'
        : '引导成功'
  const detail = item.status === 'queued'
    ? '等待智能体到达下一个安全边界'
    : item.status === 'accepted'
      ? '智能体已读取该引导并继续运行'
      : item.status === 'completed'
        ? '本轮运行已采用此引导'
        : item.status === 'not_applied'
          ? '本轮结束前未进入下一次模型请求'
          : '引导未能提交到当前运行'
  return <article className={`guidance-message guidance-${placement} ${item.status}`} data-guidance-status={item.status}>
    <span className="guidance-title"><i aria-hidden="true" />{title}</span>
    <strong>{item.content}</strong>
    <small>{detail}</small>
  </article>
}

export function buildScheduledTaskItems(tasks: CronTaskSummary[]): ScheduledTaskItem[] {
  const supportedStatuses = new Set<ScheduledTaskItem['status']>(['enabled', 'running', 'completed', 'paused', 'failed', 'cancelled', 'disabled'])
  return [...tasks]
    .filter((task) => task.user_defined)
    .sort((left, right) => (left.next_run_at || left.created_at).localeCompare(right.next_run_at || right.created_at))
    .map((task) => ({
      id: task.task_id,
      title: task.title,
      schedule: cronScheduleLabel(task),
      nextRun: formatDateTime(task.next_run_at),
      status: supportedStatuses.has(task.status as ScheduledTaskItem['status']) ? task.status as ScheduledTaskItem['status'] : 'disabled',
      icon: task.type === 'daily' ? 'calendar' : task.type === 'recurring' ? 'alarm' : 'clipboard',
    }))
}

export function buildSenseDataItems(sources: SenseSourceSummary[]): SenseDataItem[] {
  return [...sources]
    .filter((source) => source.active_for_main_agent && source.status === 'active' && source.injected_items > 0)
    .sort((left, right) => (right.updated_at || 0) - (left.updated_at || 0))
    .map((source) => ({
      id: source.id,
      name: source.display_name || source.name,
      value: source.value_preview,
      updateInterval: source.update_interval,
      updatedAt: formatDateTime(source.recent_update || source.updated_at),
      injected: true,
      icon: senseIconFor(source),
    }))
}

export function ChatPage() {
  const { user, sessionId, clientId, chatRunning: running, setChatRunning: setRunning, chatRunId: activeRunId, setChatRunId: setActiveRunId, setChatAbortController, abortChatRun, chatRuns, beginChatRun, updateChatRunItems, finishChatRun, clearChatRun, setSessionId, detachSession, notifySessionDeleted, sessions, refreshSessions, createNewSession, overview, refreshOverview, openCommandPanel } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [editingSource, setEditingSource] = useState<{ id: string; content: string } | null>(null)
  const [editedSources, setEditedSources] = useState<Set<string>>(() => new Set())
  const [copiedItem, setCopiedItem] = useState('')
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false)
  const [knowledgeDrawerOpen, setKnowledgeDrawerOpen] = useState(false)
  const [expandDrawerOpen, setExpandDrawerOpen] = useState(false)
  const [conversationBusy, setConversationBusy] = useState<'save' | 'clear' | 'compress' | 'retry' | 'edit' | ''>('')
  const [conversationFeedback, setConversationFeedback] = useState<{ tone: 'success' | 'error'; text: string } | null>(null)
  const [activeTaskOpen, setActiveTaskOpen] = useState(false)
  const [collapsedPlans, setCollapsedPlans] = useState<Set<string>>(() => new Set())
  const [planOverrides, setPlanOverrides] = useState<Record<string, PlanSummary>>({})
  const [uploadFeedback, setUploadFeedback] = useState<{ tone: 'pending' | 'success' | 'error'; text: string } | null>(null)
  const [showFollowOutput, setShowFollowOutput] = useState(false)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const composerPlanDockRef = useRef<HTMLDivElement | null>(null)
  const followOutputRef = useRef(true)
  const loadingEarlierRef = useRef(false)
  const prependSnapshotRef = useRef<{ scrollHeight: number; scrollTop: number } | null>(null)
  const locallyCommittedSessionRef = useRef('')
  const lastAttemptSessionRef = useRef(sessionId)
  const conversationKeyRef = useRef(`${user}\u0000${sessionId}`)
  const liveSessionId = sessionId || lastAttemptSessionRef.current
  const liveRun = liveSessionId ? chatRuns[chatRunKey(user, liveSessionId)] : undefined
  const liveItems = liveRun?.items ?? EMPTY_CHAT_ITEMS
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
  const tasksQuery = useQuery({
    queryKey: ['tasks', user],
    queryFn: () => getTasks(user),
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
    enabled: Boolean(user && expandDrawerOpen),
  })
  const expandModules = useMemo(
    () => expandsQuery.data?.expands.flatMap((group) => group.items) ?? [],
    [expandsQuery.data],
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
    lastAttemptSessionRef.current = sessionId
    followOutputRef.current = true
    loadingEarlierRef.current = false
    prependSnapshotRef.current = null
    setShowFollowOutput(false)
    setEditingSource(null)
    setEditedSources(new Set())
    setCopiedItem('')
    setActiveRunId('')
    setUploadFeedback(null)
    setKnowledgeDrawerOpen(false)
    setExpandDrawerOpen(false)
    setConversationBusy('')
    setConversationFeedback(null)
    setPlanOverrides({})
    abortChatRun()
  }, [abortChatRun, user, sessionId])

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

  const send = async (
    promptOverride?: string,
    options: {
      sessionId?: string
      content?: Array<Record<string, unknown>>
      historyUserMessages?: number
    } = {},
  ) => {
    const prompt = (promptOverride ?? draft).trim()
    if (!prompt || !user || running) return
    const activeSession = options.sessionId || sessionId || createSessionId()
    lastAttemptSessionRef.current = activeSession
    const runId = `run_${crypto.randomUUID().replaceAll('-', '')}`
    const historyUserMessages = options.historyUserMessages
      ?? (activeSession === sessionId ? persistedUserMessages : 0)
    beginChatRun(user, activeSession, runId, historyUserMessages)
    setDraft('')
    setRunning(true)
    setActiveRunId(runId)
    setConversationMenuOpen(false)
    followOutputRef.current = true
    setShowFollowOutput(false)
    if (editingSource) setEditedSources((current) => new Set(current).add(editingSource.id))
    updateChatRunItems(user, activeSession, (current) => [...current, {
      id: eventId('user'), kind: 'message', role: 'user', content: prompt,
      edited: Boolean(editingSource), originalContent: editingSource?.content,
    }])
    setEditingSource(null)
    const controller = new AbortController()
    setChatAbortController(controller)
    let committed = false
    try {
      await streamChat({
        user,
        sessionId: activeSession,
        clientId,
        prompt: options.content?.length ? '' : prompt,
        content: options.content,
        runId,
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === 'done') committed = true
          updateChatRunItems(user, activeSession, (current) => reduceRunEvent(current, event))
        },
      })
      await refreshSessions()
      if (!sessionId) {
        locallyCommittedSessionRef.current = activeSession
        setSessionId(activeSession)
      }
      if (committed) await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
      refreshOverview()
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        updateChatRunItems(user, activeSession, (current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '聊天失败' }])
      }
    } finally {
      finishChatRun(user, activeSession, committed)
      setChatAbortController(null)
      setActiveRunId('')
      setRunning(false)
    }
  }

  const uploadFile = async (file: File) => {
    if (!user) return
    setUploadFeedback({ tone: 'pending', text: `正在上传 ${file.name}…` })
    try {
      const result = await uploadUserFile(user, 'file_upload', file.name, file)
      setUploadFeedback({ tone: 'success', text: `已上传 ${result.path || file.name} · ${formatBytes(result.size ?? file.size)}` })
      await queryClient.invalidateQueries({ queryKey: ['user-files', user, 'file_upload'] })
    } catch (error) {
      setUploadFeedback({ tone: 'error', text: error instanceof Error ? `上传失败：${error.message}` : '上传失败' })
    }
  }
  const newConversation = async () => {
    abortChatRun()
    await createNewSession()
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
      const memoryText = memory.status === 'completed'
        ? (memory.candidates > 0
            ? `已同步提取 ${memory.candidates} 条记忆候选。`
            : '记忆提取已完成，本次没有需要保存的新记忆。')
        : memory.status === 'skipped'
          ? (memory.reason === 'already_processed'
              ? '记忆已是最新状态。'
              : memory.reason === 'memory_extraction_disabled'
                ? '记忆提取已按配置关闭。'
                : '当前没有可提取的完整对话轮次。')
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
      setLiveItems((current) => dropLastLiveRound(current))
      if (sessionId) {
        await queryClient.invalidateQueries({ queryKey: ['history', user, sessionId] })
      }
      const editableContent = undo.prompt || content
      setDraft(editableContent)
      setEditingSource({ id, content })
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

  const copyMessage = async (id: string, content: string) => {
    await copyText(content)
    setCopiedItem(id)
    window.setTimeout(() => setCopiedItem((current) => current === id ? '' : current), 1200)
  }

  const sendGuidance = async () => {
    const guidance = draft.trim()
    if (!guidance || !user || !running || !activeRunId) return
    setDraft('')
    const id = eventId('guidance')
    setLiveItems((current) => [...current, { id, kind: 'guidance', content: guidance, status: 'queued' }])
    try {
      await submitGuidance(user, activeRunId, guidance)
    } catch (error) {
      setLiveItems((current) => current.map((item) => item.kind === 'guidance' && item.id === id ? { ...item, status: 'error' } : item))
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '运行中引导提交失败' }])
    }
  }

  const activePlan = overview?.active_plan
  const lastUserMessage = [...items].reverse().find((item) => item.kind === 'message' && item.role === 'user')
  const latestRunningGuidance = running
    ? [...visibleLiveItems].reverse().find((item): item is GuidanceItem => item.kind === 'guidance' && !item.finalized)
    : undefined
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
  const commandPlanStatus = async (plan: PlanSummary, action: 'pause' | 'cancel') => {
    try {
      const response = await commandPlan(user, plan.plan_id, action)
      const updated = extractPlanSummary(response.plan)
      if (updated) setPlanOverrides((current) => ({ ...current, [updated.plan_id]: updated }))
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    } catch (error) {
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划更新失败' }])
    }
  }

  const executePlan = async (plan: PlanSummary) => {
    if (!user || running) return
    const activeSession = plan.session_id || sessionId || createSessionId()
    lastAttemptSessionRef.current = activeSession
    const runId = `run_${crypto.randomUUID().replaceAll('-', '')}`
    const historyUserMessages = activeSession === sessionId ? persistedUserMessages : 0
    beginChatRun(user, activeSession, runId, historyUserMessages)
    updateChatRunItems(user, activeSession, (current) => [
      ...current,
      { id: eventId('plan_execution'), kind: 'execution_marker', planId: plan.plan_id },
    ])
    setPlanOverrides((current) => ({
      ...current,
      [plan.plan_id]: { ...plan, status: 'running', revision: plan.revision + 1 },
    }))
    setRunning(true)
    setActiveRunId(runId)
    followOutputRef.current = true
    setShowFollowOutput(false)
    const controller = new AbortController()
    setChatAbortController(controller)
    let committed = false
    let refreshedRunningPlan = false
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
          if (!refreshedRunningPlan) {
            refreshedRunningPlan = true
            void queryClient.invalidateQueries({ queryKey: ['tasks', user] })
          }
          if (event.type === 'done') committed = true
          const updated = event.type === 'tool_call_result' ? extractPlanSummary(event.result) : null
          if (updated) setPlanOverrides((current) => ({ ...current, [updated.plan_id]: updated }))
          updateChatRunItems(user, activeSession, (current) => reduceRunEvent(current, event))
        },
      })
      await refreshSessions()
      if (!sessionId) {
        locallyCommittedSessionRef.current = activeSession
        setSessionId(activeSession)
      }
      if (committed) await queryClient.invalidateQueries({ queryKey: ['history', user, activeSession] })
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
      refreshOverview()
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        updateChatRunItems(user, activeSession, (current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划执行失败' }])
      }
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    } finally {
      finishChatRun(user, activeSession, committed)
      setChatAbortController(null)
      setActiveRunId('')
      setRunning(false)
    }
  }

  const planActions = (plan: PlanSummary) => ({
    onToggleCollapse: () => setCollapsedPlans((current) => { const next = new Set(current); if (next.has(plan.plan_id)) next.delete(plan.plan_id); else next.add(plan.plan_id); return next }),
    onReject: () => void commandPlanStatus(plan, 'cancel'),
    onModify: () => navigate(`/tasks?user=${encodeURIComponent(user)}`),
    onApprove: () => void executePlan(plan),
    onPause: () => void commandPlanStatus(plan, 'pause'),
    onRetry: () => void executePlan(plan),
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
  const stopCurrentRun = () => {
    if (dockedPlan?.status === 'running') void commandPlanStatus(dockedPlan, 'pause')
    else abortChatRun()
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
  const currentRound = Math.max(1, userRoundCount, Number(overview?.context.rounds || 0))
  const roundLimit = Math.max(1, Number(overview?.context.round_limit || 30))
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
  const referenceExpand = (module: ExpandModuleSummary) => {
    const referenceId = `${module.scope}:${module.name}`
    const reference = `[拓展引用 ${referenceId}] ${module.display_name || module.name}`
    setDraft((current) => {
      if (current.includes(`[拓展引用 ${referenceId}]`)) return current
      const existing = current.trimEnd()
      return existing ? `${existing}\n${reference}` : reference
    })
    setExpandDrawerOpen(false)
  }
  const conversationBlocks = groupConversationItems(items)

  return (
    <div className={`view chat-view active${items.length === 0 ? ' welcome-mode' : ''}${conversationMenuOpen ? ' conversation-menu-open' : ''}`}>
      <div className="chat-scroll" ref={scrollRef} onScroll={handleChatScroll}>
        {items.length === 0 && (!sessionId || !historyQuery.isLoading) && (
          <section className="welcome">
            <div className="welcome-top">
              <article className="greeting-card">
                <div className="hero-logo"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent logo" /></div>
                <div className="greeting-copy">
                  <h1>{greetingLabel()}，{user || '用户'}</h1>
                  <p>当前用户的配置、历史、知识、任务与技能运行态已载入。今天需要处理什么？</p>
                  <span className="role-line">● 当前用户 · users/{user || '—'}</span>
                </div>
              </article>
              <article className="snapshot-card">
                <div className="snapshot-item"><strong>{overview?.counts.sessions ?? '—'} 个</strong><span>Web 会话</span></div>
                <div className="snapshot-item"><strong className="ok">{overview?.counts.knowledge_documents ?? '—'} 项</strong><span>文件知识</span></div>
                <div className="snapshot-item"><strong>{overview?.counts.enabled_tools ?? '—'} 个</strong><span>可用工具</span></div>
                <div className="snapshot-item"><strong>{overview?.counts.active_tasks ?? '—'} 个</strong><span>活动任务</span></div>
              </article>
            </div>
            {activePlan && !dockedPlan && <article className={`active-task-card ${activeTaskOpen ? 'open' : ''}`}>
              <div className="active-task-main">
                <span className="active-task-play"><ListChecks size={17} /></span>
                <span className="active-task-copy"><small>{statusLabel(activePlan.status)} · 当前用户 {user}</small><strong>{activePlan.title}</strong><span>{activePlan.description}</span></span>
                <span className="active-task-progress"><b>{activePlan.progress.percent}%</b><span className="progress-line"><i style={{ width: `${activePlan.progress.percent}%` }} /></span></span>
                <button className="task-inline-btn" onClick={() => setActiveTaskOpen((value) => !value)}>{activeTaskOpen ? '收起步骤' : '展开步骤'} <ChevronDown size={13} /></button>
                <button className="task-inline-btn primary" onClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}>任务中枢</button>
              </div>
              <div className="active-task-detail">{activePlan.steps.slice(0, 6).map((step, index) => <div className={`active-task-step ${step.status}`} key={step.step_id}><i>{step.status === 'completed' ? '✓' : index + 1}</i><span><strong>{step.title}</strong><small>{statusLabel(step.status)} · {step.description}</small></span></div>)}</div>
            </article>}
            <div className="quick-start">
              {quickStartCards.map(({ prompt, icon: Icon, title, desc, tone }) => (
                <button key={prompt} className={`quick-card quick-card-${tone}`} onClick={() => setDraft(prompt)}>
                  <span className="quick-icon"><Icon size={17} /></span>
                  <strong>{title}</strong>
                  <span>{desc}</span>
                </button>
              ))}
            </div>
            <RecentActivityCard
              className="welcome-recent-status"
              scheduledTasks={recentTasks}
              senseData={recentSenseData}
              onViewAllTasks={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}
              onViewAllSenseData={() => navigate(`/sense?user=${encodeURIComponent(user)}`)}
              onTaskClick={() => navigate(`/tasks?user=${encodeURIComponent(user)}`)}
              onSenseDataClick={() => navigate(`/sense?user=${encodeURIComponent(user)}`)}
            />
          </section>
        )}
        {historyQuery.isLoading && <div className="center-state">正在加载历史…</div>}
        {historyQuery.isError && sessionId && liveItems.length === 0 && <div className="center-state error">该会话尚无已提交历史，可以直接发送第一条消息。</div>}
        <div className={`messages ${items.length ? 'show' : ''}`}>
          {historyQuery.isFetchingNextPage ? <div className="history-page-status">正在加载更早对话…</div> : null}
          {historyQuery.hasNextPage && !historyQuery.isFetchingNextPage ? (
            <button className="history-page-button" type="button" onClick={() => { void loadEarlierHistory() }}>加载更早对话</button>
          ) : null}
          {!historyQuery.hasNextPage
            && (historyData?.pagination?.total_rounds ?? 0) > HISTORY_PAGE_SIZE
            ? <div className="history-page-status complete">已到达对话开头</div>
            : null}
          {items.length ? <div className="conversation-divider"><span>当前对话</span></div> : null}
          {conversationBlocks.map((block) => {
            if (block.kind === 'user') {
              const item = block.item
              return (
                <Fragment key={block.id}>
                  <article className="message user">
                    <div className="msg-avatar"><UserRound size={17} /></div>
                    <div className="message-body">
                      <div className="bubble"><MarkdownMessage content={item.content} streaming={Boolean(item.streaming)} /></div>
                      <div className="message-actions">
                        {item.edited ? <span className="edited-label">编辑后重发</span> : null}
                        {editedSources.has(item.id) ? <span className="edited-label">已用于重发</span> : null}
                        {!running && lastUserMessage?.id === item.id ? <button onClick={() => void editAndResend(item.id, item.content)} disabled={Boolean(conversationBusy)} aria-label="编辑后重发"><Pencil size={12} />{conversationBusy === 'edit' ? '正在撤销…' : '编辑重发'}</button> : null}
                        <button onClick={() => void copyMessage(item.id, item.content)} disabled={!item.content} aria-label="复制消息">{copiedItem === item.id ? <Check size={12} /> : <Copy size={12} />}{copiedItem === item.id ? '已复制' : '复制'}</button>
                      </div>
                    </div>
                  </article>
                </Fragment>
              )
            }

            const assistantMessages = block.items.filter(
              (item): item is Extract<ChatItem, { kind: 'message' }> => item.kind === 'message' && item.role === 'assistant',
            )
            const usageItems = block.items.filter(
              (item): item is Extract<ChatItem, { kind: 'usage' }> => item.kind === 'usage',
            )
            const assistantText = assistantMessages.map((item) => item.content).filter(Boolean).join('\n\n')
            const assistantCopyId = assistantMessages.at(-1)?.id || block.id
            const hasPlanBubble = block.items.some((item) => item.kind === 'task_plan')
            const finalizedGuidance = block.items.filter(
              (item): item is GuidanceItem => item.kind === 'guidance' && Boolean(item.finalized),
            )
            return (
              <article key={block.id} className="assistant-turn">
                <div className="msg-avatar assistant-turn-avatar"><img src="/kemo-agent.jpg" width={571} height={568} alt="kemo-agent" /></div>
                <div className="assistant-turn-content">
                  {block.items.map((item) => {
                    if (item.kind === 'reasoning') return <ReasoningTrace key={item.id} item={item} />
                    if (item.kind === 'execution_marker') return null
                    if (item.kind === 'tool') return <ToolCallCard key={item.id} item={item} />
                    if (item.kind === 'usage') return null
                    if (item.kind === 'task_plan') {
                      const plan = resolvePlan(item.plan)
                      return <TaskPlanRecord key={item.id} plan={plan} docked={plan.plan_id === dockedPlan?.plan_id} onOpen={() => revealPlan(plan)} />
                    }
                    if (item.kind === 'guidance') return null
                    if (item.kind === 'error') return <div key={item.id} className="chat-error">{item.content}</div>
                    if (item.role !== 'assistant') return null
                    return (
                      <div key={item.id} className="assistant-response">
                        <div className="bubble">
                          <MarkdownMessage
                            content={compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)}
                            streaming={Boolean(item.streaming)}
                          />
                        </div>
                      </div>
                    )
                  })}
                  {(usageItems.length > 0 || assistantMessages.length > 0) && (
                    <div className="assistant-turn-footer">
                      <div className="assistant-turn-usage">{usageItems.map((item) => <UsageCard key={item.id} item={item} />)}</div>
                      {assistantMessages.length > 0 && (
                        <button className="assistant-turn-copy" onClick={() => void copyMessage(assistantCopyId, assistantText)} disabled={!assistantText} aria-label="复制智能体回复">
                          {copiedItem === assistantCopyId ? <Check size={13} /> : <Copy size={13} />}{copiedItem === assistantCopyId ? '已复制' : '复制'}
                        </button>
                      )}
                    </div>
                  )}
                  {finalizedGuidance.length > 0 && <div className="assistant-guidance-list">{finalizedGuidance.map((item) => <GuidanceMessage key={item.id} item={item} placement="completed" />)}</div>}
                </div>
              </article>
            )
          })}
          {items.length > 0 && persistedSessionPlans.map((plan) => <TaskPlanRecord key={`persisted_${plan.plan_id}`} plan={plan} docked={plan.plan_id === dockedPlan?.plan_id} onOpen={() => revealPlan(plan)} />)}
        </div>
        {showFollowOutput && items.length > 0 ? <button className="chat-follow-output" type="button" onClick={resumeFollowingOutput}><ChevronDown size={15} />继续跟随最新回复</button> : null}
      </div>
      <div className="composer-zone">
        {dockedPlan ? (
          <div className="composer-plan-dock" ref={composerPlanDockRef}>
            <TaskPlanBubble
              {...taskPlanFromSummary(dockedPlan)}
              collapsed={collapsedPlans.has(dockedPlan.plan_id)}
              {...planActions(dockedPlan)}
            />
          </div>
        ) : null}
        {latestRunningGuidance ? <div className="composer-guidance-preview" aria-live="polite"><GuidanceMessage item={latestRunningGuidance} placement="current" /></div> : null}
        <AgentComposer
          value={draft}
          placeholder={user ? running ? '输入运行中引导；将在下一个 Provider/工具边界生效…' : '给 kemo-agent 发送消息…' : '请先选择用户'}
          currentRound={currentRound}
          roundLimit={roundLimit}
          running={running}
          disabled={!user}
          conversationMenuOpen={conversationMenuOpen}
          uploadFeedback={uploadFeedback ? <div className={`upload-feedback ${uploadFeedback.tone}`} role="status">{uploadFeedback.text}<button type="button" onClick={() => setUploadFeedback(null)} aria-label="关闭上传提示">×</button></div> : null}
          notice={editingSource ? <div className="edit-resend-banner"><span>最新一轮已撤销；修改内容后发送将创建新的最新一轮。</span><button onClick={() => { setEditingSource(null); setDraft('') }}>取消编辑</button></div> : null}
          conversationMenu={conversationMenuOpen ? (
            <div className="conversation-menu show" role="menu">
              <div className="conversation-menu-head">对话操作</div>
              <button className="conversation-action" role="menuitem" disabled={running || Boolean(conversationBusy)} onClick={() => { void saveAndNewConversation() }}>
                <span className="conversation-action-icon"><Save size={16} /></span>
                  <span className="conversation-action-copy"><strong>保存此对话，创建新对话</strong><span>{conversationBusy === 'save' ? '正在保存归档并切换…' : '保留当前归档，记忆转入后台提取'}</span></span>
              </button>
              <button className="conversation-action danger" role="menuitem" disabled={running || Boolean(conversationBusy)} onClick={() => { void clearConversation() }}>
                <span className="conversation-action-icon"><Trash2 size={16} /></span>
                <span className="conversation-action-copy"><strong>清空此对话</strong><span>{conversationBusy === 'clear' ? '正在删除当前归档…' : '删除当前归档并创建新对话'}</span></span>
              </button>
              <button className="conversation-action compress" role="menuitem" disabled={running || Boolean(conversationBusy) || !sessionId || !hasCommitted} onClick={() => { void compressCurrentConversation() }}>
                <span className="conversation-action-icon"><Zap size={16} /></span>
                <span className="conversation-action-copy"><strong>手动进行一次上下文压缩</strong><span>{conversationBusy === 'compress' ? '正在压缩并提取记忆…' : '整理当前上下文并同步提取待处理记忆'}</span></span>
              </button>
              <button className="conversation-action" role="menuitem" disabled={running || Boolean(conversationBusy) || !lastUserMessage} onClick={() => void regenerateLastResponse()}>
                <span className="conversation-action-icon"><RotateCcw size={16} /></span>
                <span className="conversation-action-copy"><strong>重新发送一次消息</strong><span>撤销上一轮后重放原消息，不增加对话轮数</span></span>
              </button>
              {conversationFeedback ? <div className={`conversation-menu-status ${conversationFeedback.tone}`} role="status">{conversationFeedback.text}</div> : null}
              <div className="conversation-menu-foot">再次打开网页会恢复上次活跃对话；点击“保存并创建新对话”才会关闭并切换会话。</div>
            </div>
          ) : null}
          onChange={setDraft}
          onUploadFile={uploadFile}
          onOpenKnowledge={() => {
            setExpandDrawerOpen(false)
            setKnowledgeDrawerOpen(true)
          }}
          onOpenExpand={() => {
            setKnowledgeDrawerOpen(false)
            setExpandDrawerOpen(true)
          }}
          onOpenCommands={openCommandPanel}
          onToggleConversationMenu={() => setConversationMenuOpen((value) => !value)}
          onSubmit={() => { if (running) void sendGuidance(); else void send() }}
          onStop={stopCurrentRun}
        />
      </div>
      <KnowledgeReferenceDrawer
        open={knowledgeDrawerOpen}
        documents={knowledgeQuery.data?.documents ?? []}
        loading={knowledgeQuery.isLoading || knowledgeQuery.isFetching}
        error={knowledgeQuery.isError}
        onClose={() => setKnowledgeDrawerOpen(false)}
        onReference={referenceKnowledge}
      />
      <ExpandReferenceDrawer
        open={expandDrawerOpen}
        modules={expandModules}
        loading={expandsQuery.isLoading || expandsQuery.isFetching}
        error={expandsQuery.isError}
        onClose={() => setExpandDrawerOpen(false)}
        onReference={referenceExpand}
      />
    </div>
  )
}
