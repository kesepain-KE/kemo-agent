import { act, fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { ReasoningTrace, ToolCallCard, UsageCard } from './RunEventCards'

describe('RunEventCards', () => {
  it('运行中默认展开、尊重手动折叠，并在运行结束后自动收起', () => {
    const { rerender } = render(<ReasoningTrace item={{ id: 'r1', kind: 'reasoning', content: '正在分析', streaming: true }} />)
    const toggle = screen.getByRole('button', { name: '正在思考' })
    expect(toggle).toHaveAttribute('aria-expanded', 'true')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')

    rerender(<ReasoningTrace item={{ id: 'r1', kind: 'reasoning', content: '继续分析', streaming: true }} />)
    expect(toggle).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(toggle)
    expect(toggle).toHaveAttribute('aria-expanded', 'true')

    rerender(<ReasoningTrace item={{ id: 'r1', kind: 'reasoning', content: '分析完成', streaming: false }} />)
    expect(screen.getByRole('button', { name: '思考过程' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('思考复制按钮位于正文末尾的操作区', () => {
    render(<ReasoningTrace item={{ id: 'r1', kind: 'reasoning', content: '正在分析', streaming: true }} />)
    expect(screen.getByRole('button', { name: '复制思考过程' }).closest('.trace-body-actions')).not.toBeNull()
  })

  it('工具调用即使运行中也默认折叠', () => {
    render(<ToolCallCard item={{ id: 't1', kind: 'tool', callId: 'call-1', name: 'file', status: 'running' }} />)
    expect(screen.getByRole('button', { name: /file/ })).toHaveAttribute('aria-expanded', 'false')
  })

  it('工具运行时从零计时，完成后锁定耗时并显示结果', () => {
    vi.useFakeTimers()
    try {
      const { container, rerender } = render(<ToolCallCard item={{
        id: 't1', kind: 'tool', callId: 'call-1', name: 'shell', arguments: { command: 'status' }, status: 'running',
      }} />)
      expect(container.querySelector('.tool-elapsed')).toHaveTextContent('0.0 s')
      act(() => vi.advanceTimersByTime(1200))
      expect(container.querySelector('.tool-elapsed')).toHaveTextContent('1.2 s')

      fireEvent.click(screen.getByRole('button', { name: /shell/ }))
      expect(screen.getByText('工具仍在运行，完成后显示结果')).toBeInTheDocument()
      expect(container.querySelector('.tool-call-status.success')).toBeNull()

      rerender(<ToolCallCard item={{
        id: 't1', kind: 'tool', callId: 'call-1', name: 'shell', arguments: { command: 'status' },
        result: { ok: true }, status: 'success', elapsedMs: 1234,
      }} />)
      expect(container.querySelector('.tool-elapsed')).toHaveTextContent('1.2 s')
      expect(screen.queryByText('工具仍在运行，完成后显示结果')).not.toBeInTheDocument()
      expect(screen.getByText(/"ok": true/)).toBeInTheDocument()
      expect(container.querySelector('.tool-call-status.success')).toHaveAttribute('aria-label', '已完成')
      expect(container.querySelector('.tool-call-status.success')).not.toHaveTextContent('已完成')
      expect(container.querySelector('.tool-call-meta')?.lastElementChild).toHaveClass('tool-call-status', 'success')
    } finally {
      vi.useRealTimers()
    }
  })

  it('工具正文点击后才渲染，并将参数和结果截断为前 5000 个字符', () => {
    const longText = 'x'.repeat(5100)
    const { container } = render(<ToolCallCard item={{
      id: 't1', kind: 'tool', callId: 'call-1', name: 'shell', status: 'success',
      argumentsText: longText, resultText: longText,
    }} />)
    expect(container.querySelector('.tool-call-body')).toBeNull()

    fireEvent.click(screen.getByRole('button', { name: /shell/ }))
    const payloads = [...container.querySelectorAll('.tool-call-panel pre')].map((node) => node.textContent || '')
    expect(payloads).toHaveLength(2)
    expect(payloads.every((text) => text.startsWith('x'.repeat(5000)))).toBe(true)
    expect(payloads.every((text) => text.includes('已截断'))).toBe(true)
  })

  it('运行统计是没有下拉按钮的静态指标面板', () => {
    render(<UsageCard item={{
      id: 'u1', kind: 'usage', elapsedMs: 2200,
      usage: { prompt_tokens: 11838, completion_tokens: 27, total_tokens: 11865, cached_prompt_tokens: 7296, cache_hit_rate: .616 },
    }} />)
    expect(screen.getByRole('group', { name: '本轮运行统计' })).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /运行统计/ })).not.toBeInTheDocument()
    expect(screen.getByText('7,296')).toBeInTheDocument()
    expect(screen.getByText('61.6%')).toBeInTheDocument()
    expect(screen.getByText('2.2 s')).toBeInTheDocument()
  })
})
