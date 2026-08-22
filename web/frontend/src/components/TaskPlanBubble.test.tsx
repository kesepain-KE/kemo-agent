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
    const onPause = vi.fn()
    const confirm = vi.spyOn(window, 'confirm')
    confirm.mockReturnValueOnce(false).mockReturnValueOnce(true)
    render(<TaskPlanBubble title="运行计划" status="running" steps={[{ id: 'step_1', title: '完成项', status: 'completed' }, { id: 'step_2', title: '执行项', status: 'running' }]} onPause={onPause} />)
    expect(screen.getByText('执行进度 1/2')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('正在执行：执行项')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /停止任务/ })).not.toBeInTheDocument()
    const pauseButton = screen.getByRole('button', { name: /暂停任务/ })
    fireEvent.click(pauseButton)
    expect(onPause).not.toHaveBeenCalled()
    fireEvent.click(pauseButton)
    expect(onPause).toHaveBeenCalledOnce()
    expect(confirm).toHaveBeenCalledWith('确定要暂停此任务吗？任务将在当前步骤结束后暂停。')
    confirm.mockRestore()
  })

  it('长计划只让步骤区域滚动并保持运行提示位于卡片内', () => {
    render(<TaskPlanBubble
      title="长计划"
      status="running"
      steps={Array.from({ length: 12 }, (_, index) => ({
        id: `step_${index + 1}`,
        title: `步骤 ${index + 1}`,
        status: index < 10 ? 'completed' : index === 10 ? 'running' : 'pending',
      }))}
    />)

    const plan = screen.getByRole('region', { name: '任务计划：长计划' })
    const stepList = screen.getByRole('list', { name: '任务计划步骤' })
    expect(plan.contains(stepList)).toBe(true)
    expect(screen.getAllByRole('listitem')).toHaveLength(12)
    expect(plan.contains(screen.getByText('正在执行：步骤 11'))).toBe(true)
  })

  it('暂停后允许修改并继续执行剩余步骤', () => {
    const onModify = vi.fn()
    const onRetry = vi.fn()
    render(<TaskPlanBubble
      title="暂停计划"
      status="paused"
      steps={[{ id: 'step_1', title: '完成项', status: 'completed' }, { id: 'step_2', title: '待执行项', status: 'pending' }]}
      onModify={onModify}
      onRetry={onRetry}
    />)
    expect(screen.getByText('任务计划已暂停。修改计划后可继续执行剩余步骤。')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '修改计划' }))
    fireEvent.click(screen.getByRole('button', { name: '继续执行' }))
    expect(onModify).toHaveBeenCalledOnce()
    expect(onRetry).toHaveBeenCalledOnce()
  })

  it('暂停或失败计划优先重试具体失败步骤', () => {
    const onRetry = vi.fn()
    const onRetryStep = vi.fn()
    render(<TaskPlanBubble
      title="失败计划"
      status="paused"
      steps={[{ id: 'step_1', title: '失败项', status: 'failed' }, { id: 'step_2', title: '待执行项', status: 'pending' }]}
      onRetry={onRetry}
      onRetryStep={onRetryStep}
    />)
    fireEvent.click(screen.getByRole('button', { name: '重试步骤' }))
    expect(onRetryStep).toHaveBeenCalledWith('step_1')
    expect(onRetry).not.toHaveBeenCalled()
    expect(screen.queryByRole('button', { name: '继续执行' })).not.toBeInTheDocument()
    expect(screen.getByText('任务计划已暂停。重试会先重置失败步骤；是否自动继续由“修正后自动激活”配置决定。')).toBeInTheDocument()
  })

  it('重试完成后显示后端返回的激活结果', () => {
    render(<TaskPlanBubble
      title="失败计划"
      status="approved"
      activationNotice="失败步骤已重置，计划已自动恢复执行。"
      steps={[{ id: 'step_1', title: '待重试项', status: 'pending' }]}
    />)
    expect(screen.getByText('失败步骤已重置，计划已自动恢复执行。')).toBeInTheDocument()
  })
})
