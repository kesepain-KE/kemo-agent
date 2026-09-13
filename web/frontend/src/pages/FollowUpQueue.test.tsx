import { fireEvent, render, screen, within } from '@testing-library/react'
import { useState } from 'react'
import { describe, expect, it, vi } from 'vitest'
import type { PendingNextTurnMessage } from '../components/appShellTypes'
import { reorderFollowUps } from '../components/followUpQueue'
import { FollowUpQueue } from './FollowUpQueue'

function message(id: string, status: PendingNextTurnMessage['status'] = 'queued'): PendingNextTurnMessage {
  return { id, content: `跟进-${id}`, status, historyUserMessages: 1 }
}

describe('FollowUpQueue', () => {
  it('显示全部气泡，只有点气泡内本轮引导才提交指定消息', () => {
    const onGuide = vi.fn()
    render(<FollowUpQueue user="alice" messages={[message('a'), message('b')]} canGuide onGuide={onGuide} onRemove={vi.fn()} onRetry={vi.fn()} onReorder={vi.fn()} />)
    expect(onGuide).not.toHaveBeenCalled()
    const row = screen.getByRole('article', { name: '消息跟进 2' })
    fireEvent.click(within(row).getByRole('button', { name: '本轮引导' }))
    expect(onGuide).toHaveBeenCalledWith(message('b'))
    expect(screen.getAllByRole('article')).toHaveLength(2)
  })

  it('支持上移和拖动排序，文字与附件保持绑定', () => {
    const attached = { ...message('b'), uploadedFiles: [{ path: 'b.txt', name: 'b.txt', size: 1, mimeType: 'text/plain', mediaKind: 'file' as const }] }
    function Harness() {
      const [messages, setMessages] = useState([message('a'), attached, message('c')])
      return <FollowUpQueue user="alice" messages={messages} canGuide onGuide={vi.fn()} onRemove={vi.fn()} onRetry={vi.fn()} onReorder={(id, target) => setMessages((queue) => reorderFollowUps(queue, id, target))} />
    }
    const { container } = render(<Harness />)
    fireEvent.click(screen.getByRole('button', { name: '上移消息跟进 2' }))
    expect(screen.getAllByRole('article')[0]).toHaveTextContent('跟进-b')
    expect(screen.getAllByRole('article')[0]).toHaveTextContent('b.txt')
    const data = new Map<string, string>()
    const dataTransfer = { setData: (key: string, value: string) => data.set(key, value), getData: (key: string) => data.get(key) || '', effectAllowed: '' }
    fireEvent.dragStart(screen.getByRole('button', { name: '拖动消息跟进 3' }), { dataTransfer })
    fireEvent.drop(container.querySelector('[data-follow-up-id="b"]')!, { dataTransfer })
    expect(screen.getAllByRole('article', { name: /^消息跟进 \d+$/ }).map((row) => row.textContent)).toEqual([
      expect.stringContaining('跟进-c'), expect.stringContaining('跟进-b'), expect.stringContaining('跟进-a'),
    ])
  })

  it('发送中和提交引导中的条目不能移动、取消或重复提交', () => {
    const queue = [message('a', 'sending'), message('b'), message('c', 'guiding')]
    expect(reorderFollowUps(queue, 'b', 'a')).toBe(queue)
    expect(reorderFollowUps(queue, 'b', 'c')).toBe(queue)
    expect(reorderFollowUps(queue, 'missing', 'b')).toBe(queue)
    render(<FollowUpQueue user="alice" messages={queue} canGuide onGuide={vi.fn()} onRemove={vi.fn()} onRetry={vi.fn()} onReorder={vi.fn()} />)
    for (const position of [1, 3]) {
      const row = screen.getByRole('article', { name: `消息跟进 ${position}` })
      expect(within(row).getByRole('button', { name: '本轮引导' })).toBeDisabled()
      expect(within(row).getByRole('button', { name: '取消' })).toBeDisabled()
    }
  })

  it('停止期间不提供本轮引导，失败条目可重试或取消', () => {
    const onRetry = vi.fn(), onRemove = vi.fn()
    const { rerender } = render(<FollowUpQueue user="alice" messages={[message('a', 'error')]} canGuide={false} onGuide={vi.fn()} onRemove={onRemove} onRetry={onRetry} onReorder={vi.fn()} />)
    expect(screen.queryByRole('button', { name: '本轮引导' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '重新发送' }))
    expect(onRetry).toHaveBeenCalledWith('a')
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onRemove).toHaveBeenCalledWith('a')
    rerender(<FollowUpQueue user="alice" messages={[]} canGuide={false} onGuide={vi.fn()} onRemove={onRemove} onRetry={onRetry} onReorder={vi.fn()} />)
    expect(screen.queryByLabelText('消息跟进队列')).not.toBeInTheDocument()
  })
})
