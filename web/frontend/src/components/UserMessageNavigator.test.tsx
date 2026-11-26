import { createRef } from 'react'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { compactUserMessagePreview, UserMessageNavigator, type UserMessageMarker } from './UserMessageNavigator'

const markers: UserMessageMarker[] = [
  { id: 'message-1', content: '# 第一条\n\n请检查运行状态。', integration_number: 4 },
  { id: 'message-2', content: '第二条用户消息', integration_number: 5 },
  { id: 'message-3', content: '第三条用户消息', integration_number: 6 },
]
const manyMarkers: UserMessageMarker[] = Array.from({ length: 50 }, (_, index) => ({
  id: `message-${index + 1}`,
  content: `消息 ${index + 1}`,
  integration_number: index + 1,
}))

describe('UserMessageNavigator', () => {
  it('把当前悬停轮次旋转到 180° 并触发对应轮次跳转', () => {
    const onNavigate = vi.fn()
    const scrollRef = createRef<HTMLDivElement>()
    const { container } = render(
      <div>
        <div ref={scrollRef}>
          <div className="messages">
            <article data-user-message-id="message-1" />
            <article data-user-message-id="message-2" />
          </div>
        </div>
        <UserMessageNavigator markers={markers} scrollContainerRef={scrollRef} integrationCount={10} onNavigate={onNavigate} />
      </div>,
    )

    expect(screen.getByRole('navigation', { name: '用户消息导航' })).toBeInTheDocument()
    const rail = screen.getByLabelText('轮次位置：当前预选第 6 轮，共 10 轮')
    fireEvent.pointerEnter(rail)
    const wheel = screen.getByLabelText('对话轮次卡片')
    expect(wheel).toHaveAttribute('aria-hidden', 'false')
    expect(container.querySelector('.user-message-wheel-arc')).not.toBeInTheDocument()
    expect(container.querySelector('.user-message-wheel-node')).not.toBeInTheDocument()
    const firstCard = screen.getByRole('button', { name: /第 4 轮：第一条 请检查运行状态/ })
    const middleCard = screen.getByRole('button', { name: /第 5 轮：第二条用户消息/ })
    const lastCard = screen.getByRole('button', { name: /第 6 轮：第三条用户消息/ })
    fireEvent.pointerEnter(firstCard)
    expect(screen.getByRole('progressbar', { name: '当前预选轮次' })).toHaveAttribute('aria-valuenow', '4')
    expect(screen.getByRole('progressbar', { name: '当前预选轮次' })).toHaveAttribute('aria-valuemax', '10')
    expect(firstCard).toHaveAttribute('data-wheel-angle', '180')
    expect(middleCard).toHaveAttribute('data-wheel-angle', '225')
    expect(lastCard).toHaveAttribute('data-wheel-angle', '270')
    expect(firstCard).toHaveAttribute('aria-current', 'true')
    fireEvent.pointerEnter(middleCard)
    expect(firstCard).toHaveAttribute('data-wheel-angle', '90')
    expect(middleCard).toHaveAttribute('data-wheel-angle', '180')
    expect(lastCard).toHaveAttribute('data-wheel-angle', '270')
    expect(middleCard).toHaveAttribute('aria-current', 'true')
    fireEvent.pointerEnter(lastCard)
    expect(firstCard).toHaveAttribute('data-wheel-angle', '90')
    expect(middleCard).toHaveAttribute('data-wheel-angle', '135')
    expect(lastCard).toHaveAttribute('data-wheel-angle', '180')
    expect(lastCard).toHaveAttribute('aria-current', 'true')
    fireEvent.pointerEnter(middleCard)
    fireEvent.pointerDown(middleCard, { button: 0 })
    fireEvent.click(middleCard, { detail: 1 })
    expect(onNavigate).toHaveBeenCalledWith('message-2')
    expect(onNavigate).toHaveBeenCalledTimes(1)
  })

  it('鼠标滚轮和方向键能够切换当前预览轮次', () => {
    render(
      <UserMessageNavigator
        markers={markers}
        scrollContainerRef={createRef<HTMLDivElement>()}
        onNavigate={() => undefined}
      />,
    )
    fireEvent.pointerEnter(screen.getByLabelText(/轮次位置/))
    const firstCard = screen.getByRole('button', { name: /第 4 轮：第一条 请检查运行状态/ })
    fireEvent.pointerEnter(firstCard)
    fireEvent.wheel(screen.getByRole('navigation', { name: '用户消息导航' }), { deltaY: 120 })
    const middleCard = screen.getByRole('button', { name: /第 5 轮：第二条用户消息/ })
    expect(middleCard).toHaveAttribute('aria-current', 'true')
    fireEvent.keyDown(middleCard, { key: 'ArrowDown' })
    expect(screen.getByRole('button', { name: /第 6 轮：第三条用户消息/ })).toHaveAttribute('aria-current', 'true')
  })

  it('展开时只挂载首尾各自的窗口，Home/End 仍可预选全局首尾', () => {
    const onNavigate = vi.fn()
    render(
      <UserMessageNavigator
        markers={manyMarkers}
        scrollContainerRef={createRef<HTMLDivElement>()}
        integrationCount={100}
        onNavigate={onNavigate}
      />,
    )
    const rail = screen.getByLabelText('轮次位置：当前预选第 50 轮，共 100 轮')
    fireEvent.pointerEnter(rail)
    const wheel = within(screen.getByRole('group', { name: '对话轮次卡片' }))
    // 需求变更：完整轮次索引留在内存，DOM 只挂载预选轮次前后各十张卡片。
    expect(wheel.getAllByRole('button')).toHaveLength(11)
    expect(wheel.getByRole('button', { name: '第 40 轮：消息 40' })).toBeInTheDocument()
    expect(wheel.queryByRole('button', { name: '第 39 轮：消息 39' })).not.toBeInTheDocument()
    fireEvent.keyDown(rail, { key: 'Home' })
    const firstCard = wheel.getByRole('button', { name: '第 1 轮：消息 1' })
    expect(firstCard).toHaveAttribute('aria-current', 'true')
    expect(wheel.getAllByRole('button')).toHaveLength(11)
    expect(wheel.getByRole('button', { name: '第 11 轮：消息 11' })).toBeInTheDocument()
    expect(wheel.queryByRole('button', { name: '第 12 轮：消息 12' })).not.toBeInTheDocument()
    expect(screen.getByRole('progressbar', { name: '当前预选轮次' })).toHaveAttribute('aria-valuemax', '100')
    fireEvent.keyDown(rail, { key: 'ArrowUp' })
    expect(firstCard).toHaveAttribute('aria-current', 'true')

    fireEvent.keyDown(rail, { key: 'End' })
    fireEvent.keyDown(rail, { key: 'ArrowDown' })
    expect(wheel.getByRole('button', { name: '第 50 轮：消息 50' })).toHaveAttribute('aria-current', 'true')
    expect(wheel.getAllByRole('button')).toHaveLength(11)
    expect(firstCard).not.toBeInTheDocument()
    expect(onNavigate).not.toHaveBeenCalled()
    fireEvent.keyDown(rail, { key: 'Enter' })
    expect(onNavigate).toHaveBeenCalledExactlyOnceWith('message-50')
  })

  it('窗口随滚轮从中间轮次平移到末尾，卡片角度仍以完整索引计算', () => {
    vi.useFakeTimers()
    try {
      const onNavigate = vi.fn()
      render(<UserMessageNavigator markers={manyMarkers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={onNavigate} />)
      fireEvent.pointerEnter(screen.getByLabelText(/轮次位置/))
      const nav = screen.getByRole('navigation', { name: '用户消息导航' })
      const wheel = within(screen.getByRole('group', { name: '对话轮次卡片' }))
      const card = (round: number) => wheel.getByRole('button', { name: `第 ${round} 轮：消息 ${round}` })
      for (const round of [40, 30, 25]) fireEvent.pointerEnter(card(round))

      expect(wheel.getAllByRole('button')).toHaveLength(21)
      expect(card(25)).toHaveAttribute('aria-current', 'true')
      expect(card(25)).toHaveAttribute('data-wheel-angle', '180')
      expect(card(24)).toHaveAttribute('data-wheel-angle', '176')
      expect(card(26)).toHaveAttribute('data-wheel-angle', '184')
      expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuemax', '50')

      for (let round = 26; round <= 50; round += 1) {
        vi.advanceTimersByTime(100)
        fireEvent.wheel(nav, { deltaY: 120 })
        expect(card(round)).toHaveAttribute('aria-current', 'true')
        expect(wheel.getAllByRole('button')).toHaveLength(Math.min(21, 61 - round))
      }
      expect(card(50)).toHaveAttribute('data-wheel-angle', '180')
      expect(wheel.queryByRole('button', { name: '第 25 轮：消息 25' })).not.toBeInTheDocument()
      expect(onNavigate).not.toHaveBeenCalled()
    } finally {
      vi.useRealTimers()
    }
  })

  it('只有五条消息时全部挂载', () => {
    render(<UserMessageNavigator markers={manyMarkers.slice(0, 5)} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={vi.fn()} />)
    fireEvent.click(screen.getByLabelText(/轮次位置/))
    const wheel = within(screen.getByRole('group', { name: '对话轮次卡片' }))
    expect(wheel.getAllByRole('button')).toHaveLength(5)
    expect(wheel.getByRole('button', { name: '第 1 轮：消息 1' })).toBeInTheDocument()
    expect(wheel.getByRole('button', { name: '第 5 轮：消息 5' })).toHaveAttribute('aria-current', 'true')
  })

  it('同一条消息在窗口内移动或移出后重新挂载只压缩一次预览', () => {
    const content = '```text\n需要缓存的预览\n```'
    const cachedMarkers = [...manyMarkers.slice(0, -1), { ...manyMarkers[49], content }]
    const replace = vi.spyOn(String.prototype, 'replace')
    try {
      render(<UserMessageNavigator markers={cachedMarkers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={vi.fn()} />)
      const rail = screen.getByLabelText(/轮次位置/)
      const name = '第 50 轮：需要缓存的预览'
      fireEvent.click(rail)
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-current', 'true')
      fireEvent.keyDown(rail, { key: 'ArrowUp' })
      expect(screen.getByRole('button', { name })).not.toHaveAttribute('aria-current')
      fireEvent.keyDown(rail, { key: 'Home' })
      expect(screen.queryByRole('button', { name })).not.toBeInTheDocument()
      fireEvent.keyDown(rail, { key: 'End' })
      expect(screen.getByRole('button', { name })).toHaveAttribute('aria-current', 'true')
      // 第一条 replace 就会去掉代码围栏；以原文为接收者的调用数即为压缩次数。
      expect(replace.mock.contexts.filter((value) => String(value) === content)).toHaveLength(1)
    } finally {
      replace.mockRestore()
    }
  })

  it('空会话隐藏，首条用户发言出现后即可展开预览并定位', () => {
    const onNavigate = vi.fn()
    const scrollRef = createRef<HTMLDivElement>()
    const { container, rerender } = render(
      <UserMessageNavigator markers={[]} scrollContainerRef={scrollRef} onNavigate={onNavigate} />,
    )
    expect(container).toBeEmptyDOMElement()

    rerender(
      <UserMessageNavigator
        markers={markers.slice(0, 1)}
        scrollContainerRef={scrollRef}
        onNavigate={onNavigate}
      />,
    )
    const rail = screen.getByRole('button', { name: '轮次位置：当前预选第 4 轮，共 4 轮' })
    fireEvent.click(rail)
    const card = screen.getByRole('button', { name: /第 4 轮：第一条 请检查运行状态/ })
    expect(card).toHaveAttribute('data-wheel-angle', '180')
    fireEvent.click(card)
    expect(onNavigate).toHaveBeenCalledExactlyOnceWith('message-1')
    expect(compactUserMessagePreview('**标题**\n\n[链接](https://example.com) 后续内容', 8)).toBe('标题 链接 后续…')
  })

  it('点击轨道可展开轮次，关闭状态不会留下卡片或正文状态文字', () => {
    render(<UserMessageNavigator markers={markers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={vi.fn()} />)
    const rail = screen.getByRole('button', { name: /轮次位置/ })
    expect(rail).toHaveAttribute('aria-expanded', 'false')
    expect(screen.queryByRole('group', { name: '对话轮次卡片' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /第 4 轮：/ })).not.toBeInTheDocument()
    fireEvent.click(rail)
    expect(rail).toHaveAttribute('aria-expanded', 'true')
    expect(screen.getByRole('group', { name: '对话轮次卡片' })).toHaveAttribute('id', rail.getAttribute('aria-controls'))
    expect(screen.getByText('点击定位这一轮')).toBeInTheDocument()
  })

  it('轨道支持方向键、Home/End 和 Enter 定位，Escape 关闭并恢复轨道焦点', () => {
    const onNavigate = vi.fn()
    render(<UserMessageNavigator markers={markers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={onNavigate} />)
    const rail = screen.getByRole('button', { name: /轮次位置/ })
    fireEvent.focus(rail)
    fireEvent.keyDown(rail, { key: 'Home' })
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '4')
    fireEvent.keyDown(rail, { key: 'ArrowDown' })
    fireEvent.keyDown(rail, { key: 'Enter' })
    expect(onNavigate).toHaveBeenCalledExactlyOnceWith('message-2')
    fireEvent.keyDown(rail, { key: 'End' })
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '6')
    fireEvent.keyDown(rail, { key: 'Escape' })
    expect(rail).toHaveFocus()
    expect(rail).toHaveAttribute('aria-expanded', 'false')
  })

  it('点击外部关闭，重新打开不会保留旧预选轮次', () => {
    render(<UserMessageNavigator markers={markers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={vi.fn()} />)
    const rail = screen.getByRole('button', { name: /轮次位置/ })
    fireEvent.click(rail)
    fireEvent.keyDown(rail, { key: 'Home' })
    fireEvent.pointerDown(document.body)
    expect(rail).toHaveAttribute('aria-expanded', 'false')
    fireEvent.click(rail)
    expect(screen.getByRole('progressbar')).toHaveAttribute('aria-valuenow', '6')
  })

  it('卡片采用可缩放百分比弧线，激活同一轮只触发一次跳转', () => {
    const onNavigate = vi.fn()
    render(<UserMessageNavigator markers={markers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={onNavigate} />)
    fireEvent.click(screen.getByRole('button', { name: /轮次位置/ }))
    const card = screen.getByRole('button', { name: /第 5 轮：第二条/ })
    expect(card.style.getPropertyValue('--wheel-x')).toMatch(/%$/)
    expect(card.style.getPropertyValue('--wheel-y')).toMatch(/%$/)
    fireEvent.pointerDown(card, { button: 0 })
    fireEvent.click(card, { detail: 1 })
    expect(onNavigate).toHaveBeenCalledExactlyOnceWith('message-2')
  })

  it('触屏点击展开后抬手不会触发鼠标离开倒计时而立即关闭', () => {
    vi.useFakeTimers()
    try {
      render(<UserMessageNavigator markers={markers} scrollContainerRef={createRef<HTMLDivElement>()} onNavigate={vi.fn()} />)
      const rail = screen.getByRole('button', { name: /轮次位置/ })
      const over = new Event('pointerover', { bubbles: true })
      Object.defineProperty(over, 'pointerType', { value: 'touch' })
      fireEvent(rail, over)
      expect(rail).toHaveAttribute('aria-expanded', 'false')
      fireEvent.click(rail)
      const out = new Event('pointerout', { bubbles: true })
      Object.defineProperties(out, { pointerType: { value: 'touch' }, relatedTarget: { value: document.body } })
      fireEvent(rail, out)
      act(() => { vi.advanceTimersByTime(250) })
      expect(rail).toHaveAttribute('aria-expanded', 'true')
    } finally {
      vi.useRealTimers()
    }
  })
})
