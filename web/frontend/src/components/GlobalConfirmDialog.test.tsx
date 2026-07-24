import { useState } from 'react'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { GlobalConfirmDialog } from './GlobalConfirmDialog'

function DialogHarness() {
  const [open, setOpen] = useState(false)
  return <>
    <button type="button" onClick={() => setOpen(true)}>打开确认框</button>
    <GlobalConfirmDialog
      open={open}
      title="确认危险操作？"
      description="执行后无法撤销。"
      icon={<span aria-hidden="true">!</span>}
      tone="danger"
      confirmLabel="确认执行"
      onCancel={() => setOpen(false)}
      onConfirm={() => setOpen(false)}
    />
  </>
}

describe('GlobalConfirmDialog', () => {
  it('挂载到 document.body，锁定滚动，并在关闭后恢复焦点', async () => {
    render(<DialogHarness />)
    const trigger = screen.getByRole('button', { name: '打开确认框' })

    trigger.focus()
    fireEvent.click(trigger)
    const dialog = screen.getByRole('alertdialog', { name: '确认危险操作？' })
    expect(dialog.parentElement?.parentElement).toBe(document.body)
    expect(document.body.style.overflow).toBe('hidden')
    await waitFor(() => expect(screen.getByRole('button', { name: '确认执行' })).toHaveFocus())

    fireEvent.keyDown(document, { key: 'Escape' })
    expect(screen.queryByRole('alertdialog', { name: '确认危险操作？' })).not.toBeInTheDocument()
    expect(document.body.style.overflow).toBe('')
    expect(trigger).toHaveFocus()
  })
})
