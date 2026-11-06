import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { useMemo, useRef } from 'react'
import { ChatPageView } from '../pages/ChatPageView'
import { buildUserMessageMarkers, groupConversationItems } from '../pages/chatState'
import type { ChatItem, PlanSummary } from '../types/api'

const noop = () => {}
const resolvePlan = (plan: PlanSummary) => plan
const query = { isLoading: false, isFetching: false, isError: false }
const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })

/** Offline production-view fixture: no model requests, services, or user data. */
export function createChatStreamItems(rounds = 20): ChatItem[] {
  const items: ChatItem[] = []
  for (let round = 0; round < rounds; round += 1) {
    items.push(
      { id: `user-${round}`, kind: 'message', role: 'user', content: `测试问题 ${round}` },
      { id: `reasoning-${round}`, kind: 'reasoning', content: '已完成分析', streaming: false },
      { id: `tool-${round}`, kind: 'tool', callId: `call-${round}`, name: 'file',
        arguments: { action: 'read', path: `fixture-${round}.md` },
        status: 'success', result: { ok: true }, elapsedMs: 10 },
      { id: `answer-${round}`, kind: 'message', role: 'assistant',
        content: `## 测试回复 ${round}\n\n正文含 **格式** 和列表。\n\n- 第一项\n- 第二项\n\n\`\`\`python\nprint(${round})\n\`\`\``,
        level: 'final', streaming: false },
      { id: `usage-${round}`, kind: 'usage', usage: { prompt_tokens: 1200, completion_tokens: 100 } },
    )
  }
  items.push(
    { id: 'user-live', kind: 'message', role: 'user', content: '继续输出' },
    { id: 'answer-live', kind: 'message', role: 'assistant', content: '流式回复', streaming: true },
  )
  return items
}

export function ChatStreamFixture({ items, tick = 0 }: { items: ChatItem[]; tick?: number }) {
  const scrollRef = useRef<HTMLDivElement>(null)
  const conversationBlocks = useMemo(() => groupConversationItems(items), [items])
  const userMessageMarkers = useMemo(() => buildUserMessageMarkers(items), [items])
  const liveItems = useMemo(() => items.slice(-2), [items])
  return <QueryClientProvider client={client}>
    <div data-page-tick={tick}>
      <ChatPageView
        items={items}
        liveItems={liveItems}
        conversationBlocks={conversationBlocks}
        userMessageMarkers={userMessageMarkers}
        recentTasks={[]}
        pendingUploads={[]}
        capabilityItems={[]}
        user="alice"
        sessionId="offline-stream-fixture"
        liveSessionId="offline-stream-fixture"
        historyQuery={query}
        knowledgeQuery={query}
        expandsQuery={query}
        skillsQuery={query}
        reasoningControl={{ value: '', options: [], disabled: true, emptyLabel: '—', onChange: noop }}
        scrollRef={scrollRef}
        editedSources={new Set()}
        collapsedPlans={new Set()}
        followUpQueue={[]}
        lastUserMessage={items.find((item) => item.id === 'user-live')}
        totalRounds={21}
        roundLimit={80}
        currentRound={21}
        running
        stopping={false}
        conversationBusy=""
        copiedItem=""
        draft=""
        showWelcome={false}
        resolvePlan={resolvePlan}
        revealPlan={noop}
        regenerateLastResponse={noop}
        copyMessage={noop}
        setDraft={noop}
        handleChatScroll={noop}
        jumpToUserMessage={noop}
      />
    </div>
  </QueryClientProvider>
}
