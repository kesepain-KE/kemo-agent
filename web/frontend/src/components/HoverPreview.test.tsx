import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { HoverPreview } from './HoverPreview'

function setOverflow(element: HTMLElement, overflow: boolean) {
  Object.defineProperty(element, 'clientHeight', { configurable: true, value: 40 })
  Object.defineProperty(element, 'scrollHeight', { configurable: true, value: overflow ? 100 : 40 })
  vi.spyOn(element, 'getBoundingClientRect').mockReturnValue({
    x: 20, y: 20, left: 20, top: 20, right: 220, bottom: 60,
    width: 200, height: 40, toJSON: () => ({}),
  })
}

describe('HoverPreview', () => {
  beforeEach(() => vi.useFakeTimers())
  afterEach(() => {
    vi.runOnlyPendingTimers()
    vi.useRealTimers()
    vi.restoreAllMocks()
  })

  it('只在内容溢出时延迟显示，并在指针离开后关闭', () => {
    render(<HoverPreview preview={<span>完整内容</span>} ariaLabel="状态容器"><div>摘要</div></HoverPreview>)
    const host = screen.getByLabelText('状态容器')
    setOverflow(host, true)
    fireEvent.pointerEnter(host)
    act(() => vi.advanceTimersByTime(199))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1))
    const tooltip = screen.getByRole('tooltip')
    expect(tooltip).toHaveTextContent('完整内容')
    fireEvent.pointerLeave(host)
    fireEvent.pointerEnter(tooltip)
    act(() => vi.advanceTimersByTime(100))
    expect(screen.getByRole('tooltip')).toBeInTheDocument()
    fireEvent.pointerLeave(tooltip)
    act(() => vi.advanceTimersByTime(80))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('未溢出不显示，键盘 focus 可触发溢出预览', () => {
    render(<HoverPreview preview="完整内容" ariaLabel="配置容器"><div>摘要</div></HoverPreview>)
    const host = screen.getByLabelText('配置容器')
    setOverflow(host, false)
    fireEvent.pointerEnter(host)
    act(() => vi.advanceTimersByTime(200))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()

    setOverflow(host, true)
    fireEvent.focus(host)
    act(() => vi.advanceTimersByTime(200))
    expect(screen.getByRole('tooltip')).toBeInTheDocument()
    fireEvent.blur(host)
    act(() => vi.advanceTimersByTime(80))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('卸载时清理尚未触发的定时器', () => {
    const view = render(<HoverPreview preview="完整内容" ariaLabel="操作容器"><div>摘要</div></HoverPreview>)
    const host = screen.getByLabelText('操作容器')
    setOverflow(host, true)
    fireEvent.pointerEnter(host)
    view.unmount()
    act(() => vi.advanceTimersByTime(250))
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })
})
