import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { ChatItem, PlanSummary } from '../types/api'
import { AssistantTurn, assistantTurnPropsEqual } from './ChatPageView'
import { ChatStreamFixture, createChatStreamItems } from '../test/chatStreamFixture'
import { stripBackendJudgmentAsides } from './assistantOutput'

const probes = vi.hoisted(() => ({ partition: vi.fn(), copy: vi.fn() }))
vi.mock('./chatState', async (importOriginal) => {
  const original = await importOriginal<typeof import('./chatState')>()
  return { ...original, partitionAssistantTurnItems: (...args: Parameters<typeof original.partitionAssistantTurnItems>) => {
    probes.partition()
    return original.partitionAssistantTurnItems(...args)
  } }
})
vi.mock('../components/Chat/inlineWidgetProtocol', async (importOriginal) => {
  const original = await importOriginal<typeof import('../components/Chat/inlineWidgetProtocol')>()
  return { ...original, copyableAssistantText: (source: string) => {
    probes.copy(source)
    return original.copyableAssistantText(source)
  } }
})

function toolItem(id: string): Extract<ChatItem, { kind: 'tool' }> {
  return {
    id,
    kind: 'tool',
    callId: id,
    name: 'file',
    status: 'success',
    arguments: {},
    result: { ok: true },
  }
}

describe('ChatPageView streaming render boundaries', () => {
  beforeEach(() => vi.clearAllMocks())

  it('compares an unchanged turn and detects content and callback changes', () => {
    const items = Array.from({ length: 800 }, (_, index) => toolItem(`tool-${index}`))
    const callbacks = {
      resolvePlan: vi.fn((plan: PlanSummary) => plan),
      revealPlan: vi.fn(),
      regenerateLastResponse: vi.fn(),
      copyMessage: vi.fn(),
      handleWidgetAction: vi.fn(),
    }
    const previous = {
      block: { id: 'turn-1', kind: 'assistant' as const, items },
      user: 'alice',
      sessionId: 'session-1',
      running: true,
      conversationBusy: '',
      lastUserMessage: undefined,
      copiedItem: '',
      ...callbacks,
    }
    const next = { ...previous, block: { ...previous.block, items: [...items] } }
    expect(assistantTurnPropsEqual(previous, next)).toBe(true)
    expect(assistantTurnPropsEqual(previous, { ...next, copyMessage: vi.fn() })).toBe(false)

    const changed = {
      ...next,
      block: {
        ...next.block,
        items: [...items.slice(0, -1), toolItem('tool-last-changed')],
      },
    }
    expect(assistantTurnPropsEqual(previous, changed)).toBe(false)
  })

  it('skips the unchanged assistant turn when an unrelated page state updates', () => {
    const item = toolItem('tool-1')
    const callbacks = {
      resolvePlan: vi.fn((plan: PlanSummary) => plan),
      revealPlan: vi.fn(),
      regenerateLastResponse: vi.fn(),
      copyMessage: vi.fn(),
      handleWidgetAction: vi.fn(),
    }
    const props = {
      block: { id: 'turn-1', kind: 'assistant' as const, items: [item] },
      user: 'alice',
      sessionId: 'session-1',
      running: true,
      conversationBusy: '',
      lastUserMessage: undefined,
      copiedItem: '',
      ...callbacks,
    }
    function Harness({ tick }: { tick: number }) {
      return (
        <div data-tick={tick}>
          <AssistantTurn {...props} block={{ ...props.block, items: [...props.block.items] }} />
        </div>
      )
    }

    const view = render(<Harness tick={0} />)
    const partitionCalls = probes.partition.mock.calls.length
    view.rerender(<Harness tick={1} />)

    expect(probes.partition).toHaveBeenCalledTimes(partitionCalls)
    expect(view.container.querySelector('.tool-call')).not.toBeNull()
  })

  it('keeps the entire historical DOM and expanded tool state across real page deltas', async () => {
    let items = createChatStreamItems()
    const view = render(<ChatStreamFixture items={items} />)
    await waitFor(() => expect(view.container.querySelectorAll('.markdown-body')).toHaveLength(21))
    const articles = Array.from(view.container.querySelectorAll('.msg.is-assistant'))
    fireEvent.click(articles[0].querySelector('.assistant-process-toggle')!)
    const firstTool = articles[0].querySelector<HTMLButtonElement>('.tool-call-head')!
    fireEvent.click(firstTool)
    expect(firstTool).toHaveAttribute('aria-expanded', 'true')
    const historicalArticles = articles.slice(0, -1)
    const historicalNodes = historicalArticles.map((article) => Array.from(article.querySelectorAll('*')))
    const partitionCalls = probes.partition.mock.calls.length
    const mutations: MutationRecord[] = []
    const observer = new MutationObserver((records) => mutations.push(...records))
    for (const article of historicalArticles) {
      observer.observe(article, { attributes: true, childList: true, characterData: true, subtree: true })
    }
    for (let index = 0; index < 10; index += 1) {
      items = items.map((item) => item.id === 'answer-live' && item.kind === 'message'
        ? { ...item, content: `${item.content}，增量 ${index}` } : item)
      view.rerender(<ChatStreamFixture items={items} tick={index} />)
    }
    await Promise.resolve()
    observer.disconnect()
    expect(probes.partition).toHaveBeenCalledTimes(partitionCalls + 10)
    expect(probes.copy).not.toHaveBeenCalled()
    historicalArticles.forEach((article, index) => {
      expect(Array.from(article.querySelectorAll('*'))).toEqual(historicalNodes[index])
    })
    expect(mutations).toHaveLength(0)
    expect(firstTool).toHaveAttribute('aria-expanded', 'true')
    expect(view.container.querySelector('.composer-dock')).not.toBeNull()
    expect(view.container.querySelector('.user-message-navigator')).toBeDefined()
    expect(screen.getByText(/增量 9/)).toBeInTheDocument()
  })

  it('keeps measured React render calls below 50ms during 10 full conversation-delta renders', async () => {
    let items = createChatStreamItems()
    const view = render(<ChatStreamFixture items={items} />)
    await waitFor(() => expect(view.container.querySelectorAll('.markdown-body')).toHaveLength(21))
    const samples: number[] = []
    for (let index = 0; index < 10; index += 1) {
      items = items.map((item) => item.id === 'answer-live' && item.kind === 'message'
        ? { ...item, content: `${item.content}，计时 ${index}` } : item)
      const started = performance.now()
      view.rerender(<ChatStreamFixture items={items} tick={index} />)
      samples.push(performance.now() - started)
    }
    expect(Math.max(...samples)).toBeLessThan(50)
  })

  it('keeps an expanded process and its tool DOM when earlier turns are prepended', async () => {
    const recent = createChatStreamItems(1)
    const view = render(<ChatStreamFixture items={recent} />)
    await waitFor(() => expect(view.container.querySelectorAll('.markdown-body')).toHaveLength(2))
    const process = view.container.querySelector<HTMLDivElement>('.assistant-processing')!
    fireEvent.click(view.container.querySelector('.assistant-process-toggle')!)
    const tool = process.querySelector<HTMLButtonElement>('.tool-call-head')!
    fireEvent.click(tool)
    expect(process.hidden).toBe(false)
    expect(tool).toHaveAttribute('aria-expanded', 'true')
    const earlier: ChatItem[] = [
      { id: 'older-user', kind: 'message', role: 'user', content: '分页补入的提问' },
      { id: 'older-answer', kind: 'message', role: 'assistant', content: '分页补入的回复', level: 'final' },
    ]
    view.rerender(<ChatStreamFixture items={[...earlier, ...recent]} />)
    expect(view.container.contains(process)).toBe(true)
    expect(process.hidden).toBe(false)
    expect(process.querySelector('.tool-call-head')).toBe(tool)
    expect(tool).toHaveAttribute('aria-expanded', 'true')
  })

  it('copies the latest full reply on demand and uses the latest callback', () => {
    const copyMessage = vi.fn()
    const nextCopyMessage = vi.fn()
    const props = {
      block: { id: 'turn-copy', kind: 'assistant' as const, items: [
        { id: 'copy-answer', kind: 'message' as const, role: 'assistant' as const, content: '旧正文', level: 'final' as const },
      ] },
      user: 'alice', sessionId: 'session-1', running: false, conversationBusy: '',
      lastUserMessage: undefined, copiedItem: '', copyMessage,
      resolvePlan: (plan: PlanSummary) => plan,
      revealPlan: vi.fn(), regenerateLastResponse: vi.fn(), handleWidgetAction: vi.fn(),
    }
    const view = render(<AssistantTurn {...props} />)
    const nextProps = { ...props, copyMessage: nextCopyMessage, block: {
      ...props.block, items: [{ ...props.block.items[0], content: '最新正文' }],
    } }
    view.rerender(<AssistantTurn {...nextProps} />)
    expect(probes.copy).not.toHaveBeenCalled()
    fireEvent.click(screen.getByRole('button', { name: '复制智能体回复' }))
    expect(probes.copy).toHaveBeenCalledWith('最新正文')
    expect(nextCopyMessage).toHaveBeenCalledWith('copy-answer', '最新正文')
    expect(copyMessage).not.toHaveBeenCalled()
  })

  it('hides backend duplicate-call asides from the final reply and copied text', () => {
    const copyMessage = vi.fn()
    const aside = '（第二次调用被系统判定为重复调用已拦截，时间以上面这次实测结果为准。）'
    const content = `时区偏移：+0800（Asia/Shanghai）\n${aside}`
    const props = {
      block: { id: 'turn-backend-aside', kind: 'assistant' as const, items: [
        { id: 'answer-backend-aside', kind: 'message' as const, role: 'assistant' as const, content, level: 'final' as const },
      ] },
      user: 'alice', sessionId: 'session-1', running: false, conversationBusy: '',
      lastUserMessage: undefined, copiedItem: '', copyMessage,
      resolvePlan: (plan: PlanSummary) => plan,
      revealPlan: vi.fn(), regenerateLastResponse: vi.fn(), handleWidgetAction: vi.fn(),
    }
    const view = render(<AssistantTurn {...props} />)
    expect(view.container.textContent).toContain('Asia/Shanghai')
    expect(view.container.textContent).not.toContain('第二次调用')
    fireEvent.click(screen.getByRole('button', { name: '复制智能体回复' }))
    expect(copyMessage).toHaveBeenCalledWith('answer-backend-aside', stripBackendJudgmentAsides(content))
  })

  it('uses the avatar to toggle a completed process and preserves its tool state', () => {
    const props = {
      block: { id: 'turn-final', kind: 'assistant' as const, items: [
        { id: 'reasoning-1', kind: 'reasoning' as const, content: '第一段思考' },
        toolItem('tool-1'),
        { id: 'interim', kind: 'message' as const, role: 'assistant' as const, content: '临时正文', level: 'interim' as const },
        { id: 'reasoning-2', kind: 'reasoning' as const, content: '第二段思考' },
        toolItem('tool-2'),
        { id: 'final', kind: 'message' as const, role: 'assistant' as const, content: '最终正文', level: 'final' as const },
        { id: 'usage', kind: 'usage' as const, usage: { prompt_tokens: 10, completion_tokens: 2, total_tokens: 12 }, providerRequestCount: 3, elapsedMs: 1200 },
      ] },
      user: 'alice', sessionId: 'session-1', running: false, conversationBusy: '',
      lastUserMessage: { id: 'user-final', kind: 'message' as const, role: 'user' as const, content: '测试问题' }, copiedItem: '', copyMessage: vi.fn(),
      resolvePlan: (plan: PlanSummary) => plan,
      revealPlan: vi.fn(), regenerateLastResponse: vi.fn(), handleWidgetAction: vi.fn(),
    }
    const view = render(<AssistantTurn {...props} />)
    const body = view.container.querySelector('.msg-body')!
    const process = body.querySelector<HTMLDivElement>('.assistant-processing')!
    const avatar = screen.getByRole('button', { name: '展开处理过程' })
    expect(body.children).toHaveLength(2)
    expect(process.hidden).toBe(true)
    expect(avatar).toHaveAttribute('aria-expanded', 'false')
    expect(avatar).toHaveAttribute('aria-controls', process.id)
    expect(avatar.querySelector('.kemo-logo')).not.toBeNull()
    expect(body.querySelector('summary')).toBeNull()
    expect(body.querySelector('details')).toBeNull()
    const final = body.querySelector('.assistant-turn-final')!
    const footer = final.querySelector('.assistant-turn-footer')!
    expect(final.textContent).toContain('最终正文')
    expect(footer.previousElementSibling).toHaveClass('assistant-response')
    expect(final.querySelector('.bubble .assistant-turn-footer')).toBeNull()
    expect(process.querySelector('.usage-card')).toBeNull()
    expect(body.querySelectorAll('.usage-card')).toHaveLength(1)
    expect(footer.querySelectorAll('.usage-summary-chip')).toHaveLength(6)
    const actions = footer.querySelector('.assistant-turn-actions')!
    expect(actions.previousElementSibling).toHaveClass('assistant-turn-usage')
    expect(Array.from(actions.children).map((button) => button.getAttribute('aria-label'))).toEqual([
      '重试智能体回复', '复制智能体回复',
    ])
    fireEvent.click(actions.querySelector('.assistant-turn-retry')!)
    expect(props.regenerateLastResponse).toHaveBeenCalledOnce()

    fireEvent.click(avatar)
    expect(process.hidden).toBe(false)
    expect(avatar).toHaveAttribute('aria-expanded', 'true')
    expect(avatar).toHaveAccessibleName('折叠处理过程')
    expect(avatar.querySelector('.kemo-logo')).toBeNull()
    expect(avatar.querySelector('.lucide-chevron-up')).not.toBeNull()
    const eventKinds = Array.from(process.querySelector('.assistant-processing-body')!.children)
      .filter((element) => element.matches('.trace, .tool-call, .assistant-response'))
      .map((element) => element.classList[0])
    expect(eventKinds).toEqual(['trace', 'tool-call', 'assistant-response', 'trace', 'tool-call'])
    const tool = process.querySelector<HTMLButtonElement>('.tool-call-head')!
    fireEvent.click(tool)
    expect(tool).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(avatar)
    expect(process.hidden).toBe(true)
    expect(avatar).toHaveAccessibleName('展开处理过程')
    expect(avatar.querySelector('.kemo-logo')).not.toBeNull()
    expect(final).toBeVisible()
    fireEvent.click(avatar)
    expect(process.hidden).toBe(false)
    expect(process.querySelector('.tool-call-head')).toBe(tool)
    expect(tool).toHaveAttribute('aria-expanded', 'true')
  })

  it('keeps an unfinished turn in its streamed event order', () => {
    const props = {
      block: { id: 'turn-live', kind: 'assistant' as const, items: [
        { id: 'reasoning-live', kind: 'reasoning' as const, content: '先思考', streaming: true },
        toolItem('tool-live'),
        { id: 'interim-live', kind: 'message' as const, role: 'assistant' as const, content: '临时正文', level: 'interim' as const },
        { id: 'reasoning-next', kind: 'reasoning' as const, content: '继续思考', streaming: true },
        { id: 'answer-live', kind: 'message' as const, role: 'assistant' as const, content: '输出中', streaming: true },
      ] },
      user: 'alice', sessionId: 'session-1', running: true, conversationBusy: '',
      lastUserMessage: undefined, copiedItem: '', copyMessage: vi.fn(),
      resolvePlan: (plan: PlanSummary) => plan,
      revealPlan: vi.fn(), regenerateLastResponse: vi.fn(), handleWidgetAction: vi.fn(),
    }
    const view = render(<AssistantTurn {...props} />)
    const body = view.container.querySelector('.msg-body')!
    expect(body.querySelector('.assistant-processing')).toBeNull()
    expect(view.container.querySelector('.assistant-process-toggle')).toBeNull()
    expect(Array.from(body.children)
      .filter((element) => !element.classList.contains('assistant-turn-footer'))
      .map((element) => element.classList[0])).toEqual([
      'trace', 'tool-call', 'assistant-response', 'trace', 'assistant-response',
    ])
  })

  it('keeps final response statistics visible without an empty process disclosure', () => {
    const props = {
      block: { id: 'turn-only-final', kind: 'assistant' as const, items: [
        { id: 'final-only', kind: 'message' as const, role: 'assistant' as const, content: '直接回答', level: 'final' as const },
        { id: 'usage-only', kind: 'usage' as const, usage: { prompt_tokens: 10, completion_tokens: 2 } },
      ] },
      user: 'alice', sessionId: 'session-1', running: false, conversationBusy: '',
      lastUserMessage: undefined, copiedItem: '', copyMessage: vi.fn(),
      resolvePlan: (plan: PlanSummary) => plan,
      revealPlan: vi.fn(), regenerateLastResponse: vi.fn(), handleWidgetAction: vi.fn(),
    }
    const view = render(<AssistantTurn {...props} />)
    const body = view.container.querySelector('.msg-body')!
    expect(body.querySelector('.assistant-processing')).toBeNull()
    expect(view.container.querySelector('.assistant-process-toggle')).toBeNull()
    expect(body.querySelectorAll(':scope > *')).toHaveLength(1)
    expect(body.textContent).toContain('直接回答')
    expect(body.querySelector('.assistant-turn-final > .assistant-turn-footer .usage-card')).not.toBeNull()
  })
})
