import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { PlainTextMessage } from './PlainTextMessage'

describe('PlainTextMessage', () => {
  it('原样展示用户文本而不解析 Markdown', () => {
    const content = '# 标题\n**粗体**\n[链接](https://example.com)\n```js\nalert(1)\n```'
    const { container } = render(<PlainTextMessage content={content} />)

    expect(container.firstElementChild?.textContent).toBe(content)
    expect(screen.queryByRole('heading')).not.toBeInTheDocument()
    expect(screen.queryByRole('link')).not.toBeInTheDocument()
    expect(container.querySelector('strong, pre, code')).toBeNull()
  })
})
