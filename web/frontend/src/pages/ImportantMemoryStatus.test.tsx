import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ImportantMemoryStatus } from './ImportantMemoryStatus'
import type { ImportantMemoryLifecycle } from '../types/api'

const valid: ImportantMemoryLifecycle = { status: 'valid', is_current: true, reason_codes: [], reason: '来源校验通过。', prompt_eligible: true }

describe('ImportantMemoryStatus', () => {
  it('解释失效原因并明确保留旧内容而不是删除记忆', () => {
    render(<ImportantMemoryStatus lifecycle={{ ...valid, status: 'invalid', is_current: false, reason_codes: ['source_expired'], reason: '来源记忆已到期。', prompt_eligible: false }} failed={false} />)
    expect(screen.getByRole('status')).toHaveTextContent('已失效')
    expect(screen.getByRole('status')).toHaveTextContent('来源记忆已到期。')
    expect(screen.getByRole('status')).toHaveTextContent('后续对话暂停注入；旧内容保留')
  })

  it('有效但关闭注入不误标为失效', () => {
    render(<ImportantMemoryStatus lifecycle={{ ...valid, prompt_eligible: false }} failed={false} />)
    expect(screen.getByRole('status')).toHaveTextContent('有效')
    expect(screen.getByRole('status')).toHaveTextContent('当前配置未启用该记忆注入')
    expect(screen.getByRole('status')).not.toHaveTextContent('已失效')
  })

  it('无来源记录和请求失败不会被误报为已验证有效', () => {
    const { rerender } = render(<ImportantMemoryStatus lifecycle={{ ...valid, status: 'untracked', reason: '未记录来源，暂无法校验有效性。' }} failed={false} />)
    expect(screen.getByRole('status')).toHaveTextContent('未校验')
    rerender(<ImportantMemoryStatus lifecycle={valid} failed />)
    expect(screen.getByRole('status')).toHaveTextContent('状态未知')
    expect(screen.getByRole('status')).toHaveTextContent('状态读取失败')
    expect(screen.getByRole('status')).not.toHaveTextContent('来源校验通过')
  })
})
