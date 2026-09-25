import { fireEvent, render, screen, waitFor } from '@testing-library/react'
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
    expect(tooltip).toHaveTextContent('说明.txt')

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
})
