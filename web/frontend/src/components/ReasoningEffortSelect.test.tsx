import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ReasoningEffortSelect } from './ReasoningEffortSelect'

afterEach(() => {
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

function openComposer(width: number, left: number) {
  const onChange = vi.fn()
  render(<ReasoningEffortSelect value="medium" onChange={onChange} ariaLabel="思考档位" variant="composer" />)
  const trigger = screen.getByRole('combobox', { name: '思考档位' })
  vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
    width, left, right: left + width, top: 500, bottom: 524, height: 24,
  } as DOMRect)
  fireEvent.click(trigger)
  return { trigger, onChange, popup: screen.getByRole('listbox') }
}

describe('输入坞思考档位弹层宽度', () => {
  it('使用按钮实际宽度且左对齐，选择后原回调及关闭行为不变', () => {
    const { trigger, onChange, popup } = openComposer(146, 90)
    expect(trigger.parentElement).toHaveClass('reasoning-effort-composer')
    expect(trigger.parentElement).not.toHaveClass('composer')
    expect(popup.style.width).toBe('146px')
    expect(popup.style.left).toBe('90px')
    expect(popup.parentElement).toBe(document.body)
    fireEvent.click(screen.getByRole('option', { name: /均衡/ }))
    expect(onChange).toHaveBeenCalledWith('medium')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('窄屏靠右时保留视口边距，不撑出屏幕', () => {
    vi.stubGlobal('innerWidth', 320)
    const { popup } = openComposer(160, 240)
    expect(popup.style.width).toBe('160px')
    expect(popup.style.left).toBe('148px')
  })

  it('按钮或视口尺寸改变时重新测量，不残留原先的固定宽度', () => {
    const { trigger, popup } = openComposer(146, 90)
    vi.mocked(trigger.getBoundingClientRect).mockReturnValue({
      width: 132, left: 40, right: 172, top: 500, bottom: 524, height: 24,
    } as DOMRect)
    act(() => window.dispatchEvent(new Event('resize')))
    expect(popup.style.width).toBe('132px')
    expect(popup.style.left).toBe('40px')
  })
})

describe('顶部模型思考强度弹层', () => {
  function openCompact(top = 200) {
    vi.stubGlobal('innerWidth', 390)
    vi.stubGlobal('innerHeight', 600)
    const onChange = vi.fn()
    const rendered = render(<div className="reference-model-menu" style={{ overflow: 'auto' }}>
      <ReasoningEffortSelect value="medium" onChange={onChange} ariaLabel="顶部模型思考强度" variant="compact" />
    </div>)
    const trigger = screen.getByRole('combobox')
    vi.spyOn(trigger, 'getBoundingClientRect').mockReturnValue({
      width: 250, left: 50, right: 300, top, bottom: top + 32, height: 32,
    } as DOMRect)
    fireEvent.click(trigger)
    return { ...rendered, trigger, onChange, popup: screen.getByRole('listbox') }
  }

  it('移出模型卡的滚动裁切区，两侧各缩4px，选择原样保存并恢复焦点', () => {
    const { trigger, popup, onChange } = openCompact()
    expect(popup.parentElement).toBe(document.body)
    expect(popup).toHaveClass('model-reasoning-popover')
    expect(popup).toHaveStyle({ left: '54px', width: '242px', top: '240px', maxHeight: '348px' })
    fireEvent.click(screen.getByRole('option', { name: /高.*深度推理/ }))
    expect(onChange).toHaveBeenCalledWith('high')
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(trigger).toHaveFocus()
  })

  it('矮屏改向上展开，滚动后跟随按钮，卸载时移除portal', () => {
    const { trigger, popup, unmount } = openCompact(500)
    expect(popup).toHaveStyle({ bottom: '108px', maxHeight: '360px' })
    expect(popup.style.top).toBe('')
    vi.mocked(trigger.getBoundingClientRect).mockReturnValue({
      width: 200, left: 30, right: 230, top: 100, bottom: 132, height: 32,
    } as DOMRect)
    act(() => window.dispatchEvent(new Event('scroll')))
    expect(popup).toHaveStyle({ left: '34px', width: '192px', top: '140px' })
    expect(popup.style.bottom).toBe('')
    unmount()
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
  })

  it('外部点击和Escape关闭，不提交配置', () => {
    const { trigger, onChange } = openCompact()
    fireEvent.pointerDown(document.body)
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    fireEvent.click(trigger)
    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('listbox')).not.toBeInTheDocument()
    expect(onChange).not.toHaveBeenCalled()
  })
})
