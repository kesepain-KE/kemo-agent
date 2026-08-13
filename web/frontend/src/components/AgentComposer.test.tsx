import type { ComponentProps } from 'react'
import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { AgentComposer } from './AgentComposer'

afterEach(() => {
  delete (window as Window & { webkitSpeechRecognition?: unknown }).webkitSpeechRecognition
})

function renderComposer(overrides: Partial<ComponentProps<typeof AgentComposer>> = {}) {
  const props: ComponentProps<typeof AgentComposer> = {
    value: '',
    placeholder: '给 kemo-agent 发送消息…',
    currentRound: 3,
    roundLimit: 30,
    onChange: vi.fn(),
    onOpenKnowledge: vi.fn(),
    onOpenExpand: vi.fn(),
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
    renderComposer({ value: '检查状态', currentRound: 8, totalRounds: 44, roundLimit: 30, onSubmit })
    expect(screen.getByText('上下文 8 轮')).toBeInTheDocument()
    expect(screen.getByText('8/30')).toBeInTheDocument()
    expect(screen.getByText('历史 44')).toBeInTheDocument()

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

  it('运行中仍可打开对话操作菜单，以便关闭会话级长任务模式', () => {
    const onToggleConversationMenu = vi.fn()
    renderComposer({ running: true, onToggleConversationMenu })
    const menu = screen.getByRole('button', { name: '展开对话操作' })
    expect(menu).toBeEnabled()
    fireEvent.click(menu)
    expect(onToggleConversationMenu).toHaveBeenCalledOnce()
  })

  it('停止过渡期间把新文本作为下一轮提交，不会误投给旧运行', () => {
    const onSubmit = vi.fn()
    renderComposer({ value: '停止后继续处理', running: true, stopping: true, onSubmit, onStop: vi.fn() })
    const submit = screen.getByRole('button', { name: '发送下一轮' })
    expect(submit).toBeEnabled()
    fireEvent.click(submit)
    expect(onSubmit).toHaveBeenCalledOnce()
    expect(screen.getByRole('button', { name: '停止生成' })).toBeDisabled()
  })

  it('通过隐藏文件选择器把用户选择的文件交给上传处理器', () => {
    const onUploadFiles = vi.fn()
    const { container } = renderComposer({ onUploadFiles })
    const uploadButton = screen.getByRole('button', { name: '上传文件' })
    expect(uploadButton).toBeEnabled()

    fireEvent.click(uploadButton)
    const input = container.querySelector<HTMLInputElement>('input[type="file"]')
    const first = new File(['kemo upload'], 'browser-upload.txt', { type: 'text/plain' })
    const second = new File(['archive'], 'files.zip', { type: 'application/zip' })
    expect(input).not.toBeNull()
    expect(input).toHaveAttribute('multiple')
    fireEvent.change(input!, { target: { files: [first, second] } })
    expect(onUploadFiles).toHaveBeenCalledWith([first, second])
  })

  it('粘贴截图或资源管理器文件时交给上传处理器，纯文本保持浏览器默认行为', () => {
    const onUploadFiles = vi.fn()
    renderComposer({ onUploadFiles })
    const input = screen.getByRole('textbox', { name: '消息内容' })
    const screenshot = new File(['png'], 'image.png', { type: 'image/png' })
    const archive = new File(['zip'], 'files.zip', { type: 'application/zip' })

    const filePaste = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(filePaste, 'clipboardData', {
      value: {
        items: [
          { kind: 'file', getAsFile: () => screenshot },
          { kind: 'file', getAsFile: () => archive },
        ],
        files: [screenshot, archive],
      },
    })
    input.dispatchEvent(filePaste)
    expect(filePaste.defaultPrevented).toBe(true)
    expect(onUploadFiles).toHaveBeenCalledWith([screenshot, archive])

    const textPaste = new Event('paste', { bubbles: true, cancelable: true })
    Object.defineProperty(textPaste, 'clipboardData', { value: { items: [], files: [] } })
    input.dispatchEvent(textPaste)
    expect(textPaste.defaultPrevented).toBe(false)
    expect(onUploadFiles).toHaveBeenCalledTimes(1)
  })

  it('只有待发送附件时允许发送，上传进行中保持禁用', () => {
    const onSubmit = vi.fn()
    const { rerender, props } = renderComposer({ pendingFileCount: 1, onSubmit })
    fireEvent.click(screen.getByRole('button', { name: '发送' }))
    expect(onSubmit).toHaveBeenCalledOnce()

    rerender(<AgentComposer {...props} pendingFileCount={1} uploading />)
    expect(screen.getByRole('button', { name: '发送' })).toBeDisabled()
  })

  it('运行中只有待发送附件时也允许提交引导', () => {
    const onSubmit = vi.fn()
    renderComposer({ running: true, pendingFileCount: 1, onSubmit })

    const button = screen.getByRole('button', { name: '发送引导' })
    expect(button).toBeEnabled()
    fireEvent.click(button)
    expect(onSubmit).toHaveBeenCalledOnce()
  })

  it('Boxes 按钮打开拓展面板', () => {
    const onOpenExpand = vi.fn()
    renderComposer({ onOpenExpand })
    fireEvent.click(screen.getByRole('button', { name: '打开拓展' }))
    expect(onOpenExpand).toHaveBeenCalledTimes(1)
    expect(screen.queryByRole('button', { name: '打开技能' })).not.toBeInTheDocument()
  })

  it('在上传与知识库按钮之间使用浏览器中文语音识别并追加到草稿', () => {
    let recognition: FakeSpeechRecognition | null = null
    class FakeSpeechRecognition {
      lang = ''
      interimResults = true
      continuous = true
      onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null = null
      onerror: ((event: { error: string }) => void) | null = null
      onend: (() => void) | null = null
      start = vi.fn()
      stop = vi.fn()
      abort = vi.fn()
      constructor() { recognition = this }
    }
    Object.defineProperty(window, 'webkitSpeechRecognition', { configurable: true, writable: true, value: FakeSpeechRecognition })
    const onChange = vi.fn()
    renderComposer({ value: '已有内容', onChange })

    const uploadButton = screen.getByRole('button', { name: '上传文件' })
    const speechButton = screen.getByRole('button', { name: '语音识别' })
    const knowledgeButton = screen.getByRole('button', { name: '打开知识库' })
    expect(uploadButton.compareDocumentPosition(speechButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(speechButton.compareDocumentPosition(knowledgeButton) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()

    fireEvent.click(speechButton)
    expect(recognition).not.toBeNull()
    expect(recognition!.lang).toBe('zh-CN')
    expect(recognition!.interimResults).toBe(false)
    expect(recognition!.continuous).toBe(false)
    expect(recognition!.start).toHaveBeenCalledTimes(1)
    expect(screen.getByRole('button', { name: '停止语音识别' })).toHaveAttribute('aria-pressed', 'true')

    act(() => {
      recognition!.onresult?.({ results: { 0: { 0: { transcript: '你好世界' }, length: 1 }, length: 1 } })
    })
    expect(onChange).toHaveBeenCalledWith('已有内容 你好世界')

    fireEvent.click(screen.getByRole('button', { name: '停止语音识别' }))
    expect(recognition!.stop).toHaveBeenCalledTimes(1)
  })
})
