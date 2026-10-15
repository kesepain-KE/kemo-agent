import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { KemoLogo } from './KemoLogo'

describe('KemoLogo', () => {
  it('使用现有 Logo 接口而不是字母占位符', () => {
    render(<KemoLogo />)
    expect(screen.getByRole('img', { name: 'kemo-agent Logo' })).toHaveAttribute('src', '/api/logo')
    expect(screen.queryByText('K')).not.toBeInTheDocument()
  })

  it('接口图片不可用时回退到随前端发布的同一 Logo，不循环重试', () => {
    render(<KemoLogo />)
    const image = screen.getByRole('img', { name: 'kemo-agent Logo' })
    fireEvent.error(image)
    expect(image).toHaveAttribute('src', '/kemo-agent.jpg')
    fireEvent.error(image)
    expect(image).toHaveAttribute('src', '/kemo-agent.jpg')
  })
})
