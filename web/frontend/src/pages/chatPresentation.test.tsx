import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { GuidanceMessage, TaskPlanRecord } from './chatPresentation'
import type { PlanSummary } from '../types/api'

describe('长引导和计划记录的正文保留', () => {
  const content = `PS C:/workspace> 安装依赖\n${'完整日志与长英文UnbrokenText'.repeat(50)}\n${'后续日志\n'.repeat(100)}日志末尾`

  it('当前引导归档后保留完整换行与末尾内容', () => {
    const { container, rerender } = render(<GuidanceMessage
      user="qa" placement="current"
      item={{ id: 'guide', kind: 'guidance', status: 'accepted', content }}
    />)
    expect(container.querySelector('.guidance-content strong')?.textContent).toBe(content)

    rerender(<GuidanceMessage user="qa" placement="completed"
      item={{ id: 'guide', kind: 'guidance', status: 'completed', content }}
    />)
    expect(container.querySelector('.guidance-content strong')?.textContent).toBe(content)
    expect(screen.getByText('引导成功')).toBeInTheDocument()
    expect(screen.getByText('本轮运行已采用此引导')).toBeInTheDocument()
  })

  it('长引导发送失败后仍可取消和重试，操作不截断正文', () => {
    const onCancel = vi.fn()
    const onRetry = vi.fn()
    const { container } = render(<GuidanceMessage
      user="qa" placement="current"
      item={{ id: 'guide', kind: 'guidance', status: 'next_turn_error', content }}
      onCancel={onCancel} onRetry={onRetry}
    />)

    fireEvent.click(screen.getByRole('button', { name: '重新发送' }))
    fireEvent.click(screen.getByRole('button', { name: '取消' }))
    expect(onRetry).toHaveBeenCalledOnce()
    expect(onCancel).toHaveBeenCalledOnce()
    expect(container.querySelector('.guidance-content strong')?.textContent).toBe(content)
  })

  it('已创建任务计划记录保留完整标题和原跳转操作', () => {
    const plan: PlanSummary = {
      plan_id: 'plan_qa', title: content, description: '', status: 'pending', auto_accept: false,
      reminder: '', source: 'web', session_id: 'qa', current_step: '', revision: 1,
      created_at: '', updated_at: '', progress: { completed: 0, total: 0, percent: 0 }, steps: [],
    }
    const onOpen = vi.fn()
    const { container } = render(<TaskPlanRecord plan={plan} docked onOpen={onOpen} />)
    expect(container.querySelector('.task-plan-record-copy strong')?.textContent).toBe(content)
    fireEvent.click(screen.getByRole('button', { name: '查看当前计划' }))
    expect(onOpen).toHaveBeenCalledOnce()
  })
})
