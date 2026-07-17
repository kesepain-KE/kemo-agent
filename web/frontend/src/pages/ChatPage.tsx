import { useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  CheckCircle2,
  Clock,
  FileText,
  Image,
  BrainCircuit,
  LayoutGrid,
  Plus,
  Send,
  Square,
  UserRound,
  Wrench,
  Zap,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { useOutletContext } from 'react-router-dom'
import { getHistory, streamChat } from '../api/client'
import type { ShellOutletContext } from '../components/AppShell'
import { ReasoningTrace, ToolCallCard } from '../components/RunEventCards'
import type { ChatItem, RunEvent } from '../types/api'

function createSessionId() {
  return `web_${crypto.randomUUID()}`
}

function eventId(prefix: string) {
  return `${prefix}_${crypto.randomUUID()}`
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
    return [...items, { id: eventId('reasoning'), kind: 'reasoning', content: event.content || '', streaming: true }]
  }
  if (event.type === 'tool_call_start') {
    return [...items, {
      id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'),
      name: event.tool_name || '未知工具', arguments: event.arguments, status: 'running',
    }]
  }
  if (event.type === 'tool_call_result') {
    const found = items.some((item) => item.kind === 'tool' && item.callId === event.tool_call_id)
    if (!found) return [...items, {
      id: eventId('tool'), kind: 'tool', callId: event.tool_call_id || eventId('call'), name: event.tool_name || '未知工具',
      result: event.result, status: event.error ? 'error' : 'success',
    }]
    return items.map((item) => item.kind === 'tool' && item.callId === event.tool_call_id
      ? { ...item, name: event.tool_name || item.name, result: event.result, status: event.error ? 'error' : 'success' }
      : item)
  }
  if (event.type === 'error') {
    return [
      ...items.map((item) => item.kind === 'message' || item.kind === 'reasoning' ? { ...item, streaming: false } : item),
      { id: eventId('error'), kind: 'error', content: String(event.error?.message || '聊天执行失败') },
    ]
  }
  if (event.type === 'done') {
    return items.map((item) => item.kind === 'message' || item.kind === 'reasoning' ? { ...item, streaming: false } : item)
  }
  return items
}

const quickStartCards = [
  { prompt: '检查 kemo-agent 当前运行状态', icon: Zap, title: '检查运行状态', desc: '汇总核心模块、兼容 API 与外接服务状态' },
  { prompt: '总结当前 Web 架构与已接接口', icon: FileText, title: '总结架构', desc: '梳理前端、后端与 Run 核心的接口边界' },
  { prompt: '查询知识库中关于 kemo-agent 的设计资料', icon: BookOpen_, title: '查询知识库', desc: '默认使用文件索引进行本地检索' },
  { prompt: '帮我规划今天的任务并生成优先级计划', icon: CheckCircle2, title: '规划今日任务', desc: '读取任务与上下文，生成执行顺序' },
]

function BookOpen_() { return <svg width="17" height="17" fill="none" stroke="currentColor" strokeWidth="1.8" viewBox="0 0 24 24"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3H11v16H6.5A2.5 2.5 0 0 0 4 21.5v-16Z" /><path d="M20 5.5A2.5 2.5 0 0 0 17.5 3H13v16h4.5a2.5 2.5 0 0 1 2.5 2.5v-16Z" /></svg> }

const toolDockButtons = [
  { icon: FileText, label: '添加文件' },
  { icon: Image, label: '图像' },
  { icon: BookOpen_, label: '知识库' },
  { icon: BrainCircuit, label: '全局感知' },
  { icon: LayoutGrid, label: '选择技能' },
]

export function ChatPage() {
  const { user, sessionId, setSessionId } = useOutletContext<ShellOutletContext>()
  const queryClient = useQueryClient()
  const [draft, setDraft] = useState('')
  const [liveItems, setLiveItems] = useState<ChatItem[]>([])
  const [running, setRunning] = useState(false)
  const [usage, setUsage] = useState<Record<string, unknown> | undefined>()
  const [conversationMenuOpen, setConversationMenuOpen] = useState(false)
  const abortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)
  const historyQuery = useQuery({
    queryKey: ['history', user, sessionId],
    queryFn: () => getHistory(user, sessionId),
    enabled: Boolean(user && sessionId),
    retry: false,
  })

  const historyItems = useMemo<ChatItem[]>(() => (historyQuery.data?.messages ?? [])
    .filter((message) => message.role === 'user' || message.role === 'assistant')
    .map((message, index) => ({
      id: `history_${index}`, kind: 'message', role: message.role as 'user' | 'assistant', content: message.content,
    })), [historyQuery.data])
  const items = [...historyItems, ...liveItems]

  useEffect(() => {
    setLiveItems([])
    setUsage(undefined)
    abortRef.current?.abort()
    setRunning(false)
  }, [user, sessionId])

  useEffect(() => {
    const element = scrollRef.current
    if (!element) return
    if (typeof element.scrollTo === 'function') element.scrollTo({ top: element.scrollHeight, behavior: 'smooth' })
    else element.scrollTop = element.scrollHeight
  }, [items.length, liveItems])

  const send = async () => {
    const prompt = draft.trim()
    if (!prompt || !user || running) return
    const activeSession = sessionId || createSessionId()
    setDraft('')
    setRunning(true)
    setConversationMenuOpen(false)
    setLiveItems((current) => [...current, { id: eventId('user'), kind: 'message', role: 'user', content: prompt }])
    const controller = new AbortController()
    abortRef.current = controller
    try {
      await streamChat({
        user,
        sessionId: activeSession,
        prompt,
        signal: controller.signal,
        onEvent: (event) => {
          if (event.type === 'usage') setUsage(event.usage)
          if (event.type === 'done' && event.usage) setUsage(event.usage)
          setLiveItems((current) => reduceRunEvent(current, event))
        },
      })
      if (!sessionId) setSessionId(activeSession)
      await queryClient.invalidateQueries({ queryKey: ['sessions', user] })
    } catch (error) {
      if ((error as Error).name !== 'AbortError') {
        setLiveItems((current) => [...current, { id: eventId('error'), kind: 'error', content: error instanceof Error ? error.message : '聊天失败' }])
      }
    } finally {
      abortRef.current = null
      setRunning(false)
    }
  }

  const stop = () => abortRef.current?.abort()
  const newConversation = () => {
    abortRef.current?.abort()
    setSessionId(createSessionId())
    setConversationMenuOpen(false)
  }

  const usageText = usage
    ? `${String(usage.prompt_tokens ?? '?')} + ${String(usage.completion_tokens ?? '?')} = ${String(usage.total_tokens ?? '?')}`
    : null

  return (
    <div className="view chat-view active">
      <div className="chat-scroll" ref={scrollRef}>
        {items.length === 0 && (!sessionId || !historyQuery.isLoading) && (
          <section className="welcome">
            <div className="welcome-top">
              <article className="greeting-card">
                <div className="hero-logo"><img src="/kemo-agent.jpg" alt="kemo-agent logo" /></div>
                <div className="greeting-copy">
                  <h1>你好，{user || '用户'}</h1>
                  <p>Web 已接入真实用户、会话、历史与流式聊天。今天需要处理什么？</p>
                  <span className="role-line">● 当前用户 · users/{user || '—'}</span>
                </div>
              </article>
              <article className="snapshot-card">
                <div className="snapshot-item"><strong className="ok">已接通</strong><span>POST SSE</span></div>
                <div className="snapshot-item"><strong>7 类</strong><span>RunEvent</span></div>
                <div className="snapshot-item"><strong>只读</strong><span>历史恢复</span></div>
                <div className="snapshot-item"><strong>独立</strong><span>Web 会话</span></div>
              </article>
            </div>
            <div className="quick-start">
              {quickStartCards.map(({ prompt, icon: Icon, title, desc }) => (
                <button key={prompt} className="quick-card" onClick={() => setDraft(prompt)}>
                  <span className="quick-icon"><Icon size={17} /></span>
                  <strong>{title}</strong>
                  <span>{desc}</span>
                </button>
              ))}
            </div>
          </section>
        )}
        {historyQuery.isLoading && <div className="center-state">正在加载历史…</div>}
        {historyQuery.isError && sessionId && liveItems.length === 0 && <div className="center-state error">该会话尚无已提交历史，可以直接发送第一条消息。</div>}
        <div className={`messages ${items.length ? 'show' : ''}`}>
          {items.map((item) => {
            if (item.kind === 'reasoning') return <ReasoningTrace key={item.id} item={item} />
            if (item.kind === 'tool') return <ToolCallCard key={item.id} item={item} />
            if (item.kind === 'error') return <div key={item.id} className="chat-error">{item.content}</div>
            return (
              <article key={item.id} className={`message ${item.role === 'assistant' ? 'ai' : 'user'}`}>
                <div className="msg-avatar">{item.role === 'assistant' ? <Bot size={17} /> : <UserRound size={17} />}</div>
                <div className="bubble"><ReactMarkdown remarkPlugins={[remarkGfm]}>{item.content || (item.streaming ? '…' : '')}</ReactMarkdown></div>
              </article>
            )
          })}
        </div>
      </div>
      <div className="composer-zone">
        <div className="composer">
          <textarea
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter' && !event.shiftKey) { event.preventDefault(); void send() }
            }}
            placeholder={user ? '向 kemo-agent 发送消息…' : '请先选择用户'}
            disabled={!user || running}
            rows={1}
          />
          <div className="composer-row">
            <div style={{ display: 'flex', alignItems: 'center', minWidth: 0 }}>
              <div className="tool-dock">
                {toolDockButtons.map(({ icon: Icon, label }) => (
                  <button key={label} className="tool-btn" aria-label={label} title={label} disabled>
                    <Icon size={19} />
                  </button>
                ))}
              </div>
              <div className="composer-meta">
                <i />
                <span>{user || '未选择用户'}</span>
                <span>·</span>
                <span>{sessionId || '新会话'}</span>
                {usageText && <><span>·</span><span>{usageText}</span></>}
              </div>
            </div>
            <div className="composer-submit">
              <div className="composer-more">
                <button
                  className={`composer-more-btn ${conversationMenuOpen ? 'active' : ''}`}
                  onClick={() => setConversationMenuOpen((v) => !v)}
                  aria-expanded={conversationMenuOpen}
                  aria-label="展开对话操作"
                  title="对话操作"
                  disabled={running}
                >
                  <span>对话操作</span>
                  <svg width="13" height="13" fill="none" stroke="currentColor" strokeWidth="2" viewBox="0 0 24 24"><path d="m7 10 5 5 5-5" /></svg>
                </button>
                {conversationMenuOpen && (
                  <div className="conversation-menu show" role="menu">
                    <div className="conversation-menu-head">对话操作</div>
                    <button className="conversation-action" role="menuitem" onClick={newConversation}>
                      <span className="conversation-action-icon"><Plus size={16} /></span>
                      <span className="conversation-action-copy"><strong>创建新对话</strong><span>开启独立的上下文窗口</span></span>
                    </button>
                    <button className="conversation-action" role="menuitem" onClick={() => setConversationMenuOpen(false)}>
                      <span className="conversation-action-icon"><FileText size={16} /></span>
                      <span className="conversation-action-copy"><strong>保存当前对话</strong><span>写入当前用户的历史记录</span></span>
                    </button>
                    <button className="conversation-action compress" role="menuitem" onClick={() => setConversationMenuOpen(false)}>
                      <span className="conversation-action-icon"><Zap size={16} /></span>
                      <span className="conversation-action-copy"><strong>压缩上下文</strong><span>默认使用 Token 压缩机制</span></span>
                    </button>
                    <div className="conversation-menu-foot">自动压缩阈值 80% · 保留高权重记忆</div>
                  </div>
                )}
              </div>
              {running
                ? <button className="send-btn stop" onClick={stop}><Square size={15} /> 停止</button>
                : <button className="send-btn" onClick={() => void send()} disabled={!draft.trim() || !user}><Send size={15} /> 发送 ↗</button>}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
