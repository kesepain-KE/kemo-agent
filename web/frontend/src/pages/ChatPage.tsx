import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  CheckCircle2,
  Check,
  ChevronDown,
  FileText,
  ListChecks,
  Plus,
  Copy,
  Pencil,
  TimerReset,
  UserRound,
  Zap,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useNavigate, useOutletContext, useSearchParams } from 'react-router-dom'
import { getHistory, getSense, getTasks, streamChat, submitGuidance, updatePlan, uploadUserFile } from '../api/client'
import { AgentComposer } from '../components/AgentComposer'
import type { ShellOutletContext } from '../components/AppShell'
import { formatBytes, formatDateTime, statusLabel } from '../components/ModuleUi'
import { RecentActivityCard, type ScheduledTaskItem, type SenseDataItem } from '../components/RecentActivityCard'
import { ReasoningTrace, ToolCallCard, UsageCard } from '../components/RunEventCards'
import { TaskPlanBubble, taskPlanFromSummary } from '../components/TaskPlanBubble'
import type { ChatItem, CronTaskSummary, HistoryResponse, PlanSummary, RunEvent, SenseSourceSummary } from '../types/api'
import { copyText } from '../utils/clipboard'

function createSessionId() {
  return `web_${crypto.randomUUID()}`
}

function eventId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`
}

export function isNearScrollBottom(
  metrics: Pick<HTMLElement, 'scrollHeight' | 'scrollTop' | 'clientHeight'>,
  threshold = 96,
) {
  return metrics.scrollHeight - metrics.scrollTop - metrics.clientHeight <= threshold
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
  if (event.type === 'error') {
    return [
      ...items.map((item) => item.kind === 'message' || item.kind === 'reasoning' ? { ...item, streaming: false } : item),
      { id: eventId('error'), kind: 'error', content: String(event.error?.message || '聊天执行失败') },
    ]
  }
  if (event.type === 'done') {
    let guidanceRemaining = Number(event.metadata?.guidance_count || 0)
    const completed = items.map((item) => {
      if (item.kind === 'message' || item.kind === 'reasoning') return { ...item, streaming: false }
      if (item.kind === 'guidance' && item.status === 'queued' && guidanceRemaining > 0) {
        guidanceRemaining -= 1
        return { ...item, status: 'accepted' as const }
      }
      return item
    })
    return event.usage ? [...completed, {
      id: eventId('usage'), kind: 'usage', usage: event.usage,
      elapsedMs: event.metadata?.elapsed_ms === undefined ? undefined : Number(event.metadata.elapsed_ms),
      toolCalls: event.metadata?.tool_calls === undefined ? undefined : Number(event.metadata.tool_calls),
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
  let round = 0

  for (const [index, message] of (history?.messages ?? []).entries()) {
    if (message.role !== 'user' && message.role !== 'assistant') continue
    if (message.role === 'user') {
      result.push({ id: `history_${index}`, kind: 'message', role: 'user', content: message.content })
      continue
    }

    round += 1
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
    result.push({ id: `history_${index}`, kind: 'message', role: 'assistant', content: message.content })

    const selected = metrics.get(round)
    if (selected) {
      selected.guidance.forEach((content, guidanceIndex) => result.push({ id: `history_guidance_${round}_${guidanceIndex}`, kind: 'guidance', content, status: 'accepted' }))
      result.push({
        id: `history_usage_${round}`, kind: 'usage', usage: selected.usage,
        elapsedMs: selected.elapsed_ms, toolCalls: selected.tool_calls, round,
      })
    }
  }
  return result
}

const quickStartCards = [
  { prompt: '帮我整理今天的任务并生成优先级计划', icon: CheckCircle2, title: '整理今日任务', desc: '读取任务与上下文，生成执行顺序' },
  { prompt: '查询知识库中与当前问题相关的资料', icon: BookOpen_, title: '查询知识库', desc: '使用当前用户和全局文件索引检索' },
  { prompt: '检查 kemo-agent 当前运行状态', icon: Zap, title: '检查运行状态', desc: '汇总核心模块、Provider 与外接服务状态' },
  { prompt: '为当前用户创建一个定时任务', icon: TimerReset, title: '创建定时任务', desc: '通过对话描述时间、内容与执行目标' },
]

function greetingLabel() {
  const hour = new Date().getHours()
  if (hour < 6) return '夜深了'
  if (hour < 12) return '上午好'
  if (hour < 18) return '下午好'
  return '晚上好'
}

function BookOpen_() { return <svg width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z" /><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5v-16Z" /></svg> }

function compactPlanAssistantText(content: string, hasPlanBubble: boolean) {
  if (!hasPlanBubble) return content
  const markers = ['以下是计划详情', '以下是计划的详细信息', '## 任务计划', '📋']
  const cut = markers.map((marker) => content.indexOf(marker)).filter((index) => index >= 0).sort((left, right) => left - right)[0]
  return cut === undefined ? content : content.slice(0, cut).trim() || '已创建任务计划，请在下方确认。'
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

export function buildScheduledTaskItems(tasks: CronTaskSummary[]): ScheduledTaskItem[] {
  return [...tasks]
    .filter((task) => task.user_defined)
    .sort((left, right) => (left.next_run_at || left.created_at).localeCompare(right.next_run_at || right.created_at))
    .map((task) => ({
      id: task.task_id,
      title: task.title,
      schedule: cronScheduleLabel(task),
      nextRun: formatDateTime(task.next_run_at),
      enabled: task.status === 'enabled',
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
  const { user, sessionId, setSessionId, sessions, refreshSessions, overview, refreshOverview, openCommandPanel } = useOutletContext<ShellOutletContext>()
  const navigate = useNavigate()
  const [searchParams, setSearchParams] = useSearchParams()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [liveItems, setLiveItems] = useState<ChatItem[]>([])
  const [running, setRunning] = useState(false)
  const [editingSource, setEditingSource] = useState<{ id: string; content: string } | null>(null)
  const [editedSources, setEditedSources] = useState<Set<string>>(() => new Set())
  const [copiedItem, setCopiedItem] = useState('')
  const [activeRunId, setActiveRunId] = useState('')
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false)
  const [activeTaskOpen, setActiveTaskOpen] = useState(false)
  const [collapsedPlans, setCollapsedPlans] = useState<Set<string>>(() => new Set())
  const [planOverrides, setPlanOverrides] = useState<Record<string, PlanSummary>>({})
  const [toolPause, setToolPause] = useState<{ limit: number; executed: number } | null>(null)
  const [uploadFeedback, setUploadFeedback] = useState<{ tone: 'pending' | 'success' | 'error'; text: string } | null>(null)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const followOutputRef = useRef(true)
  const locallyCommittedSessionRef = useRef('')
  const historyHandoffSessionRef = useRef('')
  const hasCommitted = useMemo(() => {
    if (!sessionId) return false
    return sessionId === locallyCommittedSessionRef.current
      || sessions.some((session) => session.session_id === sessionId)
  }, [sessionId, sessions])
  const historyQuery = useQuery({
    queryKey: ['history', user, sessionId],
    queryFn: () => getHistory(user, sessionId),
    enabled: Boolean(user && sessionId && hasCommitted),
    retry: false,
  })
  const tasksQuery = useQuery({
    queryKey: ['tasks', user],
    queryFn: () => getTasks(user),
    enabled: Boolean(user),
  })
  const senseQuery = useQuery({
    queryKey: ['sense', user],
    queryFn: () => getSense(user),
    enabled: Boolean(user),
  })

  const historyItems = useMemo<ChatItem[]>(() => buildHistoryItems(historyQuery.data), [historyQuery.data])
  const handoffReady = historyHandoffSessionRef.current === sessionId && Boolean(historyQuery.data)
  const items = [...historyItems, ...(handoffReady ? [] : liveItems)]
  const persistedToolPause = useMemo(() => {
    const metric = historyQuery.data?.round_metrics.at(-1)
    if (!metric?.tool_pause || metric.tool_pause.reason !== 'max_per_round') return null
    return {
      limit: Number(metric.tool_pause.limit || 0),
      executed: Number(metric.tool_pause.executed || 0),
    }
  }, [historyQuery.data])
  const visibleToolPause = running
    ? null
    : toolPause ?? (liveItems.length === 0 ? persistedToolPause : null)

  useEffect(() => {
    if (historyHandoffSessionRef.current === sessionId) return
    historyHandoffSessionRef.current = ''
    followOutputRef.current = true
    setLiveItems([])
    setEditingSource(null)
    setEditedSources(new Set())
    setCopiedItem('')
    setActiveRunId('')
    setToolPause(null)
    setUploadFeedback(null)
    setPlanOverrides({})
    abortRef.current?.abort()
    setRunning(false)
  }, [user, sessionId])

  useEffect(() => {
    if (historyHandoffSessionRef.current !== sessionId || !historyQuery.data) return
    historyHandoffSessionRef.current = ''
    setLiveItems([])
  }, [historyQuery.data, sessionId])

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
    if (!element || !followOutputRef.current) return
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: running ? 'auto' : 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [items.length, liveItems, running])

  const send = async (promptOverride?: string) => {
    const prompt = (promptOverride ?? draft).trim()
    if (!prompt || !user || running) return
    const activeSession = sessionId || createSessionId()
    const runId = `run_${crypto.randomUUID().replaceAll('-', '')}`
    setDraft('')
    setToolPause(null)
    setRunning(true)
    setActiveRunId(runId)
    setConversationMenuOpen(false)
    followOutputRef.current = true
    if (editingSource) setEditedSources((current) => new Set(current).add(editingSource.id))
    setLiveItems((current) => [...current, {
      id: eventId('user'), kind: 'message', role: 'user', content: prompt,
      edited: Boolean(editingSource), originalContent: editingSource?.content,
    }])
    setEditingSource(null)
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat({
        user,
        sessionId: activeSession,
        prompt,
        runId,
        signal: controller.signal,
        onEvent: (event) => {
          setLiveItems((current) => reduceRunEvent(current, event))
          if (event.type === 'done' && event.metadata?.awaiting_tool_confirmation) {
            const pause = event.metadata.tool_pause as Record<string, unknown> | undefined
            setToolPause({
              limit: Number(pause?.limit || 0),
              executed: Number(pause?.executed || 0),
            })
          }
        },
      })
      await refreshSessions()
      if (!sessionId) {
        locallyCommittedSessionRef.current = activeSession
        historyHandoffSessionRef.current = activeSession
        setSessionId(activeSession)
      }
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
      refreshOverview()
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '聊天失败' }])
      }
    } finally {
      abortRef.current = null
      setActiveRunId('')
      setRunning(false)
    }
  }

  const stop = () => abortRef.current?.abort()
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
  const newConversation = () => {
    abortRef.current?.abort()
    setSessionId('')
    setConversationMenuOpen(false)
  }

  const editAndResend = (id: string, content: string) => {
    if (running) return
    setDraft(content)
    setEditingSource({ id, content })
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
  const recentTasks = useMemo(() => buildScheduledTaskItems(tasksQuery.data?.cron_tasks || []), [tasksQuery.data])
  const recentSenseData = useMemo(() => buildSenseDataItems(senseQuery.data?.sources || []), [senseQuery.data])
  const changePlanStatus = async (plan: PlanSummary, status: 'approved' | 'paused' | 'cancelled') => {
    try {
      const response = await updatePlan(user, plan.plan_id, { revision: plan.revision, status })
      const updated = extractPlanSummary(response.plan)
      if (updated) setPlanOverrides((current) => ({ ...current, [updated.plan_id]: updated }))
      await queryClient.invalidateQueries({ queryKey: ['tasks', user] })
    } catch (error) {
      setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '任务计划更新失败' }])
    }
  }
  const planActions = (plan: PlanSummary) => ({
    onToggleCollapse: () => setCollapsedPlans((current) => { const next = new Set(current); if (next.has(plan.plan_id)) next.delete(plan.plan_id); else next.add(plan.plan_id); return next }),
    onReject: () => void changePlanStatus(plan, 'cancelled'),
    onModify: () => navigate(`/tasks?user=${encodeURIComponent(user)}`),
    onApprove: () => void changePlanStatus(plan, 'approved'),
    onStop: () => void changePlanStatus(plan, 'paused'),
    onRetry: () => void changePlanStatus(plan, 'approved'),
  })
  const renderedPlanIds = new Set(items.filter((item): item is Extract<ChatItem, { kind: 'task_plan' }> => item.kind === 'task_plan').map((item) => item.plan.plan_id))
  const persistedSessionPlans = (tasksQuery.data?.plans || []).filter((plan) => plan.session_id === sessionId && !renderedPlanIds.has(plan.plan_id))
  const userRoundCount = items.filter((item) => item.kind === 'message' && item.role === 'user').length
  const currentRound = Math.max(1, userRoundCount, Number(overview?.context.rounds || 0))
  const roundLimit = Math.max(1, Number(overview?.context.round_limit || 30))
  const handleChatScroll = () => {
    const element = scrollRef.current
    if (element) followOutputRef.current = isNearScrollBottom(element)
  }

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
            {activePlan && <article className={`active-task-card ${activeTaskOpen ? 'open' : ''}`}>
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
              {quickStartCards.map(({ prompt, icon: Icon, title, desc }) => (
                <button key={prompt} className="quick-card" onClick={() => setDraft(prompt)}>
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
          {items.length ? <div className="conversation-divider"><span>当前对话</span></div> : null}
          {items.map((item, itemIndex) => {
            if (item.kind === 'reasoning') return <ReasoningTrace key={item.id} item={item} />
            if (item.kind === 'tool') return <ToolCallCard key={item.id} item={item} />
            if (item.kind === 'usage') return <UsageCard key={item.id} item={item} />
            if (item.kind === 'task_plan') {
              const plan = planOverrides[item.plan.plan_id] || item.plan
              return <TaskPlanBubble key={item.id} {...taskPlanFromSummary(plan)} collapsed={collapsedPlans.has(plan.plan_id)} {...planActions(plan)} />
            }
            if (item.kind === 'guidance') return <article key={item.id} className={`guidance-message ${item.status}`}><span>运行中引导</span><strong>{item.content}</strong><small>{item.status === 'queued' ? '已排队，将在下一个安全边界生效' : item.status === 'accepted' ? '已在该轮运行中采用' : '提交失败'}</small></article>
            if (item.kind === 'error') return <div key={item.id} className="chat-error">{item.content}</div>
            const hasPlanBubble = items.slice(0, itemIndex).some((candidate) => candidate.kind === 'task_plan')
            return (
              <article key={item.id} className={`message ${item.role === 'assistant' ? 'ai' : 'user'}`}>
                <div className="msg-avatar">{item.role === 'assistant' ? <img src="/kemo-agent.jpg" width={571} height={568} alt="" /> : <UserRound size={17} />}</div>
                <div className="message-body">
                  <div className="bubble"><ReactMarkdown remarkPlugins={[remarkGfm]}>{compactPlanAssistantText(item.content || (item.streaming ? '…' : ''), hasPlanBubble)}</ReactMarkdown></div>
                  <div className="message-actions">
                    {item.edited ? <span className="edited-label">编辑后重发</span> : null}
                    {editedSources.has(item.id) ? <span className="edited-label">已用于重发</span> : null}
                    {item.role === 'user' && !running ? <button onClick={() => editAndResend(item.id, item.content)} aria-label="编辑后重发"><Pencil size={12} />编辑重发</button> : null}
                    <button onClick={() => void copyMessage(item.id, item.content)} disabled={!item.content} aria-label="复制消息">{copiedItem === item.id ? <Check size={12} /> : <Copy size={12} />}{copiedItem === item.id ? '已复制' : '复制'}</button>
                  </div>
                </div>
              </article>
            )
          })}
          {items.length > 0 && persistedSessionPlans.map((rawPlan) => { const plan = planOverrides[rawPlan.plan_id] || rawPlan; return <TaskPlanBubble key={`persisted_${plan.plan_id}`} {...taskPlanFromSummary(plan)} collapsed={collapsedPlans.has(plan.plan_id)} {...planActions(plan)} /> })}
        </div>
      </div>
      <div className="composer-zone">
        <AgentComposer
          value={draft}
          placeholder={user ? running ? '输入运行中引导；将在下一个 Provider/工具边界生效…' : visibleToolPause ? '已暂停工具调用；可点击继续或输入新的指令…' : '给 kemo-agent 发送消息…' : '请先选择用户'}
          currentRound={currentRound}
          roundLimit={roundLimit}
          running={running}
          disabled={!user}
          conversationMenuOpen={conversationMenuOpen}
          uploadFeedback={uploadFeedback ? <div className={`upload-feedback ${uploadFeedback.tone}`} role="status">{uploadFeedback.text}<button type="button" onClick={() => setUploadFeedback(null)} aria-label="关闭上传提示">×</button></div> : null}
          notice={visibleToolPause ? <div className="edit-resend-banner"><span>本轮已执行 {visibleToolPause.executed}/{visibleToolPause.limit} 次工具调用，中间结果已保存。确认后可继续。</span><button type="button" onClick={() => { void send('继续执行上轮未完成的任务。') }}>继续</button></div> : editingSource ? <div className="edit-resend-banner"><span>正在编辑旧消息并作为新消息追加发送；原历史不会被改写。</span><button onClick={() => { setEditingSource(null); setDraft('') }}>取消</button></div> : null}
          conversationMenu={conversationMenuOpen ? (
            <div className="conversation-menu show" role="menu">
              <div className="conversation-menu-head">对话操作</div>
              <button className="conversation-action" role="menuitem" onClick={newConversation}>
                <span className="conversation-action-icon"><Plus size={16} /></span>
                <span className="conversation-action-copy"><strong>创建新对话</strong><span>开启独立的上下文窗口</span></span>
              </button>
              <button className="conversation-action" role="menuitem" disabled>
                <span className="conversation-action-icon"><FileText size={16} /></span>
                <span className="conversation-action-copy"><strong>自动保存已启用</strong><span>每轮成功后写入当前用户历史</span></span>
              </button>
              <button className="conversation-action compress" role="menuitem" disabled>
                <span className="conversation-action-icon"><Zap size={16} /></span>
                <span className="conversation-action-copy"><strong>手动压缩待接入</strong><span>自动压缩仍由上下文生命周期处理</span></span>
              </button>
              <div className="conversation-menu-foot">成功响应自动保存 · 手动压缩 API 尚未开放</div>
            </div>
          ) : null}
          onChange={setDraft}
          onUploadFile={uploadFile}
          onOpenKnowledge={() => navigate(`/knowledge?user=${encodeURIComponent(user)}`)}
          onOpenSkills={() => navigate(`/skills?user=${encodeURIComponent(user)}`)}
          onOpenCommands={openCommandPanel}
          onToggleConversationMenu={() => setConversationMenuOpen((value) => !value)}
          onSubmit={() => { if (running) void sendGuidance(); else void send() }}
          onStop={stop}
        />
      </div>
    </div>
  )
}
