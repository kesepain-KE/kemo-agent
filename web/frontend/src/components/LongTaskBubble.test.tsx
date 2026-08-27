import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { LongTaskState } from '../types/api'
import { LongTaskBubble } from './LongTaskBubble'

function state(status: LongTaskState['status']): LongTaskState {
  return {
    enabled: true,
    status,
    task_id: 'long-task-1',
    original_prompt: '执行稳定性检查',
    started_at: '2026-08-26T00:00:00+00:00',
    updated_at: '2026-08-26T00:01:00+00:00',
    finished_at: '',
    run_count: 2,
    continuation_count: 1,
    total_tool_calls: 8,
    total_provider_requests: 3,
    active_elapsed_ms: 60_000,
    usage: { total_tokens: 2048 },
    current_run_id: 'run-2',
    last_stop_reason: '',
    cancel_requested: false,
    last_error: null,
  }
}

describe('LongTaskBubble', () => {
  it('暂停状态提供真实的结束操作', () => {
    const onCancel = vi.fn()
    render(<LongTaskBubble state={state('paused')} onCancel={onCancel} />)

    fireEvent.click(screen.getByRole('button', { name: '结束长任务' }))

    expect(onCancel).toHaveBeenCalledOnce()
    expect(screen.getByText('已暂停')).toBeInTheDocument()
  })

  it('正在停止时禁用重复取消', () => {
    const onCancel = vi.fn()
    render(<LongTaskBubble state={state('cancelling')} onCancel={onCancel} />)

    const button = screen.getByRole('button', { name: '正在停止…' })
    expect(button).toBeDisabled()
    fireEvent.click(button)
    expect(onCancel).not.toHaveBeenCalled()
  })
})
