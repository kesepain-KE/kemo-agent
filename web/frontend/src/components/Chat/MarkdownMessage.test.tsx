import { render, screen, waitFor } from '@testing-library/react'
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
