import { createRef } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { compactUserMessagePreview, UserMessageNavigator, type UserMessageMarker } from './UserMessageNavigator'

const markers: UserMessageMarker[] = [
  { id: 'message-1', content: '# 第一条\n\n请检查运行状态。', round: 4 },
  { id: 'message-2', content: '第二条用户消息', round: 5 },
]

describe('UserMessageNavigator', () => {
  it('为已加载的用户消息显示刻度、预览并触发跳转', () => {
    const onNavigate = vi.fn()
    const scrollRef = createRef<HTMLDivElement>()
    render(
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
    expect(screen.getByLabelText('已加载的用户消息，可单独滚动')).toBeInTheDocument()
    const firstMarker = screen.getByRole('button', { name: /跳转到第 4 轮/ })
    expect(firstMarker).not.toHaveAttribute('style')
    fireEvent.mouseEnter(firstMarker)
    expect(screen.getByRole('tooltip')).toHaveTextContent('第 4 轮')
    expect(screen.getByRole('tooltip')).toHaveTextContent('第一条 请检查运行状态。')
    fireEvent.click(screen.getByRole('button', { name: /跳转到第 5 轮/ }))
    expect(onNavigate).toHaveBeenCalledWith('message-2')
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
