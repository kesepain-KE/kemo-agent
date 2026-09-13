import { render, screen, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { ChatItem } from '../types/api'
import { reduceRunEvent, finalizeCurrentRoundItems, resetCurrentRoundItemsForRetry } from '../pages/chatState'
import { SubagentToolCard } from './SubagentProgressBubble'

function start(items: ChatItem[] = [], callId = 'call-a', agent = 'task_plan') {
  return reduceRunEvent(items, { type: 'tool_call_start', tool_call_id: callId, tool_name: 'subagent_dispatch', arguments: { action: 'call', agent } })
}

function tool(items: ChatItem[], callId = 'call-a') {
  return items.find((item): item is Extract<ChatItem, { kind: 'tool' }> => item.kind === 'tool' && item.callId === callId)!
}

describe('subagent progress attached to its tool card', () => {
  it('appears immediately below the matching tool card and updates its actual stage', () => {
    let items = start()
    const { container, rerender } = render(<SubagentToolCard item={tool(items)} progress={items} running />)
    expect(screen.getByText('正在准备')).toBeInTheDocument()
    expect(container.querySelector('.tool-call')?.nextElementSibling).toHaveAttribute('aria-label', '子代理运行进度')
    items = reduceRunEvent(items, { type: 'subagent_progress', tool_call_id: 'call-a', metadata: { agent: 'task_plan', status: 'tool_running', iteration: 2, tool_name: 'file' } })
    rerender(<SubagentToolCard item={tool(items)} progress={items} running />)
    expect(screen.getByText('第 2 轮 · 正在调用 file')).toBeInTheDocument()
    expect(items.filter((item) => item.kind === 'reasoning')).toHaveLength(0)
    expect(items.filter((item) => item.kind === 'subagent_progress')).toHaveLength(1)
  })

  it('keeps same-name agents attached to separate call IDs', () => {
    const items = start(start(), 'call-b')
    const { container } = render(<>
      <SubagentToolCard item={tool(items)} progress={items} running />
      <SubagentToolCard item={tool(items, 'call-b')} progress={items} running />
    </>)
    for (const id of ['call-a', 'call-b']) {
      expect(within(container.querySelector(`[data-subagent-call-id="${id}"]`) as HTMLElement).getAllByLabelText('子代理运行进度')).toHaveLength(1)
    }
    const completed = reduceRunEvent(items, { type: 'subagent_progress', tool_call_id: 'call-a', metadata: { status: 'completed' } })
    expect(completed.filter((item) => item.kind === 'subagent_progress').map((item) => item.callId)).toEqual(['call-b'])
  })

  it.each(['completed', 'completed_after_timeout', 'failed', 'cancelled', 'timed_out'])('disappears on %s', (status) => {
    let items = start()
    const { rerender } = render(<SubagentToolCard item={tool(items)} progress={items} running />)
    items = reduceRunEvent(items, { type: 'subagent_progress', tool_call_id: 'call-a', metadata: { status } })
    rerender(<SubagentToolCard item={tool(items)} progress={items} running />)
    expect(screen.queryByLabelText('子代理运行进度')).not.toBeInTheDocument()
  })

  it('clears on dispatch result, parent completion, interruption and retry', () => {
    const items = start()
    for (const event of [
      { type: 'tool_call_result' as const, tool_call_id: 'call-a', result: { ok: true, result: { status: 'completed' } } },
      { type: 'done' as const }, { type: 'error' as const, error: { message: '失败' } },
      { type: 'long_task_update' as const },
    ]) expect(reduceRunEvent(items, event).some((item) => item.kind === 'subagent_progress')).toBe(false)
    expect(finalizeCurrentRoundItems(items, {}).some((item) => item.kind === 'subagent_progress')).toBe(false)
    expect(resetCurrentRoundItemsForRetry(items).some((item) => item.kind === 'subagent_progress')).toBe(false)
    const { container } = render(<SubagentToolCard item={tool(items)} progress={items} running={false} />)
    expect(within(container).queryByLabelText('子代理运行进度')).not.toBeInTheDocument()
  })

  it('does not show a bubble for list/status operations and keeps detached tasks live', () => {
    expect(reduceRunEvent([], { type: 'tool_call_start', tool_call_id: 'list', tool_name: 'subagent_dispatch', arguments: { action: 'list' } }).some((item) => item.kind === 'subagent_progress')).toBe(false)
    const items = reduceRunEvent(start(), { type: 'tool_call_result', tool_call_id: 'call-a', result: { ok: true, result: { status: 'queued' } } })
    expect(items.some((item) => item.kind === 'subagent_progress')).toBe(true)
  })
})
