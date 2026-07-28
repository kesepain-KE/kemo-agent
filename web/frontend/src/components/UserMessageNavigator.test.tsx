import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { compactUserMessagePreview, UserMessageNavigator, type UserMessageMarker } from './UserMessageNavigator'

const markers: UserMessageMarker[] = [
  { id: 'message-1', content: '# 第一条\n\n请检查运行状态。', round: 4 },
  { id: 'message-2', content: '第二条用户消息', round: 5 },
  { id: 'message-3', content: '第三条用户消息', round: 6 },
]

describe('UserMessageNavigator', () => {
  it('按 90° 到 270° 展开无轨迹卡片并触发对应轮次跳转', () => {
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
        <UserMessageNavigator markers={markers} scrollContainerRef={scrollRef} onNavigate={onNavigate} />
      </div>,
    )

    expect(screen.getByRole('navigation', { name: '用户消息导航' })).toBeInTheDocument()
    expect(screen.getByLabelText('已加载的用户消息，可通过滚轮切换')).toBeInTheDocument()
    const firstMarker = screen.getByRole('option', { name: /跳转到第 4 轮/ })
    fireEvent.pointerEnter(firstMarker)
    const wheel = screen.getByLabelText('对话轮次卡片')
    expect(wheel).toHaveAttribute('aria-hidden', 'false')
    expect(container.querySelector('.user-message-wheel-arc')).not.toBeInTheDocument()
    expect(container.querySelector('.user-message-wheel-node')).not.toBeInTheDocument()
    const firstCard = screen.getByRole('button', { name: /第 4 轮：第一条 请检查运行状态/ })
    const middleCard = screen.getByRole('button', { name: /第 5 轮：第二条用户消息/ })
    const lastCard = screen.getByRole('button', { name: /第 6 轮：第三条用户消息/ })
    expect(firstCard).toHaveAttribute('data-wheel-angle', '90')
    expect(middleCard).toHaveAttribute('data-wheel-angle', '180')
    expect(lastCard).toHaveAttribute('data-wheel-angle', '270')
    expect(firstCard).toHaveAttribute('aria-current', 'true')
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
    const firstMarker = screen.getByRole('option', { name: /跳转到第 4 轮/ })
    fireEvent.pointerEnter(firstMarker)
    fireEvent.wheel(screen.getByRole('navigation', { name: '用户消息导航' }), { deltaY: 120 })
    expect(screen.getByRole('button', { name: /第 5 轮：第二条用户消息/ })).toHaveAttribute('aria-current', 'true')
    fireEvent.keyDown(firstMarker, { key: 'ArrowDown' })
    expect(screen.getByRole('button', { name: /第 6 轮：第三条用户消息/ })).toHaveAttribute('aria-current', 'true')
  })

  it('右侧最多保留最近二十条刻度', () => {
    const manyMarkers = Array.from({ length: 24 }, (_, index) => ({
      id: `message-${index + 1}`,
      content: `消息 ${index + 1}`,
      round: index + 1,
    }))
    render(
      <UserMessageNavigator
        markers={manyMarkers}
        scrollContainerRef={createRef<HTMLDivElement>()}
        onNavigate={() => undefined}
      />,
    )
    expect(screen.getAllByRole('option')).toHaveLength(20)
    expect(screen.queryByRole('option', { name: /跳转到第 4 轮/ })).not.toBeInTheDocument()
    expect(screen.getByRole('option', { name: /跳转到第 5 轮/ })).toBeInTheDocument()
  })

  it('存在未加载历史时提供增量加载入口', () => {
    const onLoadEarlierMessages = vi.fn()
    render(
      <UserMessageNavigator
        markers={markers.slice(0, 1)}
        scrollContainerRef={createRef<HTMLDivElement>()}
        hasEarlierMessages
        onLoadEarlierMessages={onLoadEarlierMessages}
        onNavigate={() => undefined}
      />,
    )

    fireEvent.click(screen.getByRole('button', { name: '加载更早消息' }))
    expect(onLoadEarlierMessages).toHaveBeenCalledTimes(1)
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
})
