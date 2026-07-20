import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { TaskPlanBubble } from './TaskPlanBubble'

describe('TaskPlanBubble', () => {
  it('展示待批准计划并触发三个操作', () => {
    const onReject = vi.fn()
    const onModify = vi.fn()
    const onApprove = vi.fn()
    render(<TaskPlanBubble
      title="测试任务计划"
      status="pending"
      autoAccept={false}
      steps={[{ id: 'step_1', title: '获取当前时间', status: 'pending' }, { id: 'step_2', title: '检查目录', dependency: 'step_1', status: 'pending' }]}
      onReject={onReject}
      onModify={onModify}
      onApprove={onApprove}
    />)
    expect(screen.getByText('等待批准')).toBeInTheDocument()
    expect(screen.getByText('步骤数：2 步')).toBeInTheDocument()
    expect(screen.getByText('依赖 step_1')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: /拒绝/ }))
    fireEvent.click(screen.getByRole('button', { name: /修改/ }))
    fireEvent.click(screen.getByRole('button', { name: /批准执行/ }))
    expect(onReject).toHaveBeenCalledOnce()
    expect(onModify).toHaveBeenCalledOnce()
    expect(onApprove).toHaveBeenCalledOnce()
  })

  it('运行状态显示当前进度', () => {
    render(<TaskPlanBubble title="运行计划" status="running" steps={[{ id: 'step_1', title: '完成项', status: 'completed' }, { id: 'step_2', title: '执行项', status: 'running' }]} />)
    expect(screen.getByText('执行进度 1/2')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('正在执行：执行项')).toBeInTheDocument()
  })
})
