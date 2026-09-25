import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { ComposerMessagePanel } from './ComposerMessagePanel'

function renderPanel(options: {
  sessionKey?: string
  followUpCount?: number
  guidance?: boolean
} = {}) {
  return render(
    <ComposerMessagePanel
      sessionKey={options.sessionKey ?? 'session-a'}
      followUpCount={options.followUpCount ?? 2}
      followUpContent={<div>跟进消息完整列表</div>}
      guidanceContent={options.guidance === false ? undefined : <div>当前引导消息</div>}
    />,
  )
}

describe('ComposerMessagePanel', () => {
  it('默认折叠跟进列表，但保留栏头和当前引导', () => {
    renderPanel()

    expect(screen.getByRole('region', { name: '跟进和引导消息栏' })).toBeInTheDocument()
    expect(screen.getByText('跟进 / 引导消息栏')).toBeInTheDocument()
    expect(screen.getByText('当前引导消息')).toBeInTheDocument()
    expect(screen.queryByText('跟进消息完整列表')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '展开跟进消息' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('展开后显示跟进和引导，能够再次收起', () => {
    renderPanel()

    fireEvent.click(screen.getByRole('button', { name: '展开跟进消息' }))
    expect(screen.getByText('跟进消息完整列表')).toBeInTheDocument()
    expect(screen.getByText('当前引导消息')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: '收起跟进消息' })).toHaveAttribute('aria-expanded', 'true')

    fireEvent.click(screen.getByRole('button', { name: '收起跟进消息' }))
    expect(screen.queryByText('跟进消息完整列表')).not.toBeInTheDocument()
    expect(screen.getByText('当前引导消息')).toBeInTheDocument()
  })

  it('切换会话后恢复为折叠状态', async () => {
    const { rerender } = renderPanel({ sessionKey: 'session-a' })
    fireEvent.click(screen.getByRole('button', { name: '展开跟进消息' }))
    expect(screen.getByText('跟进消息完整列表')).toBeInTheDocument()

    rerender(
      <ComposerMessagePanel
        sessionKey="session-b"
        followUpCount={2}
        followUpContent={<div>跟进消息完整列表</div>}
        guidanceContent={<div>当前引导消息</div>}
      />,
    )

    await waitFor(() => expect(screen.queryByText('跟进消息完整列表')).not.toBeInTheDocument())
    expect(screen.getByRole('button', { name: '展开跟进消息' })).toHaveAttribute('aria-expanded', 'false')
  })

  it('只有引导时不显示无意义的展开按钮', () => {
    renderPanel({ followUpCount: 0 })

    expect(screen.getByText('当前引导消息')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '展开跟进消息' })).not.toBeInTheDocument()
  })
})
