import { createRef, useState } from 'react'
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { compactUserMessagePreview, UserMessageNavigator, type UserMessageMarker } from './UserMessageNavigator'

const markers: UserMessageMarker[] = [
  { id: 'message-1', content: '# 第一条\n\n请检查运行状态。', round: 4 },
  { id: 'message-2', content: '第二条用户消息', round: 5 },
  { id: 'message-3', content: '第三条用户消息', round: 6 },
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
        <UserMessageNavigator markers={markers} scrollContainerRef={scrollRef} totalRounds={10} onNavigate={onNavigate} />
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

  it('全局短进度条只记录预选轮次，滚轮仍保留当前附近十条消息', () => {
    const manyMarkers = Array.from({ length: 24 }, (_, index) => ({
      id: `message-${index + 1}`,
      content: `消息 ${index + 1}`,
      round: index + 1,
    }))
    render(
      <UserMessageNavigator
        markers={manyMarkers}
        scrollContainerRef={createRef<HTMLDivElement>()}
        totalRounds={100}
        onNavigate={() => undefined}
      />,
    )
    const rail = screen.getByLabelText('轮次位置：当前预选第 24 轮，共 100 轮')
    fireEvent.pointerEnter(rail)
    expect(within(screen.getByRole('group', { name: '对话轮次卡片' })).getAllByRole('button')).toHaveLength(10)
    expect(screen.queryByRole('button', { name: /第 14 轮/ })).not.toBeInTheDocument()
    const earliestVisible = screen.getByRole('button', { name: /第 15 轮：消息 15/ })
    fireEvent.pointerEnter(earliestVisible)
    expect(screen.getByRole('progressbar', { name: '当前预选轮次' })).toHaveAttribute('aria-valuenow', '15')
    expect(screen.getByRole('progressbar', { name: '当前预选轮次' })).toHaveAttribute('aria-valuemax', '100')
    fireEvent.keyDown(earliestVisible, { key: 'ArrowUp' })
    expect(within(screen.getByRole('group', { name: '对话轮次卡片' })).getAllByRole('button')).toHaveLength(10)
    expect(screen.getByRole('button', { name: /第 14 轮：消息 14/ })).toHaveAttribute('aria-current', 'true')
  })

  it('存在未加载历史时不展示旧按钮，只在越过已加载边界时增量加载', () => {
    const onLoadEarlierMessages = vi.fn()
    render(
      <UserMessageNavigator
        markers={markers.slice(0, 1)}
        scrollContainerRef={createRef<HTMLDivElement>()}
        totalRounds={12}
        hasEarlierMessages
        onLoadEarlierMessages={onLoadEarlierMessages}
        onNavigate={() => undefined}
      />,
    )

    const rail = screen.getByLabelText('轮次位置：当前预选第 4 轮，共 12 轮')
    fireEvent.pointerEnter(rail)
    expect(screen.queryByRole('button', { name: '加载更早消息' })).not.toBeInTheDocument()
    expect(onLoadEarlierMessages).not.toHaveBeenCalled()
    fireEvent.keyDown(rail, { key: 'ArrowUp' })
    expect(onLoadEarlierMessages).toHaveBeenCalledTimes(1)
  })

  it('加载上一页后把动态窗口移动到新出现的更早轮次', async () => {
    const allMarkers = Array.from({ length: 13 }, (_, index) => ({
      id: `message-${index + 1}`,
      content: `消息 ${index + 1}`,
      round: index + 1,
    }))
    function Harness() {
      const [loadedMarkers, setLoadedMarkers] = useState(allMarkers.slice(-3))
      const hasEarlierMessages = loadedMarkers.length < allMarkers.length
      return (
        <UserMessageNavigator
          markers={loadedMarkers}
          scrollContainerRef={createRef<HTMLDivElement>()}
          totalRounds={allMarkers.length}
          hasEarlierMessages={hasEarlierMessages}
          onLoadEarlierMessages={() => { setLoadedMarkers(allMarkers) }}
          onNavigate={() => undefined}
        />
      )
    }

    render(<Harness />)
    fireEvent.pointerEnter(screen.getByLabelText(/轮次位置/))
    const earliestLoadedCard = screen.getByRole('button', { name: /第 11 轮：消息 11/ })
    fireEvent.pointerEnter(earliestLoadedCard)
    fireEvent.keyDown(earliestLoadedCard, { key: 'ArrowUp' })
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /第 10 轮：消息 10/ })).toHaveAttribute('aria-current', 'true')
    })
  })

  it('不足两条且没有更早历史时隐藏，并压缩 Markdown 长文本', () => {
    const { container } = render(
      <UserMessageNavigator
        markers={markers.slice(0, 1)}
        scrollContainerRef={createRef<HTMLDivElement>()}
        onNavigate={() => undefined}
      />,
    )
    expect(container).toBeEmptyDOMElement()
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

  it('加载边界的连续操作不会重复发起历史请求', async () => {
    let resolve!: () => void
    const onLoadEarlierMessages = vi.fn(() => new Promise<void>((done) => { resolve = done }))
    render(<UserMessageNavigator markers={markers.slice(0, 1)} scrollContainerRef={createRef<HTMLDivElement>()}
      hasEarlierMessages totalRounds={12} onLoadEarlierMessages={onLoadEarlierMessages} onNavigate={vi.fn()} />)
    const rail = screen.getByRole('button', { name: /轮次位置/ })
    fireEvent.click(rail)
    fireEvent.keyDown(rail, { key: 'ArrowUp' })
    fireEvent.keyDown(rail, { key: 'ArrowUp' })
    expect(onLoadEarlierMessages).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('status')).toHaveTextContent('正在加载更早消息')
    await act(async () => { resolve() })
    expect(screen.getByRole('status')).not.toHaveTextContent('正在加载更早消息')
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
