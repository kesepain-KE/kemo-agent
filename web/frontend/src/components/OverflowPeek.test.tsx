import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { OverflowPeek } from './OverflowPeek'

function setDimensions(element: HTMLElement, dimensions: {
  clientHeight: number
  scrollHeight: number
  clientWidth?: number
  scrollWidth?: number
}) {
  Object.defineProperties(element, {
    clientHeight: { configurable: true, value: dimensions.clientHeight },
    scrollHeight: { configurable: true, value: dimensions.scrollHeight },
    clientWidth: { configurable: true, value: dimensions.clientWidth ?? 300 },
    scrollWidth: { configurable: true, value: dimensions.scrollWidth ?? 300 },
  })
}

describe('OverflowPeek', () => {
  it('does not show a tooltip when the content fits', () => {
    render(<OverflowPeek text="短消息"><p>短消息</p></OverflowPeek>)
    const anchor = screen.getByText('短消息').closest('[data-overflow]') as HTMLElement
    setDimensions(anchor, { clientHeight: 80, scrollHeight: 80 })

    fireEvent.mouseEnter(anchor)
    expect(screen.queryByRole('tooltip')).not.toBeInTheDocument()
  })

  it('shows clipped content on hover and hides it after leaving', async () => {
    render(<OverflowPeek text="很长的跟进内容" attachmentNames={['说明.txt']}><p>很长的跟进内容</p></OverflowPeek>)
    const anchor = screen.getByText('很长的跟进内容').closest('[data-overflow]') as HTMLElement
    setDimensions(anchor, { clientHeight: 80, scrollHeight: 240 })

    fireEvent.mouseEnter(anchor)
    const tooltip = await screen.findByRole('tooltip')
    expect(tooltip).toHaveTextContent('很长的跟进内容')
    expect(within(tooltip).getByRole('listitem')).toHaveTextContent('说明.txt')
    expect(tooltip.querySelector('img, audio, video')).toBeNull()

    fireEvent.mouseLeave(anchor)
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('pins the preview on click and closes it with Escape', async () => {
    render(<OverflowPeek text="需要完整查看"><p>需要完整查看</p></OverflowPeek>)
    const anchor = screen.getByText('需要完整查看').closest('[data-overflow]') as HTMLElement
    setDimensions(anchor, { clientHeight: 80, scrollHeight: 240 })

    fireEvent.click(anchor)
    expect(await screen.findByRole('tooltip')).toBeInTheDocument()
    fireEvent.keyDown(document, { key: 'Escape' })
    await waitFor(() => expect(screen.queryByRole('tooltip')).not.toBeInTheDocument())
  })

  it('previews attachment-only media and makes pinned playback controls keyboard reachable', async () => {
    render(<OverflowPeek text="" attachments={[
      { name: '图.png', kind: 'image', previewUrl: '/thumb.png' },
      { name: '音频.ogg', kind: 'audio', previewUrl: '/media.ogg' },
      { name: '视频.mp4', kind: 'video', previewUrl: '/media.mp4' },
    ]}><p>附件卡片</p></OverflowPeek>)
    const anchor = screen.getByText('附件卡片').closest<HTMLElement>('[data-overflow]')!
    setDimensions(anchor, { clientHeight: 80, scrollHeight: 240 })
    fireEvent.mouseEnter(anchor)
    const tooltip = await screen.findByRole('tooltip')
    expect(anchor).toHaveAttribute('aria-describedby', tooltip.id)
    expect(within(tooltip).getByRole('img', { name: '图.png' })).toHaveAttribute('src', '/thumb.png')

    fireEvent.keyDown(anchor, { key: 'Enter' })
    const dialog = await screen.findByRole('dialog', { name: '完整消息跟进' })
    expect(dialog).toHaveFocus()
    expect(dialog.querySelector('audio[controls]')).toHaveAttribute('src', '/media.ogg')
    expect(dialog.querySelector('video[controls]')).toHaveAttribute('src', '/media.mp4')
    fireEvent.keyDown(dialog, { key: 'Escape' })
    expect(screen.queryByRole('dialog')).toBeNull()
    expect(anchor).toHaveFocus()
  })

  it('remeasures after a child image loads without resizing the anchor', () => {
    render(<OverflowPeek text="迟加载图片"><img src="/late.png" alt="图片" /></OverflowPeek>)
    const image = screen.getByRole('img', { name: '图片' })
    const anchor = image.closest<HTMLElement>('[data-overflow]')!
    setDimensions(anchor, { clientHeight: 100, scrollHeight: 100 })
    expect(anchor).toHaveAttribute('data-overflow', 'false')

    setDimensions(anchor, { clientHeight: 100, scrollHeight: 222 })
    fireEvent.load(image)
    expect(anchor).toHaveAttribute('data-overflow', 'true')
    expect(anchor).toHaveAttribute('tabindex', '0')
  })
})
