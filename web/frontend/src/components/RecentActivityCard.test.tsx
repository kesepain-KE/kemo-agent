import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RecentActivityCard } from './RecentActivityCard'

describe('RecentActivityCard', () => {
  it('渲染真实任务和感知摘要', () => {
    render(<RecentActivityCard
      scheduledTasks={[
        { id: 't1', title: '每日检查', schedule: '每天 18:00', nextRun: '07/20 18:00', status: 'enabled' },
        { id: 't2', title: '单次报告', schedule: '单次 · —', nextRun: '—', status: 'completed' },
      ]}
      senseData={[{ id: 's1', name: '运行时感知', value: 'CPU 23%', updateInterval: '', updatedAt: '07/20 17:49', injected: true }]}
    />)
    expect(screen.getByText('每日检查')).toBeInTheDocument()
    expect(screen.getByText('单次报告')).toBeInTheDocument()
    expect(screen.getByText('已完成')).toBeInTheDocument()
    expect(screen.getByText('CPU 23%')).toBeInTheDocument()
    expect(screen.getByText('频率未声明')).toBeInTheDocument()
    expect(screen.getByText('已注入')).toBeInTheDocument()
  })

  it('没有数据时分别显示明确空状态', () => {
    render(<RecentActivityCard scheduledTasks={[]} senseData={[]} />)
    expect(screen.getByText('当前没有已配置的用户定时任务')).toBeInTheDocument()
    expect(screen.getByText('当前没有正在注入的感知数据')).toBeInTheDocument()
  })

  it('定时任务和感知数据各最多显示三个且合计最多六个', () => {
    render(<RecentActivityCard
      scheduledTasks={Array.from({ length: 4 }, (_, index) => ({
        id: `t${index + 1}`,
        title: `定时任务 ${index + 1}`,
        schedule: '每天 18:00',
        nextRun: '09/26 18:00',
        status: 'enabled' as const,
      }))}
      senseData={Array.from({ length: 4 }, (_, index) => ({
        id: `s${index + 1}`,
        name: `感知数据 ${index + 1}`,
        value: `${index + 1}`,
        updateInterval: '每 5 分钟',
        updatedAt: '09/25 22:00',
        injected: true,
      }))}
      maxTaskItems={10}
      maxSenseItems={10}
    />)

    expect(screen.getByText('定时任务 3')).toBeInTheDocument()
    expect(screen.queryByText('定时任务 4')).not.toBeInTheDocument()
    expect(screen.getByText('感知数据 3')).toBeInTheDocument()
    expect(screen.queryByText('感知数据 4')).not.toBeInTheDocument()
    expect(screen.getAllByRole('button')).toHaveLength(6)
  })
})
