import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { RefreshActionButton } from './ModuleUi'

describe('RefreshActionButton', () => {
  it('在刷新期间禁用重复点击、旋转图标并切换提示文字', () => {
    const onClick = vi.fn()
    const { rerender } = render(<RefreshActionButton
      pending={false}
      label="重新读取"
      pendingLabel="读取中…"
      onClick={onClick}
    />)

    fireEvent.click(screen.getByRole('button', { name: '重新读取' }))
    expect(onClick).toHaveBeenCalledTimes(1)

    rerender(<RefreshActionButton
      pending
      label="重新读取"
      pendingLabel="读取中…"
      onClick={onClick}
    />)
    const pendingButton = screen.getByRole('button', { name: '读取中…' })
    expect(pendingButton).toBeDisabled()
    expect(pendingButton).toHaveAttribute('aria-busy', 'true')
    expect(pendingButton.querySelector('svg')).toHaveClass('spin')
    fireEvent.click(pendingButton)
    expect(onClick).toHaveBeenCalledTimes(1)
  })
})
