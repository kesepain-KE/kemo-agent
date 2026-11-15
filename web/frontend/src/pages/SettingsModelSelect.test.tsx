import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ModelSelectField } from './SettingsModelSelect'

const models = Array.from({ length: 40 }, (_, index) => ({
  id: `model-${index}`, provider_id: 'gateway', provider_model: `model-${index}`, source: 'kemo' as const,
}))

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function openPicker(top = 520) {
  vi.stubGlobal('innerWidth', 390)
  vi.stubGlobal('innerHeight', 600)
  const onChange = vi.fn()
  render(<ModelSelectField label="视频生成" value="model-0" placeholder="未指定" models={models} enabled onChange={onChange} />)
  const trigger = screen.getByRole('button', { name: '视频生成选择模型' })
  const anchor = trigger.closest('.model-select-wrap') as HTMLElement
  const rect = vi.spyOn(anchor, 'getBoundingClientRect').mockReturnValue({
    left: 18, right: 378, width: 360, top, bottom: top + 48, height: 48,
  } as DOMRect)
  fireEvent.click(trigger)
  return { trigger, rect, onChange, popup: screen.getByRole('dialog') }
}

describe('设置页模型选择弹层的视口边界', () => {
  it.each([
    { top: 50, expectedTop: '106px', expectedBottom: 'auto' },
    { top: 520, expectedTop: 'auto', expectedBottom: '88px' },
  ])('锚点位于 $top 时仅使用一个垂直定位边，避免继承 CSS 的向下偏移', ({ top, expectedTop, expectedBottom }) => {
    const { popup } = openPicker(top)
    expect(popup.parentElement).toBe(document.body)
    expect(popup).toHaveStyle({ position: 'fixed', top: expectedTop, bottom: expectedBottom, maxHeight: '430px' })
    expect(screen.getAllByRole('option')).toHaveLength(40)
    expect(screen.getByRole('textbox', { name: '视频生成筛选' })).toHaveFocus()
  })

  it('打开后缩窄视口和滚动时重新定位，清除上一方向的边约束', () => {
    const { rect, popup } = openPicker()
    vi.stubGlobal('innerWidth', 240)
    rect.mockReturnValue({ left: 12, right: 232, width: 220, top: 50, bottom: 98, height: 48 } as DOMRect)
    act(() => window.dispatchEvent(new Event('resize')))
    expect(popup).toHaveStyle({ left: '12px', width: '216px', top: '106px', bottom: 'auto' })
    rect.mockReturnValue({ left: 12, right: 232, width: 220, top: 300, bottom: 348, height: 48 } as DOMRect)
    act(() => window.dispatchEvent(new Event('scroll')))
    expect(popup).toHaveStyle({ top: 'auto', bottom: '308px', maxHeight: '280px' })
    expect(screen.getByRole('dialog')).toBe(popup)
  })

  it('末尾选项可用键盘访问和选择，筛选、关闭及焦点恢复保持可用', () => {
    const { trigger, onChange } = openPicker()
    const search = screen.getByRole('textbox', { name: '视频生成筛选' })
    fireEvent.keyDown(search, { key: 'ArrowDown' })
    fireEvent.keyDown(document.activeElement!, { key: 'End' })
    expect(screen.getByRole('option', { name: 'model-39 gateway' })).toHaveFocus()
    fireEvent.click(document.activeElement!)
    expect(onChange).toHaveBeenCalledWith('model-39')
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    fireEvent.click(trigger)
    fireEvent.change(screen.getByRole('textbox', { name: '视频生成筛选' }), { target: { value: 'model-39' } })
    expect(screen.getAllByRole('option')).toHaveLength(1)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
    fireEvent.click(trigger)
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
  })
})
