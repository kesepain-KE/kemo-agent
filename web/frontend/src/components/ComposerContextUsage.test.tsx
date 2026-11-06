import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { useUiStore } from '../store/ui'
import type { OverviewResponse } from '../types/api'
import { ComposerContextUsage } from './ComposerContextUsage'

afterEach(() => {
  useUiStore.getState().setLanguage('zh-CN', '')
})

function snapshot(overrides: Partial<OverviewResponse['context_snapshot']> = {}): OverviewResponse['context_snapshot'] {
  return {
    available: true,
    source: 'cardstream',
    measurement: 'local_estimate',
    captured_at: '2026-11-04T10:00:00Z',
    system_prompt_tokens: 2200,
    tool_schema_tokens: 10300,
    conversation_tokens: 250000,
    summary_tokens: 15000,
    other_tokens: 0,
    total_tokens: 277500,
    capacity_tokens: 1000000,
    percent: 27.75,
    cards_active: 18,
    ...overrides,
  }
}

describe('ComposerContextUsage', () => {
  it('opens a compact breakdown of the foreground context and session cache hit rate', () => {
    const { rerender } = render(<ComposerContextUsage snapshot={snapshot()} cacheHitRate={0.9984} />)

    const trigger = screen.getByRole('button', { name: '查看上下文用量' })
    const cacheTrigger = screen.getByRole('button', { name: '缓存命中 99.84%' })
    expect(trigger).toHaveAttribute('aria-haspopup', 'dialog')
    fireEvent.click(trigger)

    const dialog = screen.getByRole('dialog', { name: '上下文用量' })
    expect(dialog).toHaveTextContent('上下文已用 27.8%')
    expect(dialog).toHaveTextContent('~277.5K / 1M')
    expect(dialog).toHaveTextContent('系统提示词')
    expect(dialog).toHaveTextContent('~2.2K')
    expect(dialog).toHaveTextContent('工具定义')
    expect(dialog).toHaveTextContent('~10.3K')
    expect(dialog).toHaveTextContent('历史对话')
    expect(dialog).toHaveTextContent('~265K')
    expect(screen.getByText('Token 总命中率').closest('div')).toHaveTextContent('99.84%')
    expect(screen.getByText('当前对话空间累计')).toBeInTheDocument()
    expect(dialog).toHaveAttribute('tabindex', '-1')
    expect(dialog.querySelector('[data-segment="system"]')).not.toBeNull()
    expect(dialog.querySelector('[data-segment="tools"]')).not.toBeNull()
    expect(dialog.querySelector('[data-segment="history"]')).not.toBeNull()
    expect(cacheTrigger).toHaveAttribute('aria-controls', dialog.id)

    fireEvent.pointerDown(cacheTrigger)
    fireEvent.click(cacheTrigger)
    expect(screen.queryByRole('dialog', { name: '上下文用量' })).not.toBeInTheDocument()
    fireEvent.click(cacheTrigger)
    expect(screen.getByRole('dialog', { name: '上下文用量' })).toHaveAttribute('id', dialog.id)
    fireEvent.pointerDown(trigger)
    fireEvent.click(trigger)
    expect(screen.queryByRole('dialog', { name: '上下文用量' })).not.toBeInTheDocument()

    rerender(<ComposerContextUsage snapshot={snapshot()} cacheHitRate={0.8} />)
    expect(screen.getByRole('button', { name: '缓存命中 80.00%' })).toBeInTheDocument()
  })

  it('shows dynamic foreground tokens as a separate segment when present', () => {
    render(<ComposerContextUsage snapshot={snapshot({ other_tokens: 1200, total_tokens: 278700 })} />)
    fireEvent.click(screen.getByRole('button', { name: '查看上下文用量' }))
    expect(screen.getByText('动态上下文')).toBeInTheDocument()
    expect(screen.getByText('~1.2K')).toBeInTheDocument()
  })

  it('does not present unavailable context or a missing cache rate as zero', () => {
    render(<ComposerContextUsage snapshot={snapshot({ available: false })} capacityTokens={120000} />)
    expect(screen.getByRole('button', { name: '缓存命中 —' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '查看上下文用量' }))

    const dialog = screen.getByRole('dialog', { name: '上下文用量' })
    expect(dialog).toHaveTextContent('上下文已用 —')
    expect(screen.getByText('Token 总命中率').closest('div')).toHaveTextContent('—')
    expect(screen.getByText('系统提示词').nextElementSibling).toHaveTextContent('—')
    expect(screen.getByText('工具定义').nextElementSibling).toHaveTextContent('—')
    expect(screen.getByText('历史对话').nextElementSibling).toHaveTextContent('—')
  })

  it('supports Escape to close and return focus to the trigger', () => {
    render(<ComposerContextUsage snapshot={snapshot()} cacheHitRate={null} />)
    for (const name of ['查看上下文用量', '缓存命中 —']) {
      const trigger = screen.getByRole('button', { name })
      fireEvent.click(trigger)
      expect(screen.getByRole('dialog', { name: '上下文用量' })).toHaveFocus()
      fireEvent.keyDown(document, { key: 'Escape' })
      expect(screen.queryByRole('dialog', { name: '上下文用量' })).not.toBeInTheDocument()
      expect(trigger).toHaveFocus()

      fireEvent.click(trigger)
      fireEvent.pointerDown(document.body)
      expect(screen.queryByRole('dialog', { name: '上下文用量' })).not.toBeInTheDocument()
    }
  })
})
