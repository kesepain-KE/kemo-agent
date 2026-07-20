import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RecentActivityCard } from './RecentActivityCard'

describe('RecentActivityCard', () => {
  it('渲染真实任务和感知摘要', () => {
    render(<RecentActivityCard
      scheduledTasks={[{ id: 't1', title: '每日检查', schedule: '每天 18:00', nextRun: '07/20 18:00', enabled: true }]}
      senseData={[{ id: 's1', name: '运行时感知', value: 'CPU 23%', updateInterval: '', updatedAt: '07/20 17:49', injected: true }]}
    />)
    expect(screen.getByText('每日检查')).toBeInTheDocument()
    expect(screen.getByText('CPU 23%')).toBeInTheDocument()
    expect(screen.getByText('频率未声明')).toBeInTheDocument()
    expect(screen.getByText('已注入')).toBeInTheDocument()
  })

  it('没有数据时分别显示明确空状态', () => {
    render(<RecentActivityCard scheduledTasks={[]} senseData={[]} />)
    expect(screen.getByText('当前没有已配置的用户定时任务')).toBeInTheDocument()
    expect(screen.getByText('当前没有正在注入的感知数据')).toBeInTheDocument()
  })
})
