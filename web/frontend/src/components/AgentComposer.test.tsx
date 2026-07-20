import type { ComponentProps } from 'react'
import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import { AgentComposer } from './AgentComposer'

function renderComposer(overrides: Partial<ComponentProps<typeof AgentComposer>> = {}) {
  const props: ComponentProps<typeof AgentComposer> = {
    value: '',
    placeholder: '给 kemo-agent 发送消息…',
    currentRound: 3,
    roundLimit: 30,
    onChange: vi.fn(),
    onOpenKnowledge: vi.fn(),
    onOpenSkills: vi.fn(),
    onOpenCommands: vi.fn(),
    onToggleConversationMenu: vi.fn(),
    onSubmit: vi.fn(),
    ...overrides,
  }
  return { ...render(<AgentComposer {...props} />), props }
}

describe('AgentComposer', () => {
  it('展示真实轮次并支持 Enter 发送、Shift+Enter 换行', () => {
    const onSubmit = vi.fn()
    renderComposer({ value: '检查状态', currentRound: 8, roundLimit: 30, onSubmit })
    expect(screen.getByText('第 8 轮')).toBeInTheDocument()
    expect(screen.getByText('8/30')).toBeInTheDocument()

    const input = screen.getByRole('textbox', { name: '消息内容' })
    fireEvent.keyDown(input, { key: 'Enter', shiftKey: true })
    expect(onSubmit).not.toHaveBeenCalled()
    fireEvent.keyDown(input, { key: 'Enter' })
    expect(onSubmit).toHaveBeenCalledTimes(1)
  })

  it('运行中保留发送引导和停止能力，未接入的上传按钮保持禁用', () => {
    const onSubmit = vi.fn()
    const onStop = vi.fn()
    renderComposer({ value: '补充要求', running: true, onSubmit, onStop })
    expect(screen.getByRole('button', { name: '上传文件' })).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '发送引导' }))
    fireEvent.click(screen.getByRole('button', { name: '停止生成' }))
    expect(onSubmit).toHaveBeenCalledTimes(1)
    expect(onStop).toHaveBeenCalledTimes(1)
  })

  it('通过隐藏文件选择器把用户选择的文件交给上传处理器', () => {
    const onUploadFile = vi.fn()
    const { container } = renderComposer({ onUploadFile })
    const uploadButton = screen.getByRole('button', { name: '上传文件' })
    expect(uploadButton).toBeEnabled()

    fireEvent.click(uploadButton)
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')
    const file = new File(['kemo upload'], 'browser-upload.txt', { type: 'text/plain' })
    expect(input).not.toBeNull()
    fireEvent.change(input!, { target: { files: [file] } })
    expect(onUploadFile).toHaveBeenCalledWith(file)
  })
})
