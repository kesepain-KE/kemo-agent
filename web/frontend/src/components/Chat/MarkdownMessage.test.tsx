import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { MarkdownMessage } from './MarkdownMessage'

const renderMermaid = vi.fn()

vi.mock('mermaid', () => ({
  default: {
    initialize: vi.fn(),
    render: (...args: unknown[]) => renderMermaid(...args),
  },
}))

describe('MarkdownMessage', () => {
  beforeEach(() => {
    renderMermaid.mockReset()
    renderMermaid.mockResolvedValue({ svg: '<svg aria-label="流程图"></svg>' })
    Object.defineProperty(window, 'isSecureContext', {
      configurable: true,
      value: false,
    })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: undefined,
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: undefined,
    })
  })

  it('renders static GFM, math, emoji and highlighted code', () => {
    const { container } = render(
      <MarkdownMessage content={'first\nsecond\n\n$E=mc^2$ :rocket:\n\n| A | B |\n| - | - |\n| 1 | 2 |\n\n- [x] done\n\n![preview](https://example.com/a.png)\n\n```json\n{"ok": true}\n```'} />,
    )

    expect(container.querySelector('br')).toBeInTheDocument()
    expect(container.querySelector('.katex')).toBeInTheDocument()
    expect(screen.getByText(/🚀/)).toBeInTheDocument()
    expect(container.querySelector('table')).toBeInTheDocument()
    expect(container.querySelector('input[type="checkbox"]')).toBeDisabled()
    expect(screen.getByRole('img', { name: 'preview' })).toHaveAttribute('loading', 'lazy')
    expect(container.querySelector('code.hljs')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '复制' })).toBeInTheDocument()
  })

  it('copies highlighted code through the secure clipboard API', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })
    render(
      <MarkdownMessage content={'```bash\nsudo systemctl stop kemo-agent\necho done\n```'} />,
    )

    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => {
      expect(writeText).toHaveBeenCalledWith('sudo systemctl stop kemo-agent\necho done')
    })
    expect(execCommand).not.toHaveBeenCalled()
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
  })

  it('uses the synchronous textarea fallback on LAN HTTP pages', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockResolvedValue(undefined)
    let selectedValue = ''
    const execCommand = vi.fn().mockImplementation(() => {
      selectedValue = document.querySelector('textarea')?.value || ''
      return true
    })
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: false })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })
    render(<MarkdownMessage content={'```bash\nsudo systemctl restart kemo-agent\n```'} />)

    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith('copy'))
    expect(writeText).not.toHaveBeenCalled()
    expect(selectedValue).toBe('sudo systemctl restart kemo-agent')
    expect(document.querySelector('textarea[aria-hidden="true"]')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
  })

  it('falls back when the secure clipboard API is denied', async () => {
    const user = userEvent.setup()
    const writeText = vi.fn().mockRejectedValue(new Error('denied'))
    const execCommand = vi.fn().mockReturnValue(true)
    Object.defineProperty(window, 'isSecureContext', { configurable: true, value: true })
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { writeText },
    })
    Object.defineProperty(document, 'execCommand', {
      configurable: true,
      value: execCommand,
    })
    render(<MarkdownMessage content={'```text\nfallback copy\n```'} />)

    await user.click(screen.getByRole('button', { name: '复制' }))

    await waitFor(() => expect(execCommand).toHaveBeenCalledWith('copy'))
    expect(screen.getByRole('button', { name: '已复制' })).toBeInTheDocument()
  })

  it('does not add a copy button to inline code', () => {
    render(<MarkdownMessage content={'运行 `python update.py --check` 查看状态。'} />)

    expect(screen.queryByRole('button', { name: '复制' })).not.toBeInTheDocument()
  })

  it('filters dangerous URLs and hardens external links', () => {
    const { container } = render(
      <MarkdownMessage content={'[safe](https://example.com) [unsafe](javascript:alert(1))'} />,
    )
    const safe = screen.getByRole('link', { name: 'safe' })
    expect(safe).toHaveAttribute('target', '_blank')
    expect(safe).toHaveAttribute('rel', 'noopener noreferrer')
    expect(container.querySelector('a[href^="javascript:"]')).not.toBeInTheDocument()
  })

  it('renders mermaid code blocks asynchronously', async () => {
    const { container } = render(
      <MarkdownMessage content={'```mermaid\nflowchart LR\nA-->B\n```'} />,
    )

    await waitFor(() => expect(renderMermaid).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('svg[aria-label="流程图"]')).toBeInTheDocument())
  })

  it('keeps streaming rendering lightweight', () => {
    const { container } = render(<MarkdownMessage content={'$unfinished'} streaming />)
    expect(container.querySelector('.katex')).not.toBeInTheDocument()
    expect(container.querySelector('.hljs')).not.toBeInTheDocument()
  })
})
