import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { beijingDateTimeInput, CronEditorDialog } from './CronEditorDialog'
import type { CronTaskSummary } from '../types/api'

describe('cron editor scheduling parity', () => {
  it('converts UTC timestamps to Beijing wall time, without duplicated seconds', async () => {
    expect(beijingDateTimeInput('2026-10-25T01:30:00Z')).toBe('2026-10-25T09:30')
    const save = vi.fn().mockResolvedValue(undefined)
    render(<CronEditorDialog task={{ title: 'single', type: 'once', next_run_at: '2026-10-25T01:30:00Z' } as CronTaskSummary} pending={false} error="" onSave={save} onClose={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    await waitFor(() => expect(save).toHaveBeenCalledWith({ title: 'single', type: 'once', next_run_at: '2026-10-25T09:30:00+08:00' }))
  })
  it('rejects invalid weekly dates before sending a write', () => {
    const save = vi.fn()
    render(<CronEditorDialog pending={false} error="" onSave={save} onClose={() => {}} />)
    fireEvent.change(screen.getByLabelText('任务标题'), { target: { value: 'weekly' } })
    fireEvent.change(screen.getByLabelText('执行内容'), { target: { value: 'test only' } })
    fireEvent.change(screen.getByLabelText('任务类型'), { target: { value: 'weekly' } })
    fireEvent.change(screen.getByLabelText('星期（1=周一，7=周日）'), { target: { value: '0,8' } })
    fireEvent.click(screen.getByRole('button', { name: '保存' }))
    expect(screen.getByText('星期必须为 1–7 的整数')).toBeInTheDocument()
    expect(save).not.toHaveBeenCalled()
  })
})
