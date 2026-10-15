import { useRef } from 'react'
import { act, render, screen, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useComposerAutosize } from './useComposerAutosize'

function Input({ value }: { value: string }) {
  const ref = useRef<HTMLTextAreaElement>(null)
  useComposerAutosize(ref, value)
  return <textarea ref={ref} value={value} readOnly aria-label="高度测试" style={{ borderWidth: 0, boxSizing: 'border-box' }} />
}

let previousFontSize = ''
beforeEach(() => {
  previousFontSize = document.documentElement.style.fontSize
  document.documentElement.style.fontSize = '16px'
})
afterEach(() => {
  document.documentElement.style.fontSize = previousFontSize
  vi.restoreAllMocks()
  vi.unstubAllGlobals()
})

describe('发送框自动高度', () => {
  it('长草稿删为短文时重新测量并回缩，清空恢复最小高度及滚动位置', () => {
    const { rerender } = render(<Input value="" />)
    const input = screen.getByRole('textbox') as HTMLTextAreaElement
    let contentHeight = 600
    Object.defineProperty(input, 'scrollHeight', { get: () => contentHeight })
    rerender(<Input value={'长文\n'.repeat(40)} />)
    expect(input.style.height).toBe('192px')
    contentHeight = 70
    rerender(<Input value="短文" />)
    expect(input.style.height).toBe('70px')
    input.scrollTop = 200
    contentHeight = 600
    rerender(<Input value="" />)
    expect(Number.parseFloat(input.style.height)).toBeCloseTo(51.2)
    expect(input.scrollTop).toBe(0)
  })

  it('空草稿即使读取到旧 scrollHeight 也不被再次撑高', () => {
    vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(600)
    render(<Input value="" />)
    expect(Number.parseFloat((screen.getByRole('textbox') as HTMLTextAreaElement).style.height)).toBeCloseTo(51.2)
  })

  it('视口变矮重新限制长文高度，回缩后输入内容不变', () => {
    const { rerender } = render(<Input value="" />)
    const input = screen.getByRole('textbox') as HTMLTextAreaElement
    Object.defineProperty(input, 'scrollHeight', { get: () => 600 })
    rerender(<Input value="保留的长文" />)
    vi.stubGlobal('innerHeight', 240)
    act(() => window.dispatchEvent(new Event('resize')))
    expect(input.style.height).toBe('96px')
    expect(input.value).toBe('保留的长文')
  })

  it('宽度变化重新计算折行，自己的高度变化不触发反馈循环', () => {
    let notify = () => {}
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback: () => void) { notify = callback }
      observe = vi.fn()
      disconnect = disconnect
    })
    const { rerender, unmount } = render(<Input value="" />)
    const input = screen.getByRole('textbox') as HTMLTextAreaElement
    let width = 400
    let contentHeight = 160
    vi.spyOn(input, 'getBoundingClientRect').mockImplementation(() => ({ width }) as DOMRect)
    const heightsSeen: string[] = []
    Object.defineProperty(input, 'scrollHeight', { get: () => { heightsSeen.push(input.style.height); return contentHeight } })
    rerender(<Input value="同一份草稿" />)
    act(() => notify())
    expect(input.style.height).toBe('160px')
    contentHeight = 60
    const measurements = heightsSeen.length
    act(() => notify())
    expect(heightsSeen.length).toBe(measurements)
    width = 800
    act(() => notify())
    expect(input.style.height).toBe('60px')
    expect(heightsSeen.every((height) => height === '0px')).toBe(true)
    unmount()
    expect(disconnect).toHaveBeenCalledOnce()
    input.style.height = '88px'
    act(() => window.dispatchEvent(new Event('resize')))
    expect(input.style.height).toBe('88px')
  })

  it('字号修改但文字不变时也重新计算空草稿高度', async () => {
    render(<Input value="" />)
    const input = screen.getByRole('textbox') as HTMLTextAreaElement
    act(() => { document.documentElement.style.fontSize = '20px' })
    await waitFor(() => expect(input.style.height).toBe('64px'))
  })
})
