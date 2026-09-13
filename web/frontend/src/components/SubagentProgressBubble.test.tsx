import { render, screen, within } from '@testing-library/react'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
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
  it('shows only a numbered tool row on the matching tool card right side', () => {
    let items = start()
    const { container, rerender } = render(<SubagentToolCard item={tool(items)} progress={items} running />)
    const anchor = container.querySelector('[data-subagent-call-id="call-a"]')!
    expect(anchor).toContainElement(container.querySelector('.tool-call'))
    expect(screen.queryByLabelText('子代理运行进度')).not.toBeInTheDocument()
    items = reduceRunEvent(items, { type: 'subagent_progress', tool_call_id: 'call-a', metadata: { agent: 'task_plan', status: 'tool_running', iteration: 2, tool_name: 'file' } })
    rerender(<SubagentToolCard item={tool(items)} progress={items} running />)
    const progress = screen.getByLabelText('子代理运行进度')
    expect(anchor).toContainElement(progress)
    expect(screen.getByText('2: file')).toBeInTheDocument()
    expect(within(progress).getByTitle('2: file')).toBeInTheDocument()
    expect(progress).not.toHaveTextContent('工具调用中')
    expect(progress).not.toHaveTextContent('action:')
    expect(items.filter((item) => item.kind === 'reasoning')).toHaveLength(0)
    expect(items.filter((item) => item.kind === 'subagent_progress')).toHaveLength(1)
  })

  it('keeps same-name agents attached to separate call IDs', () => {
    let items = start(start(), 'call-b')
    items = reduceRunEvent(items, { type: 'subagent_progress', tool_call_id: 'call-a', metadata: { status: 'tool_running', iteration: 1, tool_name: 'file' } })
    items = reduceRunEvent(items, { type: 'subagent_progress', tool_call_id: 'call-b', metadata: { status: 'tool_running', iteration: 1, tool_name: 'shell' } })
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

  it('retains each tool round so the side card can scroll after its sixth row', () => {
    let items = start()
    for (let iteration = 1; iteration <= 7; iteration += 1) {
      items = reduceRunEvent(items, {
        type: 'subagent_progress', tool_call_id: 'call-a',
        metadata: { status: 'tool_running', iteration, tool_name: `tool-${iteration}` },
      })
    }
    const { container } = render(<SubagentToolCard item={tool(items)} progress={items} running />)
    const progress = screen.getByLabelText('子代理运行进度')

    expect(within(progress).getAllByTitle(/\d+: tool-/)).toHaveLength(7)
    expect(within(progress).getByText('1: tool-1')).toBeInTheDocument()
    expect(within(progress).getByText('7: tool-7')).toBeInTheDocument()
    expect(container.querySelectorAll('[data-subagent-call-id="call-a"] [aria-label="子代理运行进度"]')).toHaveLength(1)
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

describe('subagent progress side-card layout', () => {
  const stylesheet = readFileSync(resolve(process.cwd(), 'src/components/SubagentProgressBubble.module.css'), 'utf8')

  it('keeps the ordinary tool card full-sized and places progress outside its right edge', () => {
    const anchorRule = stylesheet.match(/\.anchor\s*\{([^}]*)\}/)?.[1] ?? ''
    const stackRule = stylesheet.match(/\.stack\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(anchorRule).toContain('position: relative')
    expect(anchorRule).toContain('width: min(100%, 880px)')
    expect(anchorRule).not.toContain('grid-template-columns')
    expect(stackRule).toContain('position: absolute')
    expect(stackRule).toContain('left: calc(100% + 14px)')
    expect(stackRule).toContain('width: calc((100dvw / 6) * .7)')
  })

  it('starts at one normal card height, caps at six rounds, and fixes every row to one line', () => {
    const stackRule = stylesheet.match(/\.stack\s*\{([^}]*)\}/)?.[1] ?? ''
    const bubbleRule = stylesheet.match(/\.bubble\s*\{([^}]*)\}/)?.[1] ?? ''
    const textRule = stylesheet.match(/\.round\s*\{([^}]*)\}/)?.[1] ?? ''

    expect(stackRule).toContain('min-height: 44px')
    expect(stackRule).toContain('max-height: 224px')
    expect(stackRule).toContain('overflow-y: auto')
    expect(stackRule).toContain('overscroll-behavior: contain')
    expect(bubbleRule).toContain('height: 30px')
    expect(bubbleRule).toContain('max-height: 30px')
    expect(textRule).toContain('text-overflow: ellipsis')
    expect(textRule).toContain('white-space: nowrap')
  })
})
