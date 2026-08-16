import { fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { CapabilityReferenceDrawer, type CapabilityReferenceItem } from './CapabilityReferenceDrawer'

const items: CapabilityReferenceItem[] = [
  { id: 'expand:user:weather', kind: 'expand', scope: 'user', name: 'weather', title: '天气感知', description: '读取本地天气', path: 'users/demo/expand/weather', status: '已启用' },
  { id: 'expand:shared:calendar', kind: 'expand', scope: 'shared', name: 'calendar', title: '共享日历', description: '查看团队日程', path: 'shared_expand/calendar', status: '已启用' },
  { id: 'expand:global:system_power', kind: 'expand', scope: 'global', name: 'system_power', title: '系统电源', description: '读取电池并控制亮度', path: 'global_expand/system_power', status: '已启用' },
  { id: 'skill:shared:review', kind: 'skill', scope: 'shared', name: 'review', title: '代码审查', description: '审查提交内容', path: 'shared_skills/review', status: '已启用' },
  { id: 'skill:user:release', kind: 'skill', scope: 'user', name: 'release', title: '发布检查', description: '执行用户发布流程', path: 'users/demo/user_skills/release', status: '已启用' },
  { id: 'plugin:global:clock', kind: 'plugin', scope: 'global', name: 'clock', title: 'clock', description: '读取当前时间', path: 'plugins/clock', status: '已启用' },
]

describe('CapabilityReferenceDrawer', () => {
  it('关闭时保留抽屉节点并通过 show 类驱动过渡', () => {
    const props = { items, onClose: vi.fn(), onReference: vi.fn() }
    const { rerender } = render(<CapabilityReferenceDrawer open={false} {...props} />)
    const drawer = screen.getByRole('dialog', { hidden: true })
    expect(drawer).toHaveAttribute('aria-label', '能力引用')
    expect(drawer).not.toHaveClass('show')
    expect(drawer).toHaveAttribute('aria-hidden', 'true')
    expect(drawer).toHaveAttribute('inert')

    rerender(<CapabilityReferenceDrawer open {...props} />)
    expect(screen.getByRole('dialog', { name: '能力引用' })).toHaveClass('show')
    expect(drawer).toHaveAttribute('aria-hidden', 'false')
    expect(drawer).not.toHaveAttribute('inert')
  })

  it('按能力类型动态切换层级按钮并引用技能和插件', () => {
    const onReference = vi.fn()
    render(<CapabilityReferenceDrawer open items={items} onClose={() => undefined} onReference={onReference} />)
    const drawer = screen.getByRole('dialog', { name: '能力引用' })

    expect(within(drawer).getByRole('navigation', { name: '拓展层级' })).toHaveTextContent('全部全局共享用户')
    fireEvent.click(within(drawer).getByRole('tab', { name: '技能' }))
    const skillScopes = within(drawer).getByRole('navigation', { name: '技能层级' })
    expect(skillScopes).toHaveTextContent('全部共享用户')
    expect(within(skillScopes).queryByRole('button', { name: '全局' })).not.toBeInTheDocument()
    fireEvent.click(within(skillScopes).getByRole('button', { name: '用户' }))
    expect(within(drawer).getByText('发布检查')).toBeInTheDocument()
    expect(within(drawer).queryByText('代码审查')).not.toBeInTheDocument()
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 发布检查' }))
    expect(onReference).toHaveBeenCalledWith(items[4])

    fireEvent.click(within(drawer).getByRole('tab', { name: '插件' }))
    const pluginScopes = within(drawer).getByRole('navigation', { name: '插件层级' })
    expect(within(pluginScopes).getAllByRole('button')).toHaveLength(1)
    expect(within(pluginScopes).getByRole('button', { name: '全局' })).toHaveAttribute('aria-pressed', 'true')
    fireEvent.change(within(drawer).getByRole('textbox', { name: '搜索插件' }), { target: { value: '当前时间' } })
    fireEvent.click(within(drawer).getByRole('button', { name: '引用 clock' }))
    expect(onReference).toHaveBeenCalledWith(items[5])
  })

  it('只显示当前能力清单中实际存在的层级', () => {
    const sharedOnly = items.filter((item) => item.kind === 'expand' && item.scope === 'shared')
    render(<CapabilityReferenceDrawer open items={sharedOnly} onClose={() => undefined} onReference={() => undefined} />)
    const drawer = screen.getByRole('dialog', { name: '能力引用' })
    const scopes = within(drawer).getByRole('navigation', { name: '拓展层级' })
    expect(scopes).toHaveTextContent('共享')
    expect(within(scopes).queryByRole('button', { name: '全局' })).not.toBeInTheDocument()
    expect(within(scopes).queryByRole('button', { name: '用户' })).not.toBeInTheDocument()

    fireEvent.click(within(drawer).getByRole('tab', { name: '技能' }))
    expect(within(drawer).queryByRole('navigation', { name: '技能层级' })).not.toBeInTheDocument()
  })
})
