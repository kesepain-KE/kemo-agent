import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ExpandModuleSummary } from '../types/api'
import { ExpandReferenceDrawer } from './ExpandReferenceDrawer'

const modules = [
  { id: 'user:weather', scope: 'user', name: 'weather', display_name: '天气感知', description: '读取本地天气', path: 'users/demo/expand/weather', relative_path: 'weather', valid: true, whitelisted: true, active_for_main_agent: true },
  { id: 'shared:calendar', scope: 'shared', name: 'calendar', display_name: '共享日历', description: '查看团队日程', path: 'shared_expand/calendar', relative_path: 'calendar', valid: true, whitelisted: true, active_for_main_agent: true },
  { id: 'global:system_power', scope: 'global', name: 'system_power', display_name: '系统电源', description: '读取电池并控制亮度', path: 'global_expand/system_power', relative_path: 'system_power', valid: true, whitelisted: true, active_for_main_agent: true },
] as ExpandModuleSummary[]

describe('ExpandReferenceDrawer', () => {
  it('关闭时保留抽屉节点并通过 show 类驱动过渡', () => {
    const props = { modules, onClose: vi.fn(), onReference: vi.fn() }
    const { rerender } = render(<ExpandReferenceDrawer open={false} {...props} />)
    const drawer = screen.getByRole('dialog', { hidden: true })
    expect(drawer).toHaveAttribute('aria-label', '拓展引用')

    expect(drawer).not.toHaveClass('show')
    expect(drawer).toHaveAttribute('aria-hidden', 'true')
    expect(drawer).toHaveAttribute('inert')

    rerender(<ExpandReferenceDrawer open {...props} />)
    expect(screen.getByRole('dialog', { name: '拓展引用' })).toHaveClass('show')
    expect(drawer).toHaveAttribute('aria-hidden', 'false')
    expect(drawer).not.toHaveAttribute('inert')
  })

  it('按层级和搜索词过滤拓展卡片并触发引用', () => {
    const onReference = vi.fn()
    render(<ExpandReferenceDrawer open modules={modules} onClose={() => undefined} onReference={onReference} />)
    const drawer = screen.getByRole('dialog', { name: '拓展引用' })

    fireEvent.click(within(drawer).getByRole('button', { name: '共享' }))
    expect(within(drawer).getByText('共享日历')).toBeInTheDocument()
    expect(within(drawer).queryByText('天气感知')).not.toBeInTheDocument()

    fireEvent.click(within(drawer).getByRole('button', { name: '全部' }))
    fireEvent.change(within(drawer).getByRole('textbox', { name: '搜索拓展' }), { target: { value: '控制亮度' } })
    expect(within(drawer).getByText('系统电源')).toBeInTheDocument()
    expect(within(drawer).queryByText('共享日历')).not.toBeInTheDocument()

    fireEvent.click(within(drawer).getByRole('button', { name: '引用 系统电源' }))
    expect(onReference).toHaveBeenCalledWith(modules[2])
  })
})
