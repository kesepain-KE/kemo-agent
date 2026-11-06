import { createRef } from 'react'
import { act, fireEvent, render, screen, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { compactUserMessagePreview, UserMessageNavigator, type UserMessageMarker } from './UserMessageNavigator'

const markers: UserMessageMarker[] = [
  { id: 'message-1', content: '# 第一条\n\n请检查运行状态。', integration_number: 4 },
  { id: 'message-2', content: '第二条用户消息', integration_number: 5 },
  { id: 'message-3', content: '第三条用户消息', integration_number: 6 },
]

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

  it('展开时一次挂载全部用户卡片，首尾预选不会替换或丢弃其他轮次', () => {
    const manyMarkers = Array.from({ length: 24 }, (_, index) => ({
      id: `message-${index + 1}`,
      content: `消息 ${index + 1}`,
      integration_number: index + 1,
    }))
    render(
      <UserMessageNavigator
        markers={manyMarkers}
        scrollContainerRef={createRef<HTMLDivElement>()}
        integrationCount={100}
        onNavigate={() => undefined}
      />,
    )
    const rail = screen.getByLabelText('轮次位置：当前预选第 24 轮，共 100 轮')
    fireEvent.pointerEnter(rail)
    const allCards = within(screen.getByRole('group', { name: '对话轮次卡片' })).getAllByRole('button')
    expect(allCards).toHaveLength(24)
    const firstCard = screen.getByRole('button', { name: /第 1 轮：消息 1$/ })
    fireEvent.keyDown(rail, { key: 'Home' })
    expect(firstCard).toHaveAttribute('aria-current', 'true')
    expect(screen.getByRole('progressbar', { name: '当前预选轮次' })).toHaveAttribute('aria-valuemax', '100')
    fireEvent.keyDown(rail, { key: 'End' })
    expect(screen.getByRole('button', { name: /第 24 轮：消息 24/ })).toHaveAttribute('aria-current', 'true')
    expect(within(screen.getByRole('group', { name: '对话轮次卡片' })).getAllByRole('button')).toEqual(allCards)
    fireEvent.keyDown(rail, { key: 'Home' })
    fireEvent.keyDown(rail, { key: 'ArrowUp' })
    expect(firstCard).toHaveAttribute('aria-current', 'true')
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
